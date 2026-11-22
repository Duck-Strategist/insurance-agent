from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import RagSettings
from app.rag.embedder import DashScopeEmbedder
from app.rag.parser import MinerUParser
from app.rag.pipeline import IngestionPipeline
from app.rag.reranker import DashScopeReranker
from app.rag.retriever import Retriever


class RagNotConfiguredError(Exception):
    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"缺少配置：{', '.join(missing)}")
        self.missing = missing


def check_configured(rag: RagSettings, *, need_parser: bool = True) -> None:
    """need_parser=False：只检索（向量化 + 重排），不需要 MinerU。"""
    required = [("RAG__DASHSCOPE_API_KEY", rag.dashscope_api_key)]
    if need_parser:
        required.insert(0, ("RAG__MINERU_TOKEN", rag.mineru_token))
    missing = [name for name, value in required if value is None]
    if missing:
        raise RagNotConfiguredError(missing)


def make_embedder(rag: RagSettings) -> DashScopeEmbedder:
    assert rag.dashscope_api_key
    return DashScopeEmbedder(
        rag.dashscope_api_key.get_secret_value(),
        base_url=rag.dashscope_base_url,
        model=rag.embedding_model,
        dim=rag.embedding_dim,
        batch_size=rag.embedding_batch_size,
    )


@asynccontextmanager
async def ingestion_pipeline(
    rag: RagSettings, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[IngestionPipeline]:
    """组装真实的解析器与向量化客户端；退出时关闭 HTTP 连接。"""
    check_configured(rag)
    assert rag.mineru_token
    embedder = make_embedder(rag)
    parser = MinerUParser(
        rag.mineru_token.get_secret_value(), rag.parsed_dir, rag.mineru_timeout_seconds
    )
    try:
        yield IngestionPipeline(session_factory, parser, embedder, rag)
    finally:
        await embedder.aclose()


@asynccontextmanager
async def retriever(
    rag: RagSettings, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Retriever]:
    """组装真实的向量化与重排客户端；退出时关闭 HTTP 连接。"""
    check_configured(rag, need_parser=False)
    assert rag.dashscope_api_key
    embedder = make_embedder(rag)
    reranker = DashScopeReranker(
        rag.dashscope_api_key.get_secret_value(),
        base_url=rag.dashscope_native_url,
        model=rag.rerank_model,
    )
    try:
        yield Retriever(session_factory, embedder, reranker, rag.retrieval)
    finally:
        await embedder.aclose()
        await reranker.aclose()
