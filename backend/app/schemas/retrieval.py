from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.rag.retriever import SearchMode


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    mode: SearchMode = SearchMode.HYBRID_RERANK
    # 不传表示不限条款；对话里由代码根据产品决定，这里是调试入口，由管理员选
    document_id: int | None = None


class SearchHitOut(BaseModel):
    parent_id: int
    child_id: int
    document_id: int
    doc_title: str
    section_path: list[str]
    snippet: str
    parent_content: str
    score: float


class SearchResponse(BaseModel):
    hits: list[SearchHitOut]
    timings: dict[str, float]


class EvalRunOut(BaseModel):
    id: int
    suite: str
    dataset: str
    dataset_hash: str
    config: dict[str, Any]
    metrics: dict[str, Any]
    created_at: datetime


class EvalRunDetail(EvalRunOut):
    details: list[Any]
