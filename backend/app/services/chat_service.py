"""对话：会话 CRUD（chat_threads 表）与流式问答（LangGraph 图 → SSE 事件）。"""

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import AgentState
from app.agent.runtime import AgentRuntime
from app.core.logging import get_logger
from app.models import ChatThread
from app.schemas.chat import ChatMessageOut, ThreadOut

logger = get_logger(__name__)

TITLE_CHARS = 30
# 节点通过 get_stream_writer 发出、原样转发给前端的事件
CUSTOM_EVENTS = {"status", "token", "citations", "plan_card", "plan_status", "profile_suggestion"}
# 模型回答逐 token 转发的节点；其余节点里的模型调用（路由、改写、抽取的 JSON）不外发
ANSWER_NODES = {"answer", "claim_answer"}

PlanStatusLookup = Callable[[list[int]], Awaitable[dict[int, str]]]


def sse(event: str, data: dict[str, Any]) -> str:
    """一条 Server-Sent Event。data 是单行 JSON（ensure_ascii=False 也不会含换行）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def thread_config(thread_id: str, user_id: int | None = None) -> RunnableConfig:
    # user_id 给顾问节点写方案用；只读状态时不需要
    return {"configurable": {"thread_id": thread_id, "user_id": user_id}}


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


async def pending_plan_id(runtime: AgentRuntime, thread_id: str) -> int | None:
    """会话是否停在方案确认（advisor_review 的 interrupt）上；是的话返回待确认的方案 id。"""
    snapshot = await runtime.graph.aget_state(thread_config(thread_id))
    for item in snapshot.interrupts:
        value = item.value
        if isinstance(value, dict) and isinstance(value.get("plan_id"), int):
            return int(value["plan_id"])
    return None


async def load_messages(
    runtime: AgentRuntime, thread_id: str, plan_statuses: PlanStatusLookup | None = None
) -> list[ChatMessageOut]:
    state = await runtime.graph.aget_state(thread_config(thread_id))
    raw = [m for m in (state.values.get("messages", []) if state.values else [])]
    plan_ids = [
        int(m.additional_kwargs["plan"]["plan_id"])
        for m in raw
        if isinstance(m, AIMessage) and m.additional_kwargs.get("plan")
    ]
    statuses = await plan_statuses(plan_ids) if plan_statuses and plan_ids else {}
    out: list[ChatMessageOut] = []
    for msg in raw:
        if isinstance(msg, HumanMessage):
            out.append(ChatMessageOut(id=msg.id, role="user", content=str(msg.content)))
        elif isinstance(msg, AIMessage):
            out.append(
                ChatMessageOut(
                    id=msg.id,
                    role="assistant",
                    content=str(msg.content),
                    citations=msg.additional_kwargs.get("citations", []),
                    plan=_with_status(msg.additional_kwargs.get("plan"), statuses),
                )
            )
    return out


def _with_status(plan: dict[str, Any] | None, statuses: dict[int, str]) -> dict[str, Any] | None:
    """历史里的方案卡片带上数据库里的当前状态（待确认 / 已确认 / 已被新方案取代……）。"""
    if not plan:
        return None
    return {**plan, "status": statuses.get(int(plan["plan_id"]), "unknown")}


async def stream_answer(
    runtime: AgentRuntime, thread_id: str, message: str, user_id: int | None = None
) -> AsyncIterator[str]:
    """处理一条用户消息。会话停在方案确认上时，这句话作为恢复值交给 advisor_review
    （由它判断是确认、调整还是问别的），而不是开启新的一轮（spec D48）。"""
    payload: AgentState | Command[Any]
    if await pending_plan_id(runtime, thread_id) is not None:
        payload = Command(resume={"action": "message", "text": message})
    else:
        payload = AgentState(messages=[HumanMessage(content=message)])
    async for event in _stream(runtime, thread_id, user_id, payload):
        yield event


async def stream_resume(
    runtime: AgentRuntime, thread_id: str, user_id: int, decision: dict[str, Any]
) -> AsyncIterator[str]:
    """方案卡片上的按钮：确认 / 取消 / 去掉某几项。"""
    async for event in _stream(runtime, thread_id, user_id, Command(resume=decision)):
        yield event


async def _stream(
    runtime: AgentRuntime,
    thread_id: str,
    user_id: int | None,
    payload: AgentState | Command[Any],
) -> AsyncIterator[str]:
    """图的执行过程转成 SSE 事件（事件类型见 CUSTOM_EVENTS，另有 token / done / error）。"""
    try:
        async for item in runtime.graph.astream(
            payload,
            thread_config(thread_id, user_id),
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
                    and meta.get("langgraph_node") in ANSWER_NODES
                    and chunk.content
                ):
                    yield sse("token", {"text": str(chunk.content)})
            elif isinstance(data, dict) and data.get("type") in CUSTOM_EVENTS:
                kind = data["type"]
                yield sse(kind, {k: v for k, v in data.items() if k != "type"})
        yield sse("done", {})
    except Exception:
        # 不把内部错误细节（可能含上游接口信息）发给前端
        logger.exception("chat.stream_failed", thread_id=thread_id)
        yield sse("error", {"message": "回答生成失败，请稍后重试"})
