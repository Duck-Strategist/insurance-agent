"""费率表入库与报价接口。"""

import json
import shutil
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, Quote, RateBookRecord
from app.pricing.tables import RATES_DIR
from app.seed import seed_products
from app.services.pricing_service import load_rate_books
from tests.test_auth import bearer, register

XH = "新华人寿E增福优享版"
ZA = "众安尊享e生百万医疗险2026版"
NO_RATES = "达尔文宝贝计划12号"  # 没有费率表

LIFE_PARAMS = {"age": 30, "gender": "male", "pay_term": "3y", "annual_premium": "10000"}


@pytest.fixture
async def products(db_session: AsyncSession) -> dict[str, int]:
    """事务内清空产品（级联清掉费率表和报价），写入种子产品并加载费率表。"""
    await db_session.execute(delete(Product))
    await seed_products(db_session)
    report = await load_rate_books(db_session)
    assert len(report.inserted) == len(list(RATES_DIR.glob("*.json")))
    assert report.unknown_products == []
    rows = await db_session.execute(select(Product.name, Product.id))
    return {name: pid for name, pid in rows.all()}


async def _token(client: AsyncClient, username: str = "alice") -> str:
    resp = await register(client, username=username)
    token: str = resp.json()["access_token"]
    return token


# ---------- 入库 ----------


async def test_load_is_idempotent(db_session: AsyncSession, products: dict[str, int]) -> None:
    report = await load_rate_books(db_session)
    assert report.inserted == []
    assert len(report.unchanged) == len(list(RATES_DIR.glob("*.json")))
    count = len((await db_session.scalars(select(RateBookRecord))).all())
    assert count == len(report.unchanged)


async def test_new_version_keeps_old_one_for_reproducibility(
    db_session: AsyncSession, products: dict[str, int], tmp_path: Path
) -> None:
    src = RATES_DIR / "e-zengfu.json"
    shutil.copy(src, tmp_path / src.name)
    old = await db_session.scalar(
        select(RateBookRecord).where(RateBookRecord.product_id == products[XH])
    )
    assert old is not None

    data = json.loads(src.read_text(encoding="utf-8"))
    data["tables"][0]["items"][0]["value"] = "965.00"
    (tmp_path / src.name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    report = await load_rate_books(db_session, tmp_path)
    assert report.inserted == [XH]

    records = (
        await db_session.scalars(
            select(RateBookRecord)
            .where(RateBookRecord.product_id == products[XH])
            .order_by(RateBookRecord.id)
        )
    ).all()
    assert [r.is_current for r in records] == [False, True]

    # 回退到原来的内容：复用旧版本，不新增行
    shutil.copy(src, tmp_path / src.name)
    await load_rate_books(db_session, tmp_path)
    await db_session.refresh(records[0])
    await db_session.refresh(records[1])
    assert [r.is_current for r in records] == [True, False]


# ---------- 参数与试算 ----------


async def test_pricing_info(client: AsyncClient, products: dict[str, int]) -> None:
    body = (await client.get(f"/api/v1/products/{products[XH]}/pricing")).json()
    assert body["supported"] is True
    names = [p["name"] for p in body["params"]]
    assert names == ["age", "gender", "pay_term", "annual_premium"]
    pay_term = body["params"][2]
    assert pay_term["choices"][0] == {"value": "single", "label": "趸交"}

    unsupported = (await client.get(f"/api/v1/products/{products[NO_RATES]}/pricing")).json()
    assert unsupported == {"product_id": products[NO_RATES], "supported": False, "params": []}

    assert (await client.get("/api/v1/products/999999999/pricing")).status_code == 404


async def test_calculate_ok(client: AsyncClient, products: dict[str, int]) -> None:
    resp = await client.post(f"/api/v1/products/{products[XH]}/quote", json={"params": LIFE_PARAMS})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # 30 岁男 3 年交：每千元年交保费对应 2818 元
    assert body["sum_assured"] == "28180.00"
    assert body["annual_premium"] == "10000.00"
    assert body["total_premium"] == "30000.00"
    assert body["params"]["annual_premium"] == "10000.00"
    assert "第 1 页" in body["lines"][0]["basis"]


async def test_calculate_missing_params(client: AsyncClient, products: dict[str, int]) -> None:
    resp = await client.post(f"/api/v1/products/{products[ZA]}/quote", json={"params": {}})
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "missing_params"
    assert body["missing"] == ["age", "social_security"]
    assert body["annual_premium"] is None


async def test_calculate_unsupported_and_unknown(
    client: AsyncClient, products: dict[str, int]
) -> None:
    resp = await client.post(f"/api/v1/products/{products[NO_RATES]}/quote", json={"params": {}})
    assert resp.status_code == 409
    resp = await client.post("/api/v1/products/999999999/quote", json={"params": {}})
    assert resp.status_code == 404


async def test_calculate_hides_inactive_product(
    client: AsyncClient, db_session: AsyncSession, products: dict[str, int]
) -> None:
    product = await db_session.get(Product, products[XH])
    assert product is not None
    product.status = "inactive"
    await db_session.flush()
    resp = await client.post(f"/api/v1/products/{products[XH]}/quote", json={"params": LIFE_PARAMS})
    assert resp.status_code == 404


# ---------- 保存与列表 ----------


async def test_save_requires_login(client: AsyncClient, products: dict[str, int]) -> None:
    resp = await client.post("/api/v1/quotes", json={"product_id": products[XH], "params": {}})
    assert resp.status_code == 401


async def test_save_and_list_own_quotes(
    client: AsyncClient, db_session: AsyncSession, products: dict[str, int]
) -> None:
    alice = await _token(client, "alice")
    bob = await _token(client, "bob")
    resp = await client.post(
        "/api/v1/quotes",
        json={"product_id": products[XH], "params": LIFE_PARAMS},
        headers=bearer(alice),
    )
    assert resp.status_code == 201
    saved = resp.json()
    assert saved["sum_assured"] == "28180.00" and saved["pay_years"] == 3
    assert saved["lines"][0]["premium"] == "10000.00"

    row = await db_session.get(Quote, saved["id"])
    assert row is not None
    record = await db_session.get(RateBookRecord, row.rate_book_id)
    assert record is not None and record.version == saved["rate_version"]

    mine = (await client.get("/api/v1/quotes", headers=bearer(alice))).json()
    assert [q["id"] for q in mine] == [saved["id"]]
    assert (await client.get("/api/v1/quotes", headers=bearer(bob))).json() == []


async def test_save_rejects_ineligible(client: AsyncClient, products: dict[str, int]) -> None:
    token = await _token(client)
    params = {**LIFE_PARAMS, "age": 65}
    resp = await client.post(
        "/api/v1/quotes", json={"product_id": products[XH], "params": params}, headers=bearer(token)
    )
    assert resp.status_code == 422
    assert "可选：趸交" in resp.json()["detail"]

    resp = await client.post(
        "/api/v1/quotes", json={"product_id": products[XH], "params": {}}, headers=bearer(token)
    )
    assert resp.status_code == 422
    assert "缺少参数" in resp.json()["detail"]
