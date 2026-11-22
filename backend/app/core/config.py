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


class RetrievalSettings(BaseModel):
    """检索参数由代码固定，不暴露给 LLM（spec §6.3）。"""

    top_k: int = 5  # 返回的父块数
    recall_k: int = 30  # 每一路召回的子块数
    rrf_k: int = 60  # RRF 常数，原论文取 60
    fulltext_weight: float = 1.0  # RRF 中全文召回一路的权重（向量一路为 1）
    rerank_candidates: int = 30  # 融合后送去重排的子块数
    hnsw_ef_search: int = 100
    # 全文召回时忽略出现在超过这个比例子块里的词（如「的」「条款」），它们只会带来噪声
    fulltext_max_df_ratio: float = 0.5
    df_cache_seconds: float = 600.0


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

    # 重排（DashScope 原生接口，不在 OpenAI 兼容模式下）
    dashscope_native_url: str = "https://dashscope.aliyuncs.com/api/v1"
    rerank_model: str = "qwen3-rerank"  # M3 评测选定（spec D33）
    # 评测集生成用的对话模型（只在离线评测脚本里用）
    eval_llm_model: str = "qwen-plus"

    retrieval: RetrievalSettings = RetrievalSettings()

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


class LlmSettings(BaseModel):
    """对话模型：任何 OpenAI 兼容接口（spec D36）。

    默认用 DashScope 的 qwen-plus，Key 不填时复用 RAG__DASHSCOPE_API_KEY；
    换 DeepSeek 只需设置 LLM__BASE_URL=https://api.deepseek.com、LLM__MODEL=deepseek-chat、LLM__API_KEY。
    """

    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: SecretStr | None = None
    model: str = "qwen-plus"
    temperature: float = 0.3
    timeout_seconds: float = 60.0
    max_retries: int = 2
    # 回答时放进上下文的章节原文，每节最多这么多字（父块最长 2500 字，5 节约 1 万字）
    context_chars_per_section: int = 1500
    # 路由与改写时带上最近几轮对话
    history_turns: int = 3

    @field_validator("api_key", mode="before")
    @classmethod
    def _blank_as_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v


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
    llm: LlmSettings = LlmSettings()

    def llm_api_key(self) -> SecretStr | None:
        """对话模型的 Key：没单独配置时复用 DashScope 的 Key（默认模型就在 DashScope 上）。"""
        return self.llm.api_key or self.rag.dashscope_api_key

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
