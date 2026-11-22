from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class PlanStatus(StrEnum):
    PENDING = "pending"  # 已生成方案卡片，等待用户确认（对话停在 interrupt）
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"  # 用户要求调整，被新方案取代
    EXPIRED = "expired"  # 用户转去聊别的，对话离开了确认环节，这张卡片不能再确认


class Plan(Base):
    """顾问生成的保障方案（spec §4、D46）。items 里每项带当时的参数、报价明细和费率版本，可复现。"""

    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'cancelled', 'superseded', 'expired')",
            name="status",
        ),
        Index("ix_plans_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 删除会话不删方案：方案属于用户，会话只是来源
    thread_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chat_threads.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PlanStatus.PENDING, server_default="pending"
    )
    slots: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # 生成方案时的需求
    items: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    # 预算、合计、没配上的险种及原因
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
