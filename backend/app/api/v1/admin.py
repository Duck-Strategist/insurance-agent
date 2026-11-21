"""管理后台：条款文档的上传、入库状态与重新入库。

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

from app.api.deps import AdminUser, SessionDep, SettingsDep
from app.core.config import RagSettings
from app.core.logging import get_logger
from app.rag.factory import RagNotConfiguredError, check_configured, ingestion_pipeline
from app.schemas.document import DocumentOut, ParentPreview
from app.services.document_service import DocumentService

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

    async def run() -> None:
        try:
            async with ingestion_pipeline(rag, session_factory) as pipeline:
                await pipeline.ingest(rag.clauses_dir / file_name, force=True)
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
    if not name.lower().endswith(".pdf") or name in {".pdf", ""}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="只支持 PDF 文件")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件超过 30MB")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="文件内容不是 PDF")
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
) -> list[ParentPreview]:
    service = DocumentService(session)
    if await service.get(doc_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return await service.list_parents(doc_id, limit, offset)
