"""入库流水线：解析 → 结构化 → 切分 → 向量化 → 单事务写入。

一致性保证（对比原项目 Milvus + PG 双写无事务的问题 P3）：
- 网络调用（解析、向量化）全部在事务之外完成；
- 删除旧块、写入父块、写入子块、更新文档状态在同一个事务里，要么全成功要么全回滚，
  不会出现"子块指向不存在的父块"或"旧块删了新块没写进去"。
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import anyio.to_thread
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import RagSettings
from app.core.logging import get_logger
from app.models import Chunk, ChunkLevel, Document, DocumentStatus
from app.rag.chunker import build_chunks
from app.rag.embedder import Embedder, EmbeddingAuthError
from app.rag.parser import ParseAuthError, Parser
from app.rag.structure import build_sections
from app.rag.tokenizer import tokenize

logger = get_logger(__name__)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class IngestResult:
    file_name: str
    outcome: Literal["indexed", "skipped", "failed"]
    parents: int = 0
    children: int = 0
    error: str | None = None


class IngestionPipeline:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        parser: Parser,
        embedder: Embedder,
        settings: RagSettings,
    ) -> None:
        self._sessions = session_factory
        self._parser = parser
        self._embedder = embedder
        self._settings = settings

    async def ingest(self, path: Path, *, force: bool = False) -> IngestResult:
        # 读整个 PDF 算哈希是阻塞 IO，放到线程里
        file_hash = await anyio.to_thread.run_sync(file_sha256, path)
        doc_id, already_indexed = await self._register(path.name, file_hash)
        if already_indexed and not force:
            logger.info("ingest.skip_unchanged", file=path.name)
            return IngestResult(path.name, "skipped")

        log = logger.bind(file=path.name, document_id=doc_id)
        try:
            markdown = await self._parser.parse(path, file_hash)
            await self._set_status(doc_id, DocumentStatus.PARSED)

            structure = build_sections(markdown, title_hint=path.stem)
            parents = build_chunks(
                structure.sections,
                doc_title=path.stem,
                parent_max_chars=self._settings.parent_max_chars,
                child_chars=self._settings.child_chars,
                child_overlap=self._settings.child_overlap,
            )
            children = [c for p in parents for c in p.children]
            if not children:
                raise ValueError("清洗后没有可入库的正文，请检查解析结果")

            vectors = await self._embedder.embed([c.embed_text for c in children])
            stats = {
                **structure.stats,
                "parents": len(parents),
                "children": len(children),
                "max_child_chars": max(len(c.content) for c in children),
            }

            async with self._sessions() as session, session.begin():
                # 先锁住文档行：同一文档的两次入库并发时串行执行。
                # 否则两边各自 DELETE 旧块（READ COMMITTED 下看不到对方新插入的块），会留下两份块
                await session.execute(
                    select(Document.id).where(Document.id == doc_id).with_for_update()
                )
                await session.execute(delete(Chunk).where(Chunk.document_id == doc_id))
                seq = 0
                vec_iter = iter(vectors)
                for p in parents:
                    parent_row = Chunk(
                        document_id=doc_id,
                        level=ChunkLevel.PARENT,
                        seq=seq,
                        section_path=p.section_path,
                        content=p.content,
                    )
                    session.add(parent_row)
                    await session.flush()  # 拿到父块 id
                    seq += 1
                    for c in p.children:
                        session.add(
                            Chunk(
                                document_id=doc_id,
                                parent_id=parent_row.id,
                                level=ChunkLevel.CHILD,
                                seq=seq,
                                section_path=p.section_path,
                                content=c.content,
                                embedding=next(vec_iter),
                                tsv=func.to_tsvector("simple", tokenize(c.embed_text)),
                            )
                        )
                        seq += 1
                doc = await session.get_one(Document, doc_id)
                doc.status = DocumentStatus.INDEXED
                doc.error = None
                doc.stats = stats
                doc.parent_count = len(parents)
                doc.child_count = len(children)
                doc.indexed_at = datetime.now(UTC)
        except (ParseAuthError, EmbeddingAuthError) as exc:
            # 凭证错误对所有文件都一样：标记失败后向上抛出，让调用方停止批处理
            log.error("ingest.auth_failed", error=str(exc))
            await self._set_status(doc_id, DocumentStatus.FAILED, error=str(exc))
            raise
        except Exception as exc:
            log.exception("ingest.failed")
            await self._set_status(
                doc_id, DocumentStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            return IngestResult(path.name, "failed", error=str(exc))

        log.info("ingest.indexed", parents=len(parents), children=len(children))
        return IngestResult(path.name, "indexed", len(parents), len(children))

    async def _register(self, file_name: str, file_hash: str) -> tuple[int, bool]:
        """登记文档，返回 (id, 是否已是最新的 indexed 状态)。文件内容变了则重置为 pending。"""
        async with self._sessions() as session, session.begin():
            doc = await session.scalar(select(Document).where(Document.file_name == file_name))
            if doc is None:
                doc = Document(file_name=file_name, file_hash=file_hash)
                session.add(doc)
                await session.flush()
                return doc.id, False
            unchanged = doc.file_hash == file_hash and doc.status == DocumentStatus.INDEXED
            if doc.file_hash != file_hash:
                doc.file_hash = file_hash
                doc.status = DocumentStatus.PENDING
            return doc.id, unchanged

    async def _set_status(
        self, doc_id: int, status: DocumentStatus, *, error: str | None = None
    ) -> None:
        async with self._sessions() as session, session.begin():
            doc = await session.get_one(Document, doc_id)
            doc.status = status
            doc.error = error
