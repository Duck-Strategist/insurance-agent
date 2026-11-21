from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.core.security import create_access_token
from app.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.services.auth_service import AuthService, InvalidCredentialsError, UsernameTakenError

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(user: User, settings: SettingsDep) -> TokenResponse:
    token, expires_in = create_access_token(user.id, user.role, settings.auth)
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserOut.model_validate(user)
    )


@router.post("/register", status_code=status.HTTP_201_CREATED, summary="注册并直接登录")
async def register(
    body: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    try:
        user = await AuthService(session).register(body.username, body.password)
    except UsernameTakenError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="用户名已被占用") from None
    return _token_response(user, settings)


@router.post("/login", summary="用户名密码登录")
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    try:
        user = await AuthService(session).authenticate(body.username, body.password)
    except InvalidCredentialsError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return _token_response(user, settings)


@router.get("/me", summary="当前登录用户")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
