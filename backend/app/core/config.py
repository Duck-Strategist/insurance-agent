"""应用配置：全部从环境变量 / .env 读取，代码中不出现任何密钥。

嵌套配置用双下划线分隔，例如 `DB__URL`、`LOG__LEVEL`。
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, SecretStr, field_validator, model_validator
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


# 仓库根目录下的 data/；容器里通过卷挂载到 /data
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


class RagSettings(BaseModel):
    data_dir: Path = DEFAULT_DATA_DIR

    # MinerU 云端解析（https://mineru.net）
    mineru_token: SecretStr | None = None
    mineru_timeout_seconds: int = 600

    # 阿里云 DashScope：OpenAI 兼容接口，向量化与重排共用一个 Key
    dashscope_api_key: SecretStr | None = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-v4"
    embedding_dim: int = 1024  # 改动需要同步修改迁移里的 vector(1024)
    embedding_batch_size: int = 10  # DashScope 单次最多 10 条

    # 切分参数（字符数）
    parent_max_chars: int = 2500
    child_chars: int = 400
    child_overlap: int = 60

    @field_validator("mineru_token", "dashscope_api_key", mode="before")
    @classmethod
    def _blank_as_none(cls, v: object) -> object:
        # docker compose 里 ${VAR:-} 未设置时传进来的是空字符串，视为未配置
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def clauses_dir(self) -> Path:
        return self.data_dir / "raw" / "clauses"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"


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
    rag: RagSettings = RagSettings()

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
