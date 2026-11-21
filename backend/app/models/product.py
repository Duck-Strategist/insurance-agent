from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base
from app.models.base import TimestampMixin


class ProductCategory(StrEnum):
    MEDICAL = "medical"
    CRITICAL_ILLNESS = "critical_illness"
    ACCIDENT = "accident"
    LIFE = "life"


class ProductStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "category IN ('medical', 'critical_illness', 'accident', 'life')", name="category"
        ),
        CheckConstraint("status IN ('active', 'inactive')", name="status"),
        CheckConstraint("max_premium IS NULL OR max_premium >= min_premium", name="premium_range"),
        Index("ix_products_category_status", "category", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 产品名唯一，种子脚本据此做幂等写入
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    insurer: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    target_group: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", server_default=""
    )
    highlights: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    # 起保费；终身寿险等按保额计费的产品只有下限，max_premium 为空
    min_premium: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    max_premium: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # 条款 PDF 文件名，M2 入库时用来关联文档
    clause_file: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ProductStatus.ACTIVE,
        server_default=ProductStatus.ACTIVE.value,
    )
