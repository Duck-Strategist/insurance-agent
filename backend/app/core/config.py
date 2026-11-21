"""应用配置：全部从环境变量 / .env 读取，代码中不出现任何密钥。

嵌套配置用双下划线分隔，例如 `DB__URL`、`LOG__LEVEL`。
"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseModel):
    name: str = "安心保智能保险顾问"
    env: Literal["local", "test", "prod"] = "local"
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


class DatabaseSettings(BaseModel):
    # 本地默认值与 deploy/docker-compose.yml 保持一致（主机端口 5433，避开本机已装的 PostgreSQL）
    url: SecretStr = SecretStr("postgresql+asyncpg://insurance:insurance@localhost:5433/insurance")
    pool_size: int = 5
    max_overflow: int = 10
    echo: bool = False


class LogSettings(BaseModel):
    level: str = "INFO"
    json_format: bool = False  # 本地用彩色控制台，容器内设为 true 输出 JSON


DEV_JWT_SECRET = "dev-insecure-jwt-secret-change-me-please"


class AuthSettings(BaseModel):
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 1 天；不做 refresh token（spec D14）


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppSettings = AppSettings()
    db: DatabaseSettings = DatabaseSettings()
    log: LogSettings = LogSettings()
    auth: AuthSettings = AuthSettings()

    @model_validator(mode="after")
    def _reject_dev_secret_in_prod(self) -> "Settings":
        # 生产环境必须显式配置 AUTH__JWT_SECRET，否则任何人都能伪造 token
        if self.app.env == "prod" and self.auth.jwt_secret.get_secret_value() == DEV_JWT_SECRET:
            raise ValueError("APP__ENV=prod 时必须设置 AUTH__JWT_SECRET")
        if len(self.auth.jwt_secret.get_secret_value().encode()) < 32:
            # HS256 要求密钥不短于哈希输出长度（RFC 7518 3.2）
            raise ValueError("AUTH__JWT_SECRET 至少 32 字节")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
