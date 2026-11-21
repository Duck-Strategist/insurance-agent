from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.models import ProductCategory

# 金额统一输出为两位小数的字符串，避免 JSON 浮点误差，格式也不随数据来源变化
Money = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.2f}", return_type=str)]


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: ProductCategory
    insurer: str
    description: str
    target_group: str
    highlights: list[str]
    min_premium: Money
    max_premium: Money | None


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
