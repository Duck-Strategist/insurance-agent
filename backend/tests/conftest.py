"""数据库相关测试的公共夹具。

每个测试在一个外层事务里运行，结束时整体回滚：
- 业务代码里的 commit 只会提交到 SAVEPOINT（join_transaction_mode="create_savepoint"）；
- 测试之间互不影响，也不会弄脏开发库。
数据库不可用或未执行迁移时，依赖这些夹具的测试自动跳过。
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.api.deps import get_session
from app.core.config import AuthSettings, Settings, get_settings
from app.main import create_app

TEST_JWT_SECRET = "test-jwt-secret-with-at-least-32-bytes"


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(get_settings().db.url.get_secret_value())
    try:
        async with eng.connect() as conn:
            has_tables = await conn.scalar(text("SELECT to_regclass('public.users') IS NOT NULL"))
    except Exception:
        await eng.dispose()
        pytest.skip("数据库不可用，跳过数据库测试")
    if not has_tables:
        await eng.dispose()
        pytest.skip("数据库未迁移，请先执行 alembic upgrade head")
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture
def test_settings() -> Settings:
    return Settings(auth=AuthSettings(jwt_secret=TEST_JWT_SECRET))  # type: ignore[arg-type]


@pytest.fixture
async def client(db_session: AsyncSession, test_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(test_settings)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
