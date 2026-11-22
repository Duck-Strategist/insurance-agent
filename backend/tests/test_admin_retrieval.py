"""管理后台的检索调试与评测记录接口（检索器用假实现，不访问网络）。"""

from collections.abc import AsyncIterator, Sequence

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.main import create_app
from app.models import EvalRun
from app.rag.reranker import RerankError
from app.rag.retriever import Hit, SearchMode, SearchResult
from tests.test_admin import login_as
from tests.test_auth import bearer


class FakeRetriever:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, SearchMode, Sequence[int] | None]] = []

    async def search(
        self, query: str, *, mode: SearchMode, document_ids: Sequence[int] | None = None
    ) -> SearchResult:
        self.calls.append((query, mode, document_ids))
        if self.fail:
            raise RerankError("重排失败 HTTP 500")
        hit = Hit(1, 2, 3, "某条款.pdf", ["第一条 等待期"], "等待期90天", "第一条 等待期\n…", 0.9)
        return SearchResult([hit], {"embed_ms": 1.0, "total_ms": 2.0})


@pytest.fixture
def fake() -> FakeRetriever:
    return FakeRetriever()


@pytest.fixture
async def search_client(
    db_session: AsyncSession, test_settings: object, fake: FakeRetriever
) -> AsyncIterator[AsyncClient]:
    app = create_app(test_settings)  # type: ignore[arg-type]

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_retriever] = lambda: fake
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c


async def test_search_requires_admin(search_client: AsyncClient, db_session: AsyncSession) -> None:
    body = {"query": "等待期"}
    assert (await search_client.post("/api/v1/admin/search", json=body)).status_code == 401
    token = await login_as(search_client, db_session, "plain_user", admin=False)
    resp = await search_client.post("/api/v1/admin/search", json=body, headers=bearer(token))
    assert resp.status_code == 403


async def test_search_returns_hits_and_passes_document_filter(
    search_client: AsyncClient, db_session: AsyncSession, fake: FakeRetriever
) -> None:
    token = await login_as(search_client, db_session, "search_admin", admin=True)
    resp = await search_client.post(
        "/api/v1/admin/search",
        json={"query": "等待期多久", "mode": "hybrid", "document_id": 7},
        headers=bearer(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["hits"][0]["doc_title"] == "某条款"
    assert body["hits"][0]["section_path"] == ["第一条 等待期"]
    assert body["timings"]["total_ms"] == 2.0
    assert fake.calls == [("等待期多久", SearchMode.HYBRID, [7])]

    # 不传 document_id = 不限条款（None），不能变成空列表
    await search_client.post("/api/v1/admin/search", json={"query": "x"}, headers=bearer(token))
    assert fake.calls[-1] == ("x", SearchMode.HYBRID_RERANK, None)


async def test_search_validates_input(search_client: AsyncClient, db_session: AsyncSession) -> None:
    token = await login_as(search_client, db_session, "search_admin2", admin=True)
    for body in ({"query": ""}, {"query": "x" * 201}, {"query": "x", "mode": "bm25"}):
        resp = await search_client.post("/api/v1/admin/search", json=body, headers=bearer(token))
        assert resp.status_code == 422, body


async def test_search_upstream_failure_is_502(
    search_client: AsyncClient, db_session: AsyncSession, fake: FakeRetriever
) -> None:
    fake.fail = True
    token = await login_as(search_client, db_session, "search_admin3", admin=True)
    resp = await search_client.post(
        "/api/v1/admin/search", json={"query": "x"}, headers=bearer(token)
    )
    assert resp.status_code == 502
    assert "重排失败" in resp.json()["detail"]


async def test_search_unconfigured_is_503(client: AsyncClient, db_session: AsyncSession) -> None:
    # conftest 清空了 DashScope Key：应用启动时不创建检索器
    token = await login_as(client, db_session, "search_admin4", admin=True)
    resp = await client.post("/api/v1/admin/search", json={"query": "x"}, headers=bearer(token))
    assert resp.status_code == 503
    assert "RAG__DASHSCOPE_API_KEY" in resp.json()["detail"]


async def test_eval_runs_list_newest_first_without_details(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await db_session.execute(delete(EvalRun))
    for suite, recall in (("retrieval", 0.8), ("route", 0.9), ("retrieval", 0.95)):
        db_session.add(
            EvalRun(
                suite=suite,
                dataset="d.jsonl",
                dataset_hash="h",
                config={"mode": "vector"},
                metrics={"recall@5": recall},
                details=[{"id": "q1", "rank": 1}],
            )
        )
    await db_session.flush()
    token = await login_as(client, db_session, "eval_admin", admin=True)

    resp = await client.get("/api/v1/admin/eval-runs?suite=retrieval", headers=bearer(token))
    assert resp.status_code == 200
    runs = resp.json()
    assert [r["metrics"]["recall@5"] for r in runs] == [0.95, 0.8]
    assert all("details" not in r for r in runs)

    user = await login_as(client, db_session, "eval_user", admin=False)
    assert (await client.get("/api/v1/admin/eval-runs", headers=bearer(user))).status_code == 403
