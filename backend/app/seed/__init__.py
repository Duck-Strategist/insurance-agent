"""演示数据：按产品名幂等写入，重复执行只会更新，不会重复插入。

用法：uv run python -m app.seed
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.infra.db import Database
from app.models import Product

logger = get_logger(__name__)

PRODUCTS_FILE = Path(__file__).with_name("products.json")


def load_products() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
    for row in rows:
        row["min_premium"] = Decimal(row["min_premium"])
        if row["max_premium"] is not None:
            row["max_premium"] = Decimal(row["max_premium"])
    return rows


async def seed_products(session: AsyncSession) -> int:
    rows = load_products()
    stmt = insert(Product).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Product.name],
        set_={
            col: stmt.excluded[col]
            for col in (
                "category",
                "insurer",
                "description",
                "target_group",
                "highlights",
                "min_premium",
                "max_premium",
                "clause_file",
            )
        },
    )
    await session.execute(stmt)
    await session.commit()
    return len(rows)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log.level, settings.log.json_format)
    db = Database(settings.db)
    try:
        async with db.session_factory() as session:
            count = await seed_products(session)
        logger.info("seed.products", count=count)
    finally:
        await db.dispose()
