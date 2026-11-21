from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """请求级数据库会话：路由中用 `Depends(get_session)` 获取。"""
    async with request.app.state.db.session_factory() as session:
        yield session
