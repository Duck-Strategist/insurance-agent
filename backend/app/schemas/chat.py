from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ThreadOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ThreadCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=100)


class ThreadRename(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class ChatMessageOut(BaseModel):
    id: str | None
    role: Literal["user", "assistant"]
    content: str
    # 回答引用的条款章节：index 对应正文里的 [n]
    citations: list[dict[str, Any]] = Field(default_factory=list)
    # 顾问生成的方案卡片（含 plan_id 和当前状态 status）
    plan: dict[str, Any] | None = None


class ChatRequest(BaseModel):
    # 不传则新建会话（第一条消息做标题）
    thread_id: str | None = Field(default=None, max_length=36)
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("消息不能为空")
        return v


class ResumeRequest(BaseModel):
    """方案卡片上的操作。plan_id 必须是会话当前待确认的方案，防止在旧卡片上点确认。"""

    thread_id: str = Field(max_length=36)
    plan_id: int
    action: Literal["confirm", "cancel", "edit"]
    # action=edit：换一款（排除这些产品，同险种换成下一款）
    remove_product_ids: list[int] = Field(default_factory=list, max_length=10)
    # action=edit：这些险种不要了
    remove_categories: list[Literal["medical", "critical_illness", "accident", "life"]] = Field(
        default_factory=list, max_length=4
    )
