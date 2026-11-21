"""数据库引擎与会话。

引擎在应用启动时创建、关闭时释放（见 app.main 的 lifespan），
请求内通过 app.api.deps.get_session 依赖拿到会话。
"""

import asyncio

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import DatabaseSettings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 就绪检查的连接超时；asyncpg 默认要等 60 秒
PING_TIMEOUT_SECONDS = 3.0

# 统一约束命名，Alembic 自动生成迁移时名字稳定
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Database:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            settings.url.get_secret_value(),
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_pre_ping=True,
            echo=settings.echo,
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def ping(self) -> bool:
        """就绪检查用：超时或失败返回 False，并记录原因方便排查。"""
        try:
            async with asyncio.timeout(PING_TIMEOUT_SECONDS):
                async with self.engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            logger.warning("db.ping_failed", error=type(exc).__name__, detail=str(exc)[:200])
            return False

    async def dispose(self) -> None:
        await self.engine.dispose()
