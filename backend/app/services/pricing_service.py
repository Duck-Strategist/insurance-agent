"""报价业务：费率表入库、按当前版本报价、保存与查询报价记录。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Product, ProductStatus, Quote, RateBookRecord, User
from app.pricing.engine import ParamSpec, QuoteResult, jsonable_params, rule_for
from app.pricing.tables import RATES_DIR, RateBook, book_from_json, content_version

logger = get_logger(__name__)


@dataclass
class LoadReport:
    inserted: list[str]
    unchanged: list[str]
    unknown_products: list[str]


def _read_rate_files(rates_dir: Path) -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted(rates_dir.glob("*.json"))]


async def load_rate_books(session: AsyncSession, rates_dir: Path = RATES_DIR) -> LoadReport:
    """把 `app/pricing/rates/*.json` 写入 rate_books。同一内容重复执行不会产生新版本。

    新版本写入后成为当前版本，旧版本保留（旧报价仍引用它）。全部产品在一个事务里完成。
    """
    texts = await anyio.to_thread.run_sync(_read_rate_files, rates_dir)
    report = LoadReport([], [], [])
    for text in texts:
        data = json.loads(text)
        version = content_version(text)
        product_id = await session.scalar(select(Product.id).where(Product.name == data["product"]))
        if product_id is None:
            report.unknown_products.append(data["product"])
            continue
        # 锁住产品行：两个 load 同时跑时不会都把自己设成当前版本
        await session.execute(select(Product.id).where(Product.id == product_id).with_for_update())
        existing = await session.scalar(
            select(RateBookRecord).where(
                RateBookRecord.product_id == product_id, RateBookRecord.version == version
            )
        )
        if existing is not None and existing.is_current:
            report.unchanged.append(data["product"])
            continue
        await session.execute(
            update(RateBookRecord)
            .where(RateBookRecord.product_id == product_id, RateBookRecord.is_current)
            .values(is_current=False)
        )
        if existing is not None:
            existing.is_current = True  # 回退到以前加载过的版本
        else:
            session.add(
                RateBookRecord(
                    product_id=product_id,
                    version=version,
                    source_file=data["source"]["file"],
                    source_sha256=data["source"]["sha256"],
                    content=data,
                    is_current=True,
                )
            )
        await session.flush()
        report.inserted.append(data["product"])
    await session.commit()
    return report


def book_of(record: RateBookRecord) -> RateBook:
    return book_from_json(record.content, version=record.version)


class PricingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _active_product(self, product_id: int) -> Product | None:
        product = await self.session.get(Product, product_id)
        if product is None or product.status != ProductStatus.ACTIVE:
            return None
        return product

    async def current_book(self, product: Product) -> tuple[RateBookRecord, RateBook] | None:
        """产品没有报价规则或费率表未入库时返回 None。"""
        if rule_for(product.name) is None:
            return None
        record = await self.session.scalar(
            select(RateBookRecord).where(
                RateBookRecord.product_id == product.id, RateBookRecord.is_current
            )
        )
        if record is None:
            return None
        return record, book_of(record)

    async def param_specs(self, product_id: int) -> tuple[Product, list[ParamSpec] | None] | None:
        """返回 (产品, 参数说明)；产品不存在返回 None，暂不支持报价时参数说明为 None。"""
        product = await self._active_product(product_id)
        if product is None:
            return None
        loaded = await self.current_book(product)
        if loaded is None:
            return product, None
        rule = rule_for(product.name)
        assert rule is not None
        return product, rule.param_specs(loaded[1])

    async def quote(
        self, product_id: int, params: Mapping[str, Any]
    ) -> tuple[Product, RateBookRecord, QuoteResult] | tuple[Product, None, None] | None:
        product = await self._active_product(product_id)
        if product is None:
            return None
        loaded = await self.current_book(product)
        if loaded is None:
            return product, None, None
        record, book = loaded
        rule = rule_for(product.name)
        assert rule is not None
        return product, record, rule.quote(book, params)

    async def save(
        self, user: User, product: Product, record: RateBookRecord, result: QuoteResult
    ) -> Quote:
        assert result.status == "ok" and result.annual_premium is not None
        assert result.total_premium is not None and result.pay_years is not None
        row = Quote(
            user_id=user.id,
            product_id=product.id,
            rate_book_id=record.id,
            params=jsonable_params(result.params),
            breakdown=[
                {
                    "code": line.code,
                    "title": line.title,
                    "premium": str(line.premium),
                    "basis": line.basis,
                    "page": line.page,
                }
                for line in result.lines
            ],
            annual_premium=result.annual_premium,
            total_premium=result.total_premium,
            pay_years=result.pay_years,
            sum_assured=result.sum_assured,
        )
        self.session.add(row)
        await self.session.commit()
        logger.info("quote.saved", quote_id=row.id, product_id=product.id, version=record.version)
        return row

    async def list_quotes(self, user: User, limit: int = 50) -> Sequence[tuple[Quote, str, str]]:
        """当前用户的报价记录（新的在前），附产品名与费率版本。"""
        rows = await self.session.execute(
            select(Quote, Product.name, RateBookRecord.version)
            .join(Product, Product.id == Quote.product_id)
            .join(RateBookRecord, RateBookRecord.id == Quote.rate_book_id)
            .where(Quote.user_id == user.id)
            .order_by(Quote.created_at.desc(), Quote.id.desc())
            .limit(limit)
        )
        return [(q, name, version) for q, name, version in rows.all()]
