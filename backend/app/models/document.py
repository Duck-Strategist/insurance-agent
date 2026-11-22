from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base
from app.models.base import TimestampMixin

EMBEDDING_DIM = 1024


class DocumentStatus(StrEnum):
    PENDING = "pending"  # 已登记，尚未处理
    PARSED = "parsed"  # 已解析出 Markdown（有缓存）
    INDEXED = "indexed"  # 已切分、向量化并写入
    FAILED = "failed"


class ChunkLevel(StrEnum):
    PARENT = "parent"  # 章节级，作为回答上下文与引用展示，不建向量
    CHILD = "child"  # 父块内的短片段，用于检索（向量 + 全文）


class Document(TimestampMixin, Base):
    """一份条款 PDF。一份条款可能被多个产品共用（如个人版/转保版），用 file_name 关联。"""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'parsed', 'indexed', 'failed')", name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DocumentStatus.PENDING, server_default="pending"
    )
    error: Mapped[str | None] = mapped_column(Text)
    # 入库统计：清洗前后字符数、识别出的标题数、父/子块数等，便于排查和后台展示
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    parent_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    child_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 库里现有这批块对应的文件版本（SHA-256），只在入库成功时更新。
    # status 只表示"最近一次操作"的结果：
    # 重新入库失败时 status=failed，但旧块和 indexed_hash 都还在。
    # 检索应按 indexed_hash IS NOT NULL 判断文档是否可用，而不是看 status。
    indexed_hash: Mapped[str | None] = mapped_column(String(64))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def searchable(self) -> bool:
        return self.indexed_hash is not None

    @property
    def stale(self) -> bool:
        """库里的块来自旧版本文件（新版本还没成功入库）。"""
        return self.indexed_hash is not None and self.indexed_hash != self.file_hash


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("level IN ('parent', 'child')", name="level"),
        # 子块必须有父块和向量；父块两者都没有
        CheckConstraint(
            "(level = 'child') = (parent_id IS NOT NULL AND embedding IS NOT NULL)",
            name="child_shape",
        ),
        Index("ix_chunks_document_id", "document_id"),
        Index("ix_chunks_parent_id", "parent_id"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    # 文档内顺序，用于按原文顺序展示和调试
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    # jieba 分词后用 'simple' 配置生成，避免自建中文分词插件
    tsv: Mapped[str | None] = mapped_column(TSVECTOR)
