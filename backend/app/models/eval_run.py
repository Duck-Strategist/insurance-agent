from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class EvalRun(Base):
    """一次评测运行。简历和文档里引用的数字都必须能在这张表里找到（spec §8）。"""

    __tablename__ = "eval_runs"
    __table_args__ = (Index("ix_eval_runs_suite_created_at", "suite", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    suite: Mapped[str] = mapped_column(String(32), nullable=False)  # retrieval / e2e / route
    # 数据集文件名与内容哈希：数据集改过之后，新旧结果不能直接比较
    dataset: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # 逐题结果（命中名次、耗时、召回的章节），用于失败案例分析
    details: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
