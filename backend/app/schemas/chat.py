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
