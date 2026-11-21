"""入库流水线的数据库集成测试：用假的解析器和向量化，验证写入结构与事务一致性。"""

import math
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import RagSettings
from app.models import Chunk, ChunkLevel, Document, DocumentStatus
from app.models.document import EMBEDDING_DIM
from app.rag.embedder import EmbeddingError
from app.rag.pipeline import IngestionPipeline
from app.rag.tokenizer import tokenize
from tests.test_rag_structure import DEC_STYLE


class FakeParser:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown
        self.calls = 0

    async def parse(self, path: Path, file_hash: str) -> str:
        self.calls += 1
        return self.markdown


class FakeEmbedder:
    """按文本长度生成确定性的单位向量；fail=True 时模拟接口故障。"""

    dim = EMBEDDING_DIM

    def __init__(self) -> None:
        self.fail = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise EmbeddingError("模拟故障")
        vectors = []
        for t in texts:
            v = [0.0] * self.dim
            v[len(t) % self.dim] = 1.0
            vectors.append(v)
        return vectors


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """流水线内部会自己开事务；把它们都绑定到一个外层事务里，测试结束整体回滚。"""
    async with engine.connect() as conn:
        outer = await conn.begin()
        yield async_sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        await outer.rollback()


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "测试条款_pytest.pdf"
    path.write_bytes(b"%PDF-fake v1")
    return path


def make_pipeline(
    factory: async_sessionmaker[AsyncSession], parser: FakeParser, embedder: FakeEmbedder
) -> IngestionPipeline:
    settings = RagSettings(parent_max_chars=2000, child_chars=400, child_overlap=60)
    return IngestionPipeline(factory, parser, embedder, settings)


async def chunk_counts(session: AsyncSession, doc_id: int) -> dict[str, int]:
    rows = await session.execute(
        select(Chunk.level, func.count()).where(Chunk.document_id == doc_id).group_by(Chunk.level)
    )
    return {level: n for level, n in rows.all()}


async def test_ingest_writes_parents_children_and_stats(
    session_factory: async_sessionmaker[AsyncSession], pdf: Path
) -> None:
    result = await make_pipeline(session_factory, FakeParser(DEC_STYLE), FakeEmbedder()).ingest(pdf)
    assert result.outcome == "indexed"
    assert (result.parents, result.children) == (3, 4)

    async with session_factory() as s:
        doc = await s.scalar(select(Document).where(Document.file_name == pdf.name))
        assert doc is not None
        assert doc.status == DocumentStatus.INDEXED
        assert (doc.parent_count, doc.child_count) == (3, 4)
        assert doc.stats["levels"] == {"dec1": 1, "dec2": 2, "dec3": 3}
        assert await chunk_counts(s, doc.id) == {"parent": 3, "child": 4}

        # 每个子块都挂在同一文档的父块下，路径一致
        orphans = await s.scalar(
            text(
                "SELECT count(*) FROM chunks c LEFT JOIN chunks p ON p.id = c.parent_id "
                "WHERE c.document_id = :d AND c.level = 'child' "
                "AND (p.id IS NULL OR p.level <> 'parent' OR p.section_path <> c.section_path)"
            ),
            {"d": doc.id},
        )
        assert orphans == 0


async def test_full_text_and_vector_search_work(
    session_factory: async_sessionmaker[AsyncSession], pdf: Path
) -> None:
    await make_pipeline(session_factory, FakeParser(DEC_STYLE), FakeEmbedder()).ingest(pdf)
    async with session_factory() as s:
        # 全文：用同一个分词函数生成查询
        query = " | ".join(tokenize("重大疾病保险金").split())
        hits = (
            await s.scalars(
                select(Chunk.content).where(
                    Chunk.tsv.op("@@")(func.to_tsquery("simple", query)),
                    Chunk.level == ChunkLevel.CHILD,
                )
            )
        ).all()
        assert any("确诊重大疾病" in h for h in hits)

        # 向量：查询向量与某个子块完全相同时，余弦距离为 0，排第一
        target = await s.scalar(select(Chunk).where(Chunk.level == ChunkLevel.CHILD).limit(1))
        assert target is not None and target.embedding is not None
        nearest = await s.scalar(
            select(Chunk.id)
            .where(Chunk.level == ChunkLevel.CHILD)
            .order_by(Chunk.embedding.cosine_distance(list(target.embedding)))
            .limit(1)
        )
        assert nearest == target.id
        # pgvector 以 float32 存储，允许微小误差
        assert math.isclose(sum(x * x for x in target.embedding), 1.0, rel_tol=1e-6)


async def test_unchanged_file_is_skipped_and_force_reindexes_without_duplicates(
    session_factory: async_sessionmaker[AsyncSession], pdf: Path
) -> None:
    parser = FakeParser(DEC_STYLE)
    pipeline = make_pipeline(session_factory, parser, FakeEmbedder())
    await pipeline.ingest(pdf)

    assert (await pipeline.ingest(pdf)).outcome == "skipped"
    assert parser.calls == 1

    assert (await pipeline.ingest(pdf, force=True)).outcome == "indexed"
    async with session_factory() as s:
        doc_id = await s.scalar(select(Document.id).where(Document.file_name == pdf.name))
        assert doc_id is not None
        assert await chunk_counts(s, doc_id) == {"parent": 3, "child": 4}


async def test_changed_file_content_triggers_reindex(
    session_factory: async_sessionmaker[AsyncSession], pdf: Path
) -> None:
    parser = FakeParser(DEC_STYLE)
    pipeline = make_pipeline(session_factory, parser, FakeEmbedder())
    await pipeline.ingest(pdf)
    pdf.write_bytes(b"%PDF-fake v2")  # noqa: ASYNC240  测试里的小文件写入
    assert (await pipeline.ingest(pdf)).outcome == "indexed"
    assert parser.calls == 2


async def test_failure_keeps_previous_chunks_intact(
    session_factory: async_sessionmaker[AsyncSession], pdf: Path
) -> None:
    """向量化失败时：文档标记 failed，但上一次成功写入的块原样保留（没有删了一半的情况）。"""
    embedder = FakeEmbedder()
    pipeline = make_pipeline(session_factory, FakeParser(DEC_STYLE), embedder)
    await pipeline.ingest(pdf)

    embedder.fail = True
    result = await pipeline.ingest(pdf, force=True)
    assert result.outcome == "failed"
    assert "模拟故障" in (result.error or "")

    async with session_factory() as s:
        doc = await s.scalar(select(Document).where(Document.file_name == pdf.name))
        assert doc is not None
        assert doc.status == DocumentStatus.FAILED
        assert doc.error and "EmbeddingError" in doc.error
        assert await chunk_counts(s, doc.id) == {"parent": 3, "child": 4}


async def test_empty_document_fails_cleanly(
    session_factory: async_sessionmaker[AsyncSession], pdf: Path
) -> None:
    result = await make_pipeline(
        session_factory, FakeParser("# 只有标题\n\n- 1 -"), FakeEmbedder()
    ).ingest(pdf)
    assert result.outcome == "failed"
    assert "没有可入库的正文" in (result.error or "")


async def test_auth_error_marks_failed_and_propagates(
    session_factory: async_sessionmaker[AsyncSession], pdf: Path
) -> None:
    """凭证错误不能被当成"单个文件失败"吞掉，否则批处理会把每个文件都失败一遍。"""
    from app.rag.parser import ParseAuthError

    class AuthFailParser:
        async def parse(self, path: Path, file_hash: str) -> str:
            raise ParseAuthError("MinerU token 无效或已过期")

    pipeline = IngestionPipeline(session_factory, AuthFailParser(), FakeEmbedder(), RagSettings())
    with pytest.raises(ParseAuthError):
        await pipeline.ingest(pdf)
    async with session_factory() as s:
        doc = await s.scalar(select(Document).where(Document.file_name == pdf.name))
        assert doc is not None and doc.status == DocumentStatus.FAILED
        assert doc.error == "MinerU token 无效或已过期"
