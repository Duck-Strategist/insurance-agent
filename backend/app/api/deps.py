from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runtime import AgentRuntime
from app.core.config import Settings, get_settings
from app.core.security import InvalidTokenError, decode_access_token
from app.models import User, UserRole
from app.rag.retriever import Retriever


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """请求级数据库会话：路由中用 `Depends(get_session)` 获取。"""
    async with request.app.state.db.session_factory() as session:
        yield session


def get_app_settings(request: Request) -> Settings:
    # create_app(settings) 传入的配置优先，便于测试注入
    settings: Settings | None = getattr(request.app.state, "settings", None)
    return settings or get_settings()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]

_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise _unauthorized("未登录")
    try:
        payload = decode_access_token(credentials.credentials, settings.auth)
    except InvalidTokenError:
        raise _unauthorized("登录已失效，请重新登录") from None
    # 每次都查库：用户被删除后旧 token 立即失效，角色也以数据库为准
    user = await session.get(User, payload.user_id)
    if user is None:
        raise _unauthorized("登录已失效，请重新登录")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_admin_user(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


def get_retriever(request: Request) -> Retriever:
    # 应用启动时按配置创建（见 app.main）；未配置 DashScope 时为 None
    retriever: Retriever | None = getattr(request.app.state, "retriever", None)
    if retriever is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="检索未配置：缺少 RAG__DASHSCOPE_API_KEY",
        )
    return retriever


RetrieverDep = Annotated[Retriever, Depends(get_retriever)]


def get_agent(request: Request) -> AgentRuntime:
    # 应用启动时创建（见 app.main）；对话模型或检索未配置时为 None
    runtime: AgentRuntime | None = getattr(request.app.state, "agent", None)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="对话服务未配置：缺少对话模型或检索的 Key",
        )
    return runtime


AgentDep = Annotated[AgentRuntime, Depends(get_agent)]
