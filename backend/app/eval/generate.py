"""按父块用 LLM 生成检索评测题（spec D19：LLM 生成 + 人工审核）。

- 每份条款按章节顺序等距抽样，覆盖条款的各个部分，而不是集中在前几章；
- LLM 同时给出"能回答问题的原文片段"，脚本校验它确实逐字出现在该章节里，编造的直接丢弃；
- 生成结果写成 JSONL，人工审核后（删掉不合格的、补充其他也能回答的章节）才作为评测集使用。
"""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import PurePath

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.eval.dataset import RetrievalItem

logger = get_logger(__name__)

MIN_BODY_CHARS = 60  # 正文太短（只有标题、一句话的章节）出不了有意义的题
MAX_PROMPT_CHARS = 4000

PROMPT = """你在为保险条款检索系统构建评测集。下面是某款保险条款中的一个章节。

条款：{title}
章节：{path}
原文：
<<<
{content}
>>>

请站在普通投保人或被保险人的角度，提出一个**只看这段原文就能回答**的问题。要求：
1. 问题具体，能从原文里找到明确答案；不要问"这一条讲了什么"这类笼统的问题；
2. 不要提产品名称或条款名称（默认用户已经在咨询这款产品）；
3. 用普通人的说法，不要照抄原文的长句；
4. evidence 是原文中能回答这个问题的一句话，必须逐字复制，不能改写或省略；
5. 如果原文没有实质内容（只有标题、目录或表头），answerable 填 false。

只输出 JSON：{{"answerable": true, "question": "...", "evidence": "..."}}"""


@dataclass
class Section:
    file_name: str
    path: list[str]
    content: str  # 同一章节被拆成多个父块时按顺序拼接
    first_seq: int

    @property
    def body_chars(self) -> int:
        heading = self.path[-1] if self.path else ""
        return len(self.content) - len(heading)


async def load_sections(session_factory: async_sessionmaker[AsyncSession]) -> list[Section]:
    async with session_factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT d.file_name, c.section_path, c.content, c.seq
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.level = 'parent' AND d.indexed_hash IS NOT NULL
                ORDER BY d.file_name, c.seq
                """
            )
        )
        merged: dict[tuple[str, tuple[str, ...]], Section] = {}
        for file_name, path, content, seq in rows.all():
            key = (file_name, tuple(path))
            if key in merged:
                merged[key].content += "\n\n" + content
            else:
                merged[key] = Section(file_name, list(path), content, seq)
    return list(merged.values())


def sample_evenly(sections: list[Section], per_doc: int) -> list[Section]:
    """每份条款在可出题的章节里按顺序等距抽 per_doc 个（确定性，重跑结果一致）。"""
    by_doc: dict[str, list[Section]] = {}
    for s in sections:
        if s.body_chars >= MIN_BODY_CHARS:
            by_doc.setdefault(s.file_name, []).append(s)
    picked: list[Section] = []
    for file_name in sorted(by_doc):
        pool = sorted(by_doc[file_name], key=lambda s: s.first_seq)
        if len(pool) <= per_doc:
            picked += pool
            continue
        step = (len(pool) - 1) / (per_doc - 1) if per_doc > 1 else 0
        indices = sorted({round(i * step) for i in range(per_doc)})
        picked += [pool[i] for i in indices]
    return picked


_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def normalize(s: str) -> str:
    """校验 evidence 时忽略空白和 HTML 标签（表格章节的原文是 HTML）。"""
    return _SPACE.sub("", _TAG.sub("", s))


def evidence_in_section(evidence: str, content: str) -> bool:
    ev = normalize(evidence)
    return len(ev) >= 6 and ev in normalize(content)


class QuestionGenerator:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        model: str,
        concurrency: int = 5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._sem = asyncio.Semaphore(concurrency)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(90.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def generate(self, section: Section) -> dict[str, object] | None:
        prompt = PROMPT.format(
            title=PurePath(section.file_name).stem,
            path=" > ".join(section.path) or "（无章节）",
            content=section.content[:MAX_PROMPT_CHARS],
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        }
        async with self._sem:
            resp: httpx.Response | None = None
            for attempt in range(3):
                try:
                    resp = await self._client.post("/chat/completions", json=payload)
                except httpx.TransportError as exc:
                    # 网络抖动只算这一题失败，不能让 gather 里的其他题一起中止
                    logger.warning("eval.generate_transport_error", error=str(exc))
                    resp = None
                else:
                    if resp.status_code in (401, 403):
                        resp.raise_for_status()  # 凭证错误直接中止整个生成
                    if resp.status_code == 200:
                        break
                await asyncio.sleep(2**attempt)
            else:
                logger.warning("eval.generate_failed", status=resp.status_code if resp else None)
                return None
            assert resp is not None
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            data: dict[str, object] = json.loads(content)
        except (KeyError, ValueError) as exc:
            logger.warning("eval.generate_bad_json", error=str(exc))
            return None
        return data


async def generate_items(
    sections: list[Section], generator: QuestionGenerator
) -> tuple[list[RetrievalItem], dict[str, int]]:
    """返回 (通过校验的题目, 丢弃原因统计)。"""
    results = await asyncio.gather(*(generator.generate(s) for s in sections))
    items: list[RetrievalItem] = []
    dropped = {"llm_error": 0, "not_answerable": 0, "evidence_not_found": 0}
    for section, data in zip(sections, results, strict=True):
        if data is None:
            dropped["llm_error"] += 1
            continue
        question = str(data.get("question") or "").strip()
        evidence = str(data.get("evidence") or "").strip()
        if not data.get("answerable") or not question:
            dropped["not_answerable"] += 1
            continue
        if not evidence_in_section(evidence, section.content):
            dropped["evidence_not_found"] += 1
            continue
        items.append(
            RetrievalItem(
                id=f"gen-{len(items) + 1:03d}",
                question=question,
                file_name=section.file_name,
                gold=[section.path],
                source="generated",
                evidence=evidence,
            )
        )
    return items, dropped
