from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core.config import DatabaseSettings, Settings
from app.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # 指向一个不存在的库：存活检查不应依赖数据库，就绪检查应返回 503
    settings = Settings(
        db=DatabaseSettings(url=SecretStr("postgresql+asyncpg://x:x@127.0.0.1:1/none"))
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_liveness_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.headers["x-request-id"]


async def test_readiness_reports_db_down(client: AsyncClient) -> None:
    resp = await client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "database": "unavailable"}


async def test_request_id_is_propagated(client: AsyncClient) -> None:
    resp = await client.get("/health", headers={"x-request-id": "abc123"})
    assert resp.headers["x-request-id"] == "abc123"
