"""演示数据：按产品名幂等写入，重复执行不会重复插入。

用法：
  uv run python -m app.seed              只补缺失的产品（容器启动时执行）
  uv run python -m app.seed --overwrite  用种子文件覆盖同名产品
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func
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


SEED_COLUMNS = (
    "category",
    "insurer",
    "description",
    "target_group",
    "highlights",
    "min_premium",
    "max_premium",
    "clause_file",
)


async def seed_products(session: AsyncSession, *, overwrite: bool = False) -> int:
    """写入演示产品，返回实际插入或更新的行数。

    默认只补缺失的产品，不改已有数据（容器每次启动都会执行，不能覆盖后台改过的内容）；
    overwrite=True 时用种子文件覆盖同名产品，并刷新 updated_at。
    """
    rows = load_products()
    stmt = insert(Product).values(rows)
    if overwrite:
        stmt = stmt.on_conflict_do_update(
            index_elements=[Product.name],
            set_={
                **{col: stmt.excluded[col] for col in SEED_COLUMNS},
                # ORM 的 onupdate 对 INSERT ... ON CONFLICT 不生效，需要显式设置
                "updated_at": func.now(),
            },
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=[Product.name])
    result = await session.execute(stmt.returning(Product.id))
    affected = len(result.all())
    await session.commit()
    return affected


async def main(overwrite: bool = False) -> None:
    settings = get_settings()
    configure_logging(settings.log.level, settings.log.json_format)
    db = Database(settings.db)
    try:
        async with db.session_factory() as session:
            count = await seed_products(session, overwrite=overwrite)
        logger.info("seed.products", affected=count, overwrite=overwrite)
    finally:
        await db.dispose()
