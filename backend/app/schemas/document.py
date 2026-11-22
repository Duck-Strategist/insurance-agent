from datetime import datetime
from typing import Any

from pydantic import BaseModel


class LinkedProduct(BaseModel):
    id: int
    name: str


class DocumentOut(BaseModel):
    id: int
    file_name: str
    status: str  # 最近一次操作的结果
    processing: bool  # 后台正在入库（本进程内）
    searchable: bool  # 库里有可检索的块（可能是旧版本）
    stale: bool  # 库里的块来自旧版本文件，新版本尚未成功入库
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


class ParentPage(BaseModel):
    items: list[ParentPreview]
    total: int
    offset: int
    limit: int
