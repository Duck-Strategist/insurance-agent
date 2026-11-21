from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Username = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[a-zA-Z0-9_]{3,32}$"),
]
# 上限 128：Argon2 本身不截断，但限制长度可以防止超长输入拖慢哈希
Password = Annotated[str, Field(min_length=8, max_length=128)]


class RegisterRequest(BaseModel):
    username: Username = Field(description="3-32 位字母、数字或下划线，不区分大小写")
    password: Password


class LoginRequest(BaseModel):
    # 登录时不校验格式，避免泄露注册规则；错了统一返回"用户名或密码错误"
    username: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=64)]
    password: Annotated[str, Field(max_length=128)]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
