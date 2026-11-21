"""需要真实数据库的集成测试：连不上时自动跳过（CI 中会提供 pgvector 服务）。"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.infra.db import Database


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    database = Database(get_settings().db)
    if not await database.ping():
        await database.dispose()
        pytest.skip("数据库不可用，跳过集成测试")
    yield database
    await database.dispose()


async def test_pgvector_extension_installed(db: Database) -> None:
    async with db.engine.connect() as conn:
        version = await conn.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
    assert version is not None, "未启用 pgvector，请先执行 alembic upgrade head"


async def test_vector_distance_query(db: Database) -> None:
    async with db.engine.connect() as conn:
        distance = await conn.scalar(text("SELECT '[1,0]'::vector <=> '[0,1]'::vector"))
    assert distance == pytest.approx(1.0)
