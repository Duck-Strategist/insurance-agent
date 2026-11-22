from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class UserProfile(Base):
    """长期画像（spec §4、D8、D21）。一人一行；只记每个字段的来源和整体更新时间，不留变更历史。

    family：[{id, relation: spouse|child|parent, name, birth_date, gender, social_security}]
    existing_coverage：[{insured: "self" | 成员 id, product_id?, product_name, category?,
                         sum_assured?, start_date?}]，product_id 为空表示库外产品
    field_sources：字段名 → manual（页面填写）/ chat（对话里确认保存）/ chat_age（由对话中的
                   年龄估算的出生日期）
    """

    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint("gender IN ('male', 'female')", name="gender"),
        CheckConstraint("occupation_class BETWEEN 1 AND 6", name="occupation_class"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    birth_date: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(8))
    social_security: Mapped[bool | None] = mapped_column(Boolean)
    occupation_class: Mapped[int | None] = mapped_column(SmallInteger)
    city: Mapped[str | None] = mapped_column(String(32))
    annual_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    family: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    existing_coverage: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    field_sources: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
