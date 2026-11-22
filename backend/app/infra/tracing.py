"""LLM 可观测性（spec D17）：Langfuse 自托管，通过 LangChain 回调接入。

- 没配置 Key 时完全不启用（返回空的回调列表），对话和评测照常运行；
- trace 带 user_id、session_id（会话 id）、名称和标签；LangGraph 的每个节点、每次模型调用
  自动成为 trace 里的一层，能看到路由、改写、检索后的回答各花了多久、用了多少 token；
- 评测运行也上报（标签 eval + 套件名），便于对照线上 trace。
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from app.core.config import LangfuseSettings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: Langfuse | None = None
_public_key: str = ""


def init_tracing(settings: LangfuseSettings, environment: str = "local") -> bool:
    """按配置创建 Langfuse 客户端（进程内一个）；返回是否启用。"""
    global _client, _public_key
    if not settings.enabled():
        return False
    if _client is None:
        assert settings.secret_key is not None
        _client = Langfuse(
            public_key=settings.public_key,
            secret_key=settings.secret_key.get_secret_value(),
            host=settings.host,
            environment=environment,
        )
        _public_key = settings.public_key
        logger.info("tracing.enabled", host=settings.host)
    return True


def tracing_enabled() -> bool:
    return _client is not None


def trace_config(
    name: str,
    *,
    user_id: int | None = None,
    session_id: str | None = None,
    tags: Sequence[str] = (),
) -> RunnableConfig:
    """给 graph.astream / llm.ainvoke 的 config：启用时带上 Langfuse 回调和 trace 属性。"""
    if _client is None:
        return {}
    metadata: dict[str, Any] = {"langfuse_trace_name": name, "langfuse_tags": list(tags)}
    if user_id is not None:
        metadata["langfuse_user_id"] = str(user_id)
    if session_id is not None:
        metadata["langfuse_session_id"] = session_id
    return {"callbacks": [CallbackHandler(public_key=_public_key)], "metadata": metadata}


def flush_traces() -> None:
    """命令行评测结束前调用：后台批量上报，进程退出前要把剩下的发出去。"""
    if _client is not None:
        _client.flush()


def shutdown_tracing() -> None:
    global _client
    if _client is not None:
        _client.shutdown()
        _client = None
