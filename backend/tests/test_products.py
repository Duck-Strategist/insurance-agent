from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product
from app.seed import seed_products


@pytest.fixture
async def products(db_session: AsyncSession) -> list[Product]:
    """清空（事务内，结束回滚）后放入 5 个已知产品：3 个医疗险（1 个下架）、2 个寿险。"""
    await db_session.execute(delete(Product))
    rows = [
        Product(name="医疗A", category="medical", insurer="甲", min_premium=Decimal("100.00")),
        Product(name="医疗B", category="medical", insurer="甲", min_premium=Decimal("200.50")),
        Product(
            name="医疗下架", category="medical", insurer="甲", min_premium=1, status="inactive"
        ),
        Product(
            name="寿险A",
            category="life",
            insurer="乙",
            min_premium=Decimal("10000"),
            max_premium=None,
            highlights=["终身", "分红"],
        ),
        Product(name="寿险B", category="life", insurer="乙", min_premium=Decimal("5000")),
    ]
    db_session.add_all(rows)
    await db_session.flush()
    return rows


async def test_list_hides_inactive_and_orders_by_id(
    client: AsyncClient, products: list[Product]
) -> None:
    resp = await client.get("/api/v1/products")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    assert [p["name"] for p in body["items"]] == ["医疗A", "医疗B", "寿险A", "寿险B"]
    assert body["page"] == 1 and body["page_size"] == 12


async def test_list_filters_by_category(client: AsyncClient, products: list[Product]) -> None:
    body = (await client.get("/api/v1/products", params={"category": "life"})).json()
    assert body["total"] == 2
    assert {p["category"] for p in body["items"]} == {"life"}


async def test_pagination(client: AsyncClient, products: list[Product]) -> None:
    page2 = (await client.get("/api/v1/products", params={"page": 2, "page_size": 3})).json()
    assert page2["total"] == 4
    assert [p["name"] for p in page2["items"]] == ["寿险B"]

    beyond = (await client.get("/api/v1/products", params={"page": 9, "page_size": 3})).json()
    assert beyond["items"] == [] and beyond["total"] == 4


@pytest.mark.parametrize(
    "params",
    [{"category": "car"}, {"page": 0}, {"page_size": 0}, {"page_size": 51}],
)
async def test_list_rejects_bad_params(
    client: AsyncClient, products: list[Product], params: dict[str, object]
) -> None:
    resp = await client.get("/api/v1/products", params=params)
    assert resp.status_code == 422


async def test_detail_serializes_money_and_nulls(
    client: AsyncClient, products: list[Product]
) -> None:
    life = products[3]
    body = (await client.get(f"/api/v1/products/{life.id}")).json()
    assert body["name"] == "寿险A"
    # 金额以字符串返回，避免浮点误差
    assert body["min_premium"] == "10000.00"
    assert body["max_premium"] is None
    assert body["highlights"] == ["终身", "分红"]


async def test_detail_404_for_missing_or_inactive(
    client: AsyncClient, products: list[Product]
) -> None:
    inactive = products[2]
    assert (await client.get(f"/api/v1/products/{inactive.id}")).status_code == 404
    assert (await client.get("/api/v1/products/999999999")).status_code == 404


async def test_seed_is_idempotent(db_session: AsyncSession) -> None:
    await db_session.execute(delete(Product))
    assert await seed_products(db_session) == 12
    assert await seed_products(db_session) == 12
    count = await db_session.scalar(select(func.count()).select_from(Product))
    assert count == 12
