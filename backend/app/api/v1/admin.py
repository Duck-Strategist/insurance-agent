"""管理后台：条款文档的上传、入库状态与重新入库；检索调试与评测记录。

入库在 FastAPI BackgroundTasks 中执行（同进程、响应返回后开始）。对演示项目足够；
多实例部署时需要换成任务队列，且 processing 状态要放到数据库或 Redis 里。
"""

import hashlib
from pathlib import PurePath
from typing import Annotated

import anyio
from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)

from app.api.deps import AdminUser, RetrieverDep, SessionDep, SettingsDep
from app.core.config import RagSettings
from app.core.logging import get_logger
from app.rag.embedder import EmbeddingError
from app.rag.factory import RagNotConfiguredError, check_configured, ingestion_pipeline
from app.rag.reranker import RerankError
from app.rag.retriever import Retriever
from app.schemas.document import DocumentOut, ParentPage
from app.schemas.retrieval import EvalRunOut, SearchHitOut, SearchRequest, SearchResponse
from app.services.document_service import DocumentService
from app.services.eval_service import EvalService

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def _processing(request: Request) -> set[str]:
    state = request.app.state
    if not hasattr(state, "ingesting"):
        state.ingesting = set()
    ingesting: set[str] = state.ingesting
    return ingesting


def _require_rag(rag: RagSettings) -> None:
    try:
        check_configured(rag)
    except RagNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None


def _schedule_ingest(
    request: Request, tasks: BackgroundTasks, rag: RagSettings, file_name: str
) -> None:
    processing = _processing(request)
    if file_name in processing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该文档正在入库，请稍后")
    processing.add(file_name)
    session_factory = request.app.state.db.session_factory
    retriever: Retriever | None = getattr(request.app.state, "retriever", None)

    async def run() -> None:
        try:
            async with ingestion_pipeline(rag, session_factory) as pipeline:
                result = await pipeline.ingest(rag.clauses_dir / file_name, force=True)
            if result.outcome == "indexed" and retriever is not None:
                # 新条款的词要参与全文检索的 IDF 统计，不等缓存过期
                retriever.invalidate_term_stats()
        except Exception:
            # pipeline 内部已把文档标为 failed；这里兜底记录组装阶段的异常
            logger.exception("admin.ingest_crashed", file=file_name)
        finally:
            processing.discard(file_name)

    tasks.add_task(run)


@router.get("/documents", summary="条款文档与入库状态")
async def list_documents(_: AdminUser, request: Request, session: SessionDep) -> list[DocumentOut]:
    return await DocumentService(session).list_documents(_processing(request))


@router.post(
    "/documents/{doc_id}/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    summary="重新入库（后台执行）",
)
async def reindex(
    doc_id: int,
    _: AdminUser,
    request: Request,
    tasks: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, str]:
    _require_rag(settings.rag)
    doc = await DocumentService(session).get(doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    if not await anyio.Path(settings.rag.clauses_dir / doc.file_name).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="原始 PDF 不存在，请重新上传")
    _schedule_ingest(request, tasks, settings.rag, doc.file_name)
    return {"status": "accepted"}


@router.post(
    "/documents",
    status_code=status.HTTP_202_ACCEPTED,
    summary="上传条款 PDF 并入库（后台执行）",
)
async def upload_document(
    _: AdminUser,
    request: Request,
    tasks: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="条款 PDF，最大 30MB")],
) -> DocumentOut:
    _require_rag(settings.rag)
    # 只取文件名本身，防止 ../ 之类的路径穿越
    name = PurePath(file.filename or "").name
    path = PurePath(name)
    # 必须是 .pdf（不区分大小写），且文件名主体非空、不以点开头（否则会存成隐藏文件）
    if path.suffix.lower() != ".pdf" or not path.stem.strip() or name.startswith("."):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="只支持 PDF 文件")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件超过 30MB")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="文件内容不是 PDF")
    if name in _processing(request):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该文档正在入库，请稍后")

    target = anyio.Path(settings.rag.clauses_dir / name)
    await target.parent.mkdir(parents=True, exist_ok=True)
    await target.write_bytes(content)

    service = DocumentService(session)
    doc = await service.register_upload(name, hashlib.sha256(content).hexdigest())
    _schedule_ingest(request, tasks, settings.rag, name)
    docs = await service.list_documents(_processing(request))
    return next(d for d in docs if d.id == doc.id)


@router.get("/documents/{doc_id}/parents", summary="查看父块切分结果（调试用）")
async def list_parents(
    doc_id: int,
    _: AdminUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ParentPage:
    service = DocumentService(session)
    if await service.get(doc_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    items, total = await service.list_parents(doc_id, limit, offset)
    return ParentPage(items=items, total=total, offset=offset, limit=limit)


@router.post("/search", summary="检索调试：查看三种检索配置的结果与耗时")
async def search(_: AdminUser, body: SearchRequest, retriever: RetrieverDep) -> SearchResponse:
    try:
        result = await retriever.search(
            body.query,
            mode=body.mode,
            document_ids=[body.document_id] if body.document_id is not None else None,
        )
    except (EmbeddingError, RerankError) as exc:
        # 外部服务故障（含 Key 失效）：返回 502，让前端提示，而不是 500
        logger.warning("admin.search_failed", error=str(exc))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"检索服务调用失败：{exc}"
        ) from None
    return SearchResponse(
        hits=[
            SearchHitOut(
                parent_id=h.parent_id,
                child_id=h.child_id,
                document_id=h.document_id,
                doc_title=h.doc_title,
                section_path=h.section_path,
                snippet=h.snippet,
                parent_content=h.parent_content,
                score=h.score,
            )
            for h in result.hits
        ],
        timings=result.timings,
    )


@router.get("/eval-runs", summary="评测运行记录（不含逐题结果）")
async def list_eval_runs(
    _: AdminUser,
    session: SessionDep,
    suite: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[EvalRunOut]:
    return await EvalService(session).list_runs(suite, limit)
