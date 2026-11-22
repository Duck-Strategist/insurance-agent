"""对话：会话 CRUD（chat_threads 表）与流式问答（LangGraph 图 → SSE 事件）。"""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runtime import AgentRuntime
from app.core.logging import get_logger
from app.models import ChatThread
from app.schemas.chat import ChatMessageOut, ThreadOut

logger = get_logger(__name__)

TITLE_CHARS = 30


def sse(event: str, data: dict[str, Any]) -> str:
    """一条 Server-Sent Event。data 是单行 JSON（ensure_ascii=False 也不会含换行）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def thread_config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


def make_title(message: str) -> str:
    title = " ".join(message.split())
    return title[:TITLE_CHARS] + ("…" if len(title) > TITLE_CHARS else "")


class ThreadService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, user_id: int) -> list[ThreadOut]:
        rows = await self._session.scalars(
            select(ChatThread)
            .where(ChatThread.user_id == user_id)
            .order_by(ChatThread.updated_at.desc())
            .limit(100)
        )
        return [ThreadOut.model_validate(t, from_attributes=True) for t in rows]

    async def get(self, user_id: int, thread_id: str) -> ChatThread | None:
        """只返回属于该用户的会话：别人的会话和不存在的一样，都是 None。"""
        return await self._session.scalar(
            select(ChatThread).where(ChatThread.id == thread_id, ChatThread.user_id == user_id)
        )

    async def create(self, user_id: int, title: str = "新对话") -> ChatThread:
        thread = ChatThread(id=str(uuid.uuid4()), user_id=user_id, title=title)
        self._session.add(thread)
        await self._session.commit()
        await self._session.refresh(thread)
        return thread

    async def rename(self, thread: ChatThread, title: str) -> ChatThread:
        thread.title = title
        await self._session.commit()
        await self._session.refresh(thread)
        return thread

    async def touch(self, thread: ChatThread, *, first_message: str | None = None) -> None:
        values: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if first_message is not None and thread.title == "新对话":
            values["title"] = make_title(first_message)
        await self._session.execute(
            update(ChatThread).where(ChatThread.id == thread.id).values(**values)
        )
        await self._session.commit()

    async def delete(self, thread: ChatThread) -> None:
        await self._session.execute(delete(ChatThread).where(ChatThread.id == thread.id))
        await self._session.commit()


async def load_messages(runtime: AgentRuntime, thread_id: str) -> list[ChatMessageOut]:
    state = await runtime.graph.aget_state(thread_config(thread_id))
    out: list[ChatMessageOut] = []
    for msg in state.values.get("messages", []) if state.values else []:
        if isinstance(msg, HumanMessage):
            out.append(ChatMessageOut(id=msg.id, role="user", content=str(msg.content)))
        elif isinstance(msg, AIMessage):
            out.append(
                ChatMessageOut(
                    id=msg.id,
                    role="assistant",
                    content=str(msg.content),
                    citations=msg.additional_kwargs.get("citations", []),
                )
            )
    return out


async def stream_answer(runtime: AgentRuntime, thread_id: str, message: str) -> AsyncIterator[str]:
    """把图的执行过程转成 SSE 事件：status / token / citations / done / error。"""
    try:
        async for item in runtime.graph.astream(
            {"messages": [HumanMessage(content=message)]},
            thread_config(thread_id),
            stream_mode=["messages", "custom"],
        ):
            # 多种 stream_mode 时每项是 (模式, 数据)
            mode, data = cast(tuple[str, Any], item)
            if mode == "messages":
                chunk, meta = data
                # 只转发回答节点里模型的流式片段：路由、改写的 JSON 不外发；
                # 节点返回的完整 AIMessage 也会出现在 messages 流里，不能再发一遍
                if (
                    isinstance(chunk, AIMessageChunk)
                    and meta.get("langgraph_node") == "answer"
                    and chunk.content
                ):
                    yield sse("token", {"text": str(chunk.content)})
            elif isinstance(data, dict) and data.get("type") in ("status", "token", "citations"):
                kind = data["type"]
                payload = (
                    {"items": data["items"]} if kind == "citations" else {"text": data["text"]}
                )
                yield sse(kind, payload)
        yield sse("done", {})
    except Exception:
        # 不把内部错误细节（可能含上游接口信息）发给前端
        logger.exception("chat.stream_failed", thread_id=thread_id)
        yield sse("error", {"message": "回答生成失败，请稍后重试"})
