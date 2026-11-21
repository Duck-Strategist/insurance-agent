from datetime import datetime
from typing import Any

from pydantic import BaseModel


class LinkedProduct(BaseModel):
    id: int
    name: str


class DocumentOut(BaseModel):
    id: int
    file_name: str
    status: str
    processing: bool  # 后台正在入库（本进程内）
    error: str | None
    parent_count: int
    child_count: int
    stats: dict[str, Any]
    indexed_at: datetime | None
    updated_at: datetime
    products: list[LinkedProduct]


class ParentPreview(BaseModel):
    id: int
    seq: int
    section_path: list[str]
    chars: int
    child_count: int
    preview: str
