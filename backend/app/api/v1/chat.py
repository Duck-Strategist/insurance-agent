"""对话：会话管理与流式问答（SSE）。所有会话按用户隔离，别人的会话一律当作不存在（404）。"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import AgentDep, CurrentUser, SessionDep
from app.models import ChatThread
from app.schemas.chat import ChatMessageOut, ChatRequest, ThreadCreate, ThreadOut, ThreadRename
from app.services.chat_service import ThreadService, load_messages, sse, stream_answer

router = APIRouter(tags=["chat"])

ThreadId = Annotated[str, Path(max_length=36)]


def _busy(request: Request) -> set[str]:
    """正在生成回答的会话。同一会话并发写 checkpoint 会互相覆盖，第二个请求直接拒绝。
    进程内集合：多实例部署时需要换成数据库锁或 Redis。"""
    state = request.app.state
    if not hasattr(state, "chat_busy"):
        state.chat_busy = set()
    busy: set[str] = state.chat_busy
    return busy


async def _own_thread(service: ThreadService, user_id: int, thread_id: str) -> ChatThread:
    thread = await service.get(user_id, thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return thread


@router.get("/threads", summary="我的会话（按最近对话时间倒序）")
async def list_threads(user: CurrentUser, session: SessionDep) -> list[ThreadOut]:
    return await ThreadService(session).list(user.id)


@router.post("/threads", status_code=status.HTTP_201_CREATED, summary="新建会话")
async def create_thread(user: CurrentUser, session: SessionDep, body: ThreadCreate) -> ThreadOut:
    thread = await ThreadService(session).create(user.id, body.title)
    return ThreadOut.model_validate(thread, from_attributes=True)


@router.patch("/threads/{thread_id}", summary="重命名会话")
async def rename_thread(
    thread_id: ThreadId, user: CurrentUser, session: SessionDep, body: ThreadRename
) -> ThreadOut:
    service = ThreadService(session)
    thread = await service.rename(await _own_thread(service, user.id, thread_id), body.title)
    return ThreadOut.model_validate(thread, from_attributes=True)


@router.delete(
    "/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除会话及其消息"
)
async def delete_thread(
    thread_id: ThreadId, user: CurrentUser, session: SessionDep, agent: AgentDep, request: Request
) -> None:
    service = ThreadService(session)
    thread = await _own_thread(service, user.id, thread_id)
    if thread.id in _busy(request):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该会话正在回答，请稍后再删")
    # 先删消息（checkpoint）再删会话行：反过来的话中途失败会留下找不到归属的消息
    await agent.checkpointer.adelete_thread(thread.id)
    await service.delete(thread)


@router.get("/threads/{thread_id}/messages", summary="会话历史消息")
async def list_messages(
    thread_id: ThreadId, user: CurrentUser, session: SessionDep, agent: AgentDep
) -> list[ChatMessageOut]:
    thread = await _own_thread(ThreadService(session), user.id, thread_id)
    return await load_messages(agent, thread.id)


@router.post(
    "/chat",
    summary="流式问答（SSE）",
    response_description="text/event-stream：thread / status / token / citations / done / error",
)
async def chat(
    body: ChatRequest, user: CurrentUser, session: SessionDep, agent: AgentDep, request: Request
) -> StreamingResponse:
    service = ThreadService(session)
    if body.thread_id is None:
        thread = await service.create(user.id)
    else:
        thread = await _own_thread(service, user.id, body.thread_id)
    busy = _busy(request)
    if thread.id in busy:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="上一条消息还在回答中，请稍候")
    busy.add(thread.id)
    try:
        await service.touch(thread, first_message=body.message)
        title = (await service.get(user.id, thread.id) or thread).title
    except BaseException:
        busy.discard(thread.id)
        raise

    async def events() -> AsyncIterator[str]:
        try:
            # 第一个事件告诉前端会话 id（新建会话时前端据此跳转）
            yield sse("thread", {"id": thread.id, "title": title})
            async for event in stream_answer(agent, thread.id, body.message):
                yield event
        finally:
            # 客户端断开时生成器被关闭，同样会走到这里
            busy.discard(thread.id)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # 关闭代理缓冲（nginx），否则 token 会攒一批才到前端
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
