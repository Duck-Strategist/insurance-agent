"""应用配置：全部从环境变量 / .env 读取，代码中不出现任何密钥。

嵌套配置用双下划线分隔，例如 `DB__URL`、`LOG__LEVEL`。
"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, SecretStr
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
