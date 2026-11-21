"""密码哈希与 JWT。只做纯计算，不碰数据库。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.core.config import AuthSettings

# 默认使用 Argon2id
_password_hash = PasswordHash.recommended()

# 用户不存在时也做一次同等耗时的校验，避免通过响应时间探测用户名是否存在
_DUMMY_HASH = _password_hash.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        _password_hash.verify(password, _DUMMY_HASH)
        return False
    return _password_hash.verify(password, password_hash)


@dataclass(frozen=True)
class TokenPayload:
    user_id: int
    role: str


class InvalidTokenError(Exception):
    pass


def create_access_token(user_id: int, role: str, settings: AuthSettings) -> tuple[str, int]:
    """返回 (token, 有效秒数)。"""
    expires_in = settings.access_token_expire_minutes * 60
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, expires_in


def decode_access_token(token: str, settings: AuthSettings) -> TokenPayload:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            # 显式限定算法，防止 alg=none 等降级攻击
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "iat"]},
        )
        return TokenPayload(user_id=int(payload["sub"]), role=str(payload.get("role", "user")))
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise InvalidTokenError(str(exc)) from exc
