from datetime import UTC, datetime, timedelta

import jwt
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from tests.conftest import TEST_JWT_SECRET


async def register(client: AsyncClient, username: str = "alice", password: str = "password123"):
    return await client.post(
        "/api/v1/auth/register", json={"username": username, "password": password}
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_register_returns_token_and_normalized_user(client: AsyncClient) -> None:
    resp = await register(client, username="  Alice_01 ")
    assert resp.status_code == 201
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 24 * 3600
    assert body["user"]["username"] == "alice_01"
    assert body["user"]["role"] == "user"

    me = await client.get("/api/v1/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["username"] == "alice_01"


async def test_password_is_hashed_with_argon2(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await register(client, password="password123")
    user = await db_session.scalar(select(User).where(User.username == "alice"))
    assert user is not None
    assert user.password_hash.startswith("$argon2")
    assert "password123" not in user.password_hash


async def test_duplicate_username_is_rejected_case_insensitively(client: AsyncClient) -> None:
    assert (await register(client, username="alice")).status_code == 201
    resp = await register(client, username="ALICE")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "用户名已被占用"


async def test_register_validates_input(client: AsyncClient) -> None:
    assert (await register(client, username="ab")).status_code == 422
    assert (await register(client, username="bad name")).status_code == 422
    assert (await register(client, username="中文名")).status_code == 422
    assert (await register(client, password="short")).status_code == 422
    assert (await register(client, password="x" * 129)).status_code == 422


async def test_login_success_and_failures_share_message(client: AsyncClient) -> None:
    await register(client)
    ok = await client.post(
        "/api/v1/auth/login", json={"username": "ALICE", "password": "password123"}
    )
    assert ok.status_code == 200
    assert ok.json()["user"]["username"] == "alice"

    wrong_pw = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "wrong-password"}
    )
    no_user = await client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "password123"}
    )
    for resp in (wrong_pw, no_user):
        assert resp.status_code == 401
        # 不区分"用户不存在"和"密码错误"，防止枚举用户名
        assert resp.json()["detail"] == "用户名或密码错误"


async def test_me_requires_token(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def make_token(secret: str, sub: str = "1", expires_delta: timedelta = timedelta(hours=1)) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": sub, "role": "user", "iat": now, "exp": now + expires_delta},
        secret,
        algorithm="HS256",
    )


async def test_invalid_tokens_are_rejected(client: AsyncClient) -> None:
    body = (await register(client)).json()
    user_id = str(body["user"]["id"])

    cases = {
        "垃圾字符串": "not-a-jwt",
        "过期": make_token(TEST_JWT_SECRET, user_id, timedelta(seconds=-1)),
        "其他密钥签名": make_token("another-jwt-secret-also-32-bytes-long", user_id),
        "alg=none": jwt.encode({"sub": user_id, "exp": 9999999999, "iat": 0}, None, "none"),
    }
    for name, token in cases.items():
        resp = await client.get("/api/v1/auth/me", headers=bearer(token))
        assert resp.status_code == 401, name


async def test_token_of_deleted_user_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = (await register(client)).json()["access_token"]
    await db_session.execute(delete(User).where(User.username == "alice"))
    resp = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert resp.status_code == 401
