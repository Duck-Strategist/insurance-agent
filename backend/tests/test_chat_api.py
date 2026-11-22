"""对话接口：会话 CRUD、按用户隔离、SSE 流式问答（对话图用假 LLM，不访问网络）。"""

import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import prompts
from app.agent.runtime import AgentRuntime
from app.api import deps
from app.api.v1 import chat as chat_module
from app.core.config import Settings
from app.main import create_app
from app.models import Document, Product
from tests.conftest import serial_sessionmaker
from tests.test_agent import make_runtime, route
from tests.test_auth import bearer, register


class Box:
    """测试里可替换的对话运行时（不同测试要不同的 LLM 脚本）。"""

    runtime: AgentRuntime | None = None


@pytest.fixture
def box() -> Box:
    return Box()


@pytest.fixture
def factory(db_session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    # 对话图里查产品目录用同一个连接，能看到测试事务里插入的数据
    return serial_sessionmaker(db_session.bind)  # type: ignore[arg-type]


@pytest.fixture
async def api(
    db_session: AsyncSession, test_settings: Settings, box: Box
) -> AsyncIterator[AsyncClient]:
    app = create_app(test_settings)

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _agent() -> AgentRuntime:
        if box.runtime is None:
            raise HTTPException(503, detail="对话服务未配置")
        return box.runtime

    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_agent] = _agent
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c


async def token_for(api: AsyncClient, name: str) -> dict[str, str]:
    return bearer((await register(api, username=name)).json()["access_token"])


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.strip().split("\n\n"):
        head, data = block.split("\n")
        events.append((head.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


async def test_chat_requires_login(api: AsyncClient) -> None:
    assert (await api.get("/api/v1/threads")).status_code == 401
    assert (await api.post("/api/v1/chat", json={"message": "你好"})).status_code == 401


async def test_chat_returns_503_when_agent_not_configured(api: AsyncClient) -> None:
    h = await token_for(api, "chat_no_agent")
    resp = await api.post("/api/v1/chat", json={"message": "你好"}, headers=h)
    assert resp.status_code == 503


async def test_new_chat_creates_thread_streams_and_persists(
    api: AsyncClient, box: Box, factory: async_sessionmaker[AsyncSession]
) -> None:
    box.runtime, *_ = make_runtime(factory, json_replies=[])
    h = await token_for(api, "chat_alice")

    resp = await api.post("/api/v1/chat", json={"message": "  你好  "}, headers=h)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    assert events[0][0] == "thread"
    thread_id = events[0][1]["id"]
    assert events[0][1]["title"] == "你好"  # 首条消息（去掉空白）作标题
    assert events[-1] == ("done", {})

    threads = (await api.get("/api/v1/threads", headers=h)).json()
    assert [t["id"] for t in threads] == [thread_id]

    msgs = (await api.get(f"/api/v1/threads/{thread_id}/messages", headers=h)).json()
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "你好"),
        ("assistant", prompts.GREETING),
    ]


async def test_continue_thread_and_citations_in_history(
    api: AsyncClient, box: Box, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with factory() as s, s.begin():
        s.add(Document(file_name="接口测试条款.pdf", file_hash="h", indexed_hash="h"))
        p = Product(
            name="接口测试险",
            category="medical",
            insurer="测试",
            min_premium=Decimal("1"),
            clause_file="接口测试条款.pdf",
        )
        s.add(p)
        await s.flush()
        pid = p.id
    box.runtime, *_ = make_runtime(
        factory,
        json_replies=[
            json.dumps({"intent": "chitchat", "product_ids": []}),
            route("clause_qa", [pid]),
            json.dumps({"query": "等待期"}),
        ],
        answer_replies=["等待期 90 天[1]"],
    )
    h = await token_for(api, "chat_bob")
    first = parse_sse((await api.post("/api/v1/chat", json={"message": "嗯"}, headers=h)).text)
    tid = first[0][1]["id"]
    second = parse_sse(
        (
            await api.post(
                "/api/v1/chat", json={"thread_id": tid, "message": "接口测试险等待期"}, headers=h
            )
        ).text
    )
    assert second[0] == ("thread", {"id": tid, "title": "嗯"})  # 续聊不改标题
    msgs = (await api.get(f"/api/v1/threads/{tid}/messages", headers=h)).json()
    assert msgs[-1]["citations"][0]["index"] == 1
    assert msgs[-1]["citations"][0]["section_path"] == ["第三条 等待期"]


async def test_threads_are_isolated_between_users(
    api: AsyncClient, box: Box, factory: async_sessionmaker[AsyncSession]
) -> None:
    box.runtime, *_ = make_runtime(factory, json_replies=[])
    alice = await token_for(api, "iso_alice")
    bob = await token_for(api, "iso_bob")
    tid = (await api.post("/api/v1/threads", json={}, headers=alice)).json()["id"]

    # 别人的会话一律 404（不暴露是否存在）
    assert (await api.get(f"/api/v1/threads/{tid}/messages", headers=bob)).status_code == 404
    assert (
        await api.patch(f"/api/v1/threads/{tid}", json={"title": "x"}, headers=bob)
    ).status_code == 404
    assert (await api.delete(f"/api/v1/threads/{tid}", headers=bob)).status_code == 404
    resp = await api.post("/api/v1/chat", json={"thread_id": tid, "message": "你好"}, headers=bob)
    assert resp.status_code == 404
    assert (await api.get("/api/v1/threads", headers=bob)).json() == []


async def test_rename_and_delete_thread_removes_messages(
    api: AsyncClient, box: Box, factory: async_sessionmaker[AsyncSession]
) -> None:
    rt, *_ = make_runtime(factory, json_replies=[])
    box.runtime = rt
    h = await token_for(api, "chat_carol")
    tid = parse_sse((await api.post("/api/v1/chat", json={"message": "你好"}, headers=h)).text)[0][
        1
    ]["id"]

    resp = await api.patch(f"/api/v1/threads/{tid}", json={"title": "等待期问题"}, headers=h)
    assert resp.json()["title"] == "等待期问题"

    assert (await api.delete(f"/api/v1/threads/{tid}", headers=h)).status_code == 204
    assert (await api.get(f"/api/v1/threads/{tid}/messages", headers=h)).status_code == 404
    # checkpoint 里的消息也删掉了
    state = await rt.graph.aget_state({"configurable": {"thread_id": tid}})
    assert not state.values


async def test_busy_thread_rejects_second_message(
    api: AsyncClient, box: Box, factory: async_sessionmaker[AsyncSession]
) -> None:
    box.runtime, *_ = make_runtime(factory, json_replies=[])
    h = await token_for(api, "chat_dave")
    tid = (await api.post("/api/v1/threads", json={}, headers=h)).json()["id"]
    api_app = api._transport.app  # type: ignore[attr-defined]
    chat_module._busy(_FakeRequest(api_app)).add(tid)
    resp = await api.post("/api/v1/chat", json={"thread_id": tid, "message": "你好"}, headers=h)
    assert resp.status_code == 409
    assert (await api.delete(f"/api/v1/threads/{tid}", headers=h)).status_code == 409


class _FakeRequest:
    def __init__(self, app: Any) -> None:
        self.app = app


async def test_message_validation(api: AsyncClient, box: Box, factory: Any) -> None:
    box.runtime, *_ = make_runtime(factory, json_replies=[])
    h = await token_for(api, "chat_erin")
    for body in (
        {"message": "   "},
        {"message": "x" * 1001},
        {"message": "hi", "thread_id": "x" * 37},
    ):
        assert (await api.post("/api/v1/chat", json=body, headers=h)).status_code == 422, body
