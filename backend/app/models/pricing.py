from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class RateBookRecord(Base):
    """一个产品某个版本的全部费率表（JSON 原样存储，spec D41）。

    旧版本不删除：报价记录引用具体版本，费率表更新后旧报价仍可按当时的数据复现。
    """

    __tablename__ = "rate_books"
    __table_args__ = (
        UniqueConstraint("product_id", "version"),
        # 每个产品只有一个当前版本
        Index(
            "uq_rate_books_current",
            "product_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(16), nullable=False)  # JSON 文本的哈希
    source_file: Mapped[str] = mapped_column(String(300), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


class Quote(Base):
    """一次保存下来的报价：参数 + 明细 + 所用费率版本，可复现（spec §4）。"""

    __tablename__ = "quotes"
    __table_args__ = (Index("ix_quotes_user_id_created_at", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    rate_book_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rate_books.id"), nullable=False
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    breakdown: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    annual_premium: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total_premium: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    pay_years: Mapped[int] = mapped_column(nullable=False)
    sum_assured: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
