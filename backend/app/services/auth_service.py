from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password_async, verify_password_async
from app.models import User, UserRole


class UsernameTakenError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register(self, username: str, password: str) -> User:
        user = User(
            username=username,
            password_hash=await hash_password_async(password),
            role=UserRole.USER,
        )
        self.session.add(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            # 依赖唯一约束判重，而不是先查后插，避免并发注册时的竞态
            await self.session.rollback()
            raise UsernameTakenError(username) from exc
        await self.session.refresh(user)
        return user

    async def authenticate(self, username: str, password: str) -> User:
        user = await self.session.scalar(select(User).where(User.username == username))
        # 无论用户是否存在都做一次哈希校验，响应时间一致
        ok = await verify_password_async(password, user.password_hash if user else None)
        if not ok or user is None:
            raise InvalidCredentialsError
        return user

    async def get_user(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)
