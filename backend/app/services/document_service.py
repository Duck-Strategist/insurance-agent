from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Chunk, ChunkLevel, Document, DocumentStatus, Product
from app.schemas.document import DocumentOut, LinkedProduct, ParentPreview


class DocumentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_documents(self, processing: set[str]) -> list[DocumentOut]:
        docs: Sequence[Document] = (
            await self.session.scalars(select(Document).order_by(Document.file_name))
        ).all()
        # 一份条款可能被多个产品共用，按文件名关联
        rows = await self.session.execute(
            select(Product.clause_file, Product.id, Product.name).where(
                Product.clause_file.is_not(None)
            )
        )
        by_file: dict[str, list[LinkedProduct]] = defaultdict(list)
        for clause_file, pid, name in rows.all():
            if clause_file is not None:
                by_file[clause_file].append(LinkedProduct(id=pid, name=name))

        return [
            DocumentOut(
                id=d.id,
                file_name=d.file_name,
                status=d.status,
                processing=d.file_name in processing,
                error=d.error,
                parent_count=d.parent_count,
                child_count=d.child_count,
                stats=d.stats,
                indexed_at=d.indexed_at,
                updated_at=d.updated_at,
                products=by_file.get(d.file_name, []),
            )
            for d in docs
        ]

    async def get(self, doc_id: int) -> Document | None:
        return await self.session.get(Document, doc_id)

    async def register_upload(self, file_name: str, file_hash: str) -> Document:
        """上传后立即登记，列表里马上能看到；内容变了就重置为 pending 等待入库。"""
        doc = await self.session.scalar(select(Document).where(Document.file_name == file_name))
        if doc is None:
            doc = Document(file_name=file_name, file_hash=file_hash)
            self.session.add(doc)
        elif doc.file_hash != file_hash:
            doc.file_hash = file_hash
            doc.status = DocumentStatus.PENDING
            doc.error = None
        await self.session.commit()
        await self.session.refresh(doc)
        return doc

    async def list_parents(self, doc_id: int, limit: int, offset: int) -> list[ParentPreview]:
        child = aliased(Chunk)
        child_count = (
            select(func.count())
            .where(child.parent_id == Chunk.id)
            .correlate(Chunk)
            .scalar_subquery()
        )
        rows = await self.session.execute(
            select(Chunk, child_count)
            .where(Chunk.document_id == doc_id, Chunk.level == ChunkLevel.PARENT)
            .order_by(Chunk.seq)
            .offset(offset)
            .limit(limit)
        )
        return [
            ParentPreview(
                id=c.id,
                seq=c.seq,
                section_path=c.section_path,
                chars=len(c.content),
                child_count=n,
                preview=c.content[:200],
            )
            for c, n in rows.all()
        ]
