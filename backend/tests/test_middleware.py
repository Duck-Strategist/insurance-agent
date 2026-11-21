from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core.config import DatabaseSettings, Settings
from app.main import _resolve_request_id, create_app


@pytest.fixture
async def app_and_client() -> AsyncIterator[tuple[FastAPI, AsyncClient]]:
    settings = Settings(
        db=DatabaseSettings(url=SecretStr("postgresql+asyncpg://x:x@127.0.0.1:1/none"))
    )
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("测试用异常")

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client


async def test_unhandled_error_returns_500_with_request_id(
    app_and_client: tuple[FastAPI, AsyncClient],
) -> None:
    _, client = app_and_client
    resp = await client.get("/boom", headers={"x-request-id": "trace-1"})
    assert resp.status_code == 500
    assert resp.headers["x-request-id"] == "trace-1"
    assert resp.json() == {"detail": "服务器内部错误", "request_id": "trace-1"}
    # 不向客户端泄露异常内容
    assert "测试用异常" not in resp.text


async def test_unsafe_request_id_is_replaced(app_and_client: tuple[FastAPI, AsyncClient]) -> None:
    _, client = app_and_client
    resp = await client.get("/health", headers={"x-request-id": "bad id\twith spaces"})
    assert resp.headers["x-request-id"] != "bad id\twith spaces"
    assert len(resp.headers["x-request-id"]) == 32


@pytest.mark.parametrize(
    ("incoming", "kept"),
    [
        ("abc-123", True),
        ("A" * 64, True),
        ("A" * 65, False),
        ("has space", False),
        ("中文", False),
        ("", False),
        (None, False),
    ],
)
def test_resolve_request_id(incoming: str | None, kept: bool) -> None:
    result = _resolve_request_id(incoming)
    assert (result == incoming) is kept
    assert 1 <= len(result) <= 64
