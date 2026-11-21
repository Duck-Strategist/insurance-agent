from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.v1 import admin as admin_module
from app.core.config import AuthSettings, RagSettings, Settings
from app.main import create_app
from app.models import Document, User
from app.rag.pipeline import IngestResult
from tests.conftest import TEST_JWT_SECRET
from tests.test_auth import bearer, register

PDF = b"%PDF-1.7 fake"


async def login_as(client: AsyncClient, db: AsyncSession, username: str, *, admin: bool) -> str:
    token: str = (await register(client, username=username)).json()["access_token"]
    if admin:
        await db.execute(update(User).where(User.username == username).values(role="admin"))
    return token


class RecordingPipeline:
    def __init__(self) -> None:
        self.ingested: list[tuple[str, bool]] = []

    async def ingest(self, path: Path, *, force: bool = False) -> IngestResult:
        self.ingested.append((path.name, force))
        return IngestResult(path.name, "indexed")


@pytest.fixture(autouse=True)
async def empty_documents(db_session: AsyncSession) -> None:
    """不依赖开发库里已有的文档：在测试事务内清空（结束后回滚，不影响真实数据）。"""
    await db_session.execute(delete(Document))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> RecordingPipeline:
    """替换真实入库（会调 MinerU/DashScope 并写库）为只记录调用的假实现。"""
    rec = RecordingPipeline()

    @asynccontextmanager
    async def fake_pipeline(rag: RagSettings, session_factory: object) -> AsyncIterator[object]:
        yield rec

    monkeypatch.setattr(admin_module, "ingestion_pipeline", fake_pipeline)
    return rec


@pytest.fixture
async def rag_client(db_session: AsyncSession, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """配置了（假的）RAG 密钥、数据目录指向临时目录的客户端。"""
    settings = Settings(
        auth=AuthSettings(jwt_secret=TEST_JWT_SECRET),  # type: ignore[arg-type]
        rag=RagSettings(
            data_dir=tmp_path,
            mineru_token="fake",  # type: ignore[arg-type]
            dashscope_api_key="fake",  # type: ignore[arg-type]
        ),
    )
    app = create_app(settings)

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[deps.get_session] = _session
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://t") as c,
    ):
        yield c


async def test_admin_endpoints_require_admin_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    assert (await client.get("/api/v1/admin/documents")).status_code == 401
    user_token = await login_as(client, db_session, "normal", admin=False)
    resp = await client.get("/api/v1/admin/documents", headers=bearer(user_token))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "需要管理员权限"


async def test_list_documents_links_shared_clause_to_products(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.seed import seed_products

    await seed_products(db_session)
    shared = "复星联合优选二号长期住院医疗险(星相守2号)条款.pdf"
    db_session.add(Document(file_name=shared, file_hash="h", status="indexed", child_count=9))
    await db_session.flush()

    token = await login_as(client, db_session, "boss", admin=True)
    docs = (await client.get("/api/v1/admin/documents", headers=bearer(token))).json()
    doc = next(d for d in docs if d["file_name"] == shared)
    assert doc["status"] == "indexed" and doc["processing"] is False
    # 个人版和转保版共用这份条款
    assert sorted(p["name"] for p in doc["products"]) == [
        "复星联合星相守2号长期医疗险（个人版）",
        "复星联合星相守2号长期医疗险（转保版）",
    ]


async def test_upload_without_rag_config_is_503(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await login_as(client, db_session, "boss", admin=True)
    resp = await client.post(
        "/api/v1/admin/documents",
        headers=bearer(token),
        files={"file": ("a.pdf", PDF, "application/pdf")},
    )
    assert resp.status_code == 503
    assert "RAG__MINERU_TOKEN" in resp.json()["detail"]


async def test_upload_saves_registers_and_schedules_ingest(
    rag_client: AsyncClient, db_session: AsyncSession, tmp_path: Path, recorder: RecordingPipeline
) -> None:
    token = await login_as(rag_client, db_session, "boss", admin=True)
    resp = await rag_client.post(
        "/api/v1/admin/documents",
        headers=bearer(token),
        files={"file": ("新条款.pdf", PDF, "application/pdf")},
    )
    assert resp.status_code == 202
    assert resp.json()["file_name"] == "新条款.pdf"
    assert resp.json()["status"] == "pending"
    assert (tmp_path / "raw" / "clauses" / "新条款.pdf").read_bytes() == PDF
    # 后台任务已执行，且结束后不再标记为处理中
    assert recorder.ingested == [("新条款.pdf", True)]
    docs = (await rag_client.get("/api/v1/admin/documents", headers=bearer(token))).json()
    assert next(d for d in docs if d["file_name"] == "新条款.pdf")["processing"] is False


@pytest.mark.parametrize(
    ("filename", "content", "status"),
    [
        ("notes.txt", PDF, 422),
        ("fake.pdf", b"not a pdf at all", 422),
        (".pdf", PDF, 422),
    ],
)
async def test_upload_validation(
    rag_client: AsyncClient,
    db_session: AsyncSession,
    recorder: RecordingPipeline,
    filename: str,
    content: bytes,
    status: int,
) -> None:
    token = await login_as(rag_client, db_session, "boss", admin=True)
    resp = await rag_client.post(
        "/api/v1/admin/documents",
        headers=bearer(token),
        files={"file": (filename, content, "application/pdf")},
    )
    assert resp.status_code == status
    assert recorder.ingested == []


async def test_upload_strips_path_traversal(
    rag_client: AsyncClient, db_session: AsyncSession, tmp_path: Path, recorder: RecordingPipeline
) -> None:
    token = await login_as(rag_client, db_session, "boss", admin=True)
    resp = await rag_client.post(
        "/api/v1/admin/documents",
        headers=bearer(token),
        files={"file": ("../../evil.pdf", PDF, "application/pdf")},
    )
    assert resp.status_code == 202
    assert resp.json()["file_name"] == "evil.pdf"
    assert (tmp_path / "raw" / "clauses" / "evil.pdf").exists()
    assert not (tmp_path.parent / "evil.pdf").exists()


async def test_reupload_with_new_content_resets_status(
    rag_client: AsyncClient, db_session: AsyncSession, recorder: RecordingPipeline
) -> None:
    db_session.add(Document(file_name="旧.pdf", file_hash="old", status="indexed"))
    await db_session.flush()
    token = await login_as(rag_client, db_session, "boss", admin=True)
    resp = await rag_client.post(
        "/api/v1/admin/documents",
        headers=bearer(token),
        files={"file": ("旧.pdf", PDF, "application/pdf")},
    )
    assert resp.json()["status"] == "pending"
    doc = await db_session.scalar(select(Document).where(Document.file_name == "旧.pdf"))
    assert doc is not None and doc.file_hash != "old"


async def test_reindex(
    rag_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
    recorder: RecordingPipeline,
) -> None:
    token = await login_as(rag_client, db_session, "boss", admin=True)
    missing = await rag_client.post("/api/v1/admin/documents/999999/reindex", headers=bearer(token))
    assert missing.status_code == 404

    doc = Document(file_name="已有.pdf", file_hash="h")
    db_session.add(doc)
    await db_session.flush()
    no_file = await rag_client.post(
        f"/api/v1/admin/documents/{doc.id}/reindex", headers=bearer(token)
    )
    assert no_file.status_code == 404
    assert "原始 PDF 不存在" in no_file.json()["detail"]

    clauses = tmp_path / "raw" / "clauses"
    clauses.mkdir(parents=True)
    (clauses / "已有.pdf").write_bytes(PDF)  # noqa: ASYNC240
    ok = await rag_client.post(f"/api/v1/admin/documents/{doc.id}/reindex", headers=bearer(token))
    assert ok.status_code == 202
    assert recorder.ingested == [("已有.pdf", True)]
