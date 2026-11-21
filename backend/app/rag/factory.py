from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import RagSettings
from app.rag.embedder import DashScopeEmbedder
from app.rag.parser import MinerUParser
from app.rag.pipeline import IngestionPipeline


class RagNotConfiguredError(Exception):
    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"缺少配置：{', '.join(missing)}")
        self.missing = missing


def check_configured(rag: RagSettings) -> None:
    missing = [
        name
        for name, value in (
            ("RAG__MINERU_TOKEN", rag.mineru_token),
            ("RAG__DASHSCOPE_API_KEY", rag.dashscope_api_key),
        )
        if value is None
    ]
    if missing:
        raise RagNotConfiguredError(missing)


@asynccontextmanager
async def ingestion_pipeline(
    rag: RagSettings, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[IngestionPipeline]:
    """组装真实的解析器与向量化客户端；退出时关闭 HTTP 连接。"""
    check_configured(rag)
    assert rag.mineru_token and rag.dashscope_api_key
    embedder = DashScopeEmbedder(
        rag.dashscope_api_key.get_secret_value(),
        base_url=rag.dashscope_base_url,
        model=rag.embedding_model,
        dim=rag.embedding_dim,
        batch_size=rag.embedding_batch_size,
    )
    parser = MinerUParser(
        rag.mineru_token.get_secret_value(), rag.parsed_dir, rag.mineru_timeout_seconds
    )
    try:
        yield IngestionPipeline(session_factory, parser, embedder, rag)
    finally:
        await embedder.aclose()
