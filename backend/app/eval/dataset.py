"""检索评测集：JSONL，一行一题，放在 data/eval/ 下并入库。

标准答案用「条款文件名 + 章节路径」表示，而不是块 id：重新入库后块 id 会变，章节路径不变
（除非结构规则改了，那时评测集也应该复核）。一题可以有多个标准章节（答案分散在几处，
或同一内容在正文和释义里都有），检索到任意一个就算命中。
"""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class RetrievalItem(BaseModel):
    id: str
    question: str
    file_name: str  # 这道题针对的条款（对话里由代码定位产品后得到）
    gold: list[list[str]] = Field(min_length=1)  # 标准章节路径，任一命中即可
    source: Literal["generated", "hard"]
    evidence: str = ""  # 生成题：条款里能回答问题的原文片段（已校验确实在章节里）
    tags: list[str] = Field(default_factory=list)  # 难题的类型：口语化、跨章节、同义改写……
    note: str = ""  # 审核备注

    def gold_keys(self) -> set[tuple[str, tuple[str, ...]]]:
        return {(self.file_name, tuple(path)) for path in self.gold}


def load(path: Path) -> list[RetrievalItem]:
    items = [
        RetrievalItem.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [i.id for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path.name} 里有重复的题目 id")
    return items


def save(path: Path, items: list[RetrievalItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(i.model_dump(exclude_defaults=True), ensure_ascii=False) for i in items]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
