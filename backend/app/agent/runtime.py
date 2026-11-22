"""组装运行时的对话图：真实 LLM + 检索器 + PostgreSQL checkpointer。"""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import AgentDeps, AgentState, build_graph
from app.agent.llm import make_llms
from app.core.config import Settings
from app.core.logging import get_logger
from app.rag.retriever import Retriever

logger = get_logger(__name__)

AgentGraph = CompiledStateGraph[AgentState, None, AgentState, AgentState]


@dataclass
class AgentRuntime:
    graph: AgentGraph
    checkpointer: BaseCheckpointSaver[str]


def psycopg_conninfo(sqlalchemy_url: str) -> str:
    """应用用 asyncpg（postgresql+asyncpg://），checkpointer 用 psycopg，同一个库换驱动名即可。"""
    return (
        make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


def _proactor_loop() -> bool:
    # psycopg 的异步连接不支持 Windows 默认的 ProactorEventLoop
    return sys.platform == "win32" and isinstance(
        asyncio.get_running_loop(), asyncio.ProactorEventLoop
    )


@asynccontextmanager
async def agent_runtime(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    retriever: Retriever,
) -> AsyncIterator[AgentRuntime]:
    json_llm, answer_llm = make_llms(settings.llm, settings.llm_api_key())
    deps = AgentDeps(session_factory, retriever, json_llm, answer_llm, settings.llm)

    if _proactor_loop():
        # 只发生在 Windows 本机直接跑 uvicorn（不带 --reload）时；
        # 容器里和 --reload 时用的都是 SelectorEventLoop
        logger.warning("agent.checkpointer_in_memory", reason="Windows ProactorEventLoop")
        memory = InMemorySaver()
        yield AgentRuntime(build_graph(deps, memory), memory)
        return

    pool = AsyncConnectionPool(
        psycopg_conninfo(settings.db.url.get_secret_value()),
        min_size=1,
        max_size=settings.db.pool_size,
        # LangGraph 要求：自动提交、dict 行、关闭预编译（连接池复用时预编译语句会冲突）
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await saver.setup()  # 建表与迁移，幂等
        yield AgentRuntime(build_graph(deps, saver), saver)
    finally:
        await pool.close()
