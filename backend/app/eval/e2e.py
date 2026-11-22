"""端到端评测（spec §8、D12）：条款问答的回答质量。

流程：每道题用线上同一个函数（graph.answer_from_clauses：检索 + 带引用生成）回答，
再由另一个、更强的模型当裁判（LLM-as-judge），对照标准章节原文打分：
- 正确性：correct / partial / incorrect（只对条款里有答案的题）；
- 引用忠实度：回答里每个 [n] 标注的内容，第 n 段原文里是否有依据；
- 拒答：条款里没有答案的题应该说「条款中没有找到」，有答案的题不应该拒答。

裁判和人工的一致率：按题目分层抽 20% 导出 CSV，人工标注后用 `agreement` 命令计算，
结果写回同一条 eval_runs 记录（metrics.human）。人工没标完之前，这个数字是「待测」。
"""

import csv
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.eval import metrics as M

Correctness = Literal["correct", "partial", "incorrect"]
CORRECTNESS_SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
REFERENCE_CHARS = 2500
CITED_CHARS = 1500


class E2EItem(BaseModel):
    id: str
    question: str
    file_name: str
    answerable: bool
    gold: list[list[str]] = Field(default_factory=list)  # 有答案的题：标准章节路径
    evidence: str = ""  # 生成题的原文依据
    source: str = ""  # 来自哪个评测集的哪道题，或 handwritten
    note: str = ""


JUDGE_SYSTEM = """你是保险条款问答的评审员。根据给出的条款原文，严格评价一个回答。只输出 JSON。"""

JUDGE_USER = """问题：{question}

{reference_block}

待评价的回答：
{answer}

回答里引用的原文段落（编号对应回答中的 [n]）：
{cited}

请输出 JSON：
{{
  "refused": 回答是否表示条款中没有找到 / 无法回答（true/false），
  "correctness": "correct" / "partial" / "incorrect"，
  "citations": [{{"index": 编号,
                 "supported": 回答里标注这个编号的内容能否在该段原文中找到依据（true/false）}}],
  "reason": "一句话理由"
}}

评分标准：
- correctness 只看回答的结论和关键数字是否与标准依据一致：完全一致且没有错误信息为 correct；
  结论对但漏了重要条件、或混入了小错误为 partial；
  结论错、编造了条款里没有的规定、或该答却拒答为 incorrect；
- 条款里没有答案的题：回答明确说没找到、没有编造具体规定为 correct；编造了答案为 incorrect；
- citations 列出回答中出现的每一个编号，逐个判断；回答里没有编号就输出空列表。"""


def reference_block(item: E2EItem, gold_text: str) -> str:
    if not item.answerable:
        return "标准依据：这款产品的条款里没有这个问题的答案（正确做法是说明条款中没有找到）。"
    parts = []
    if item.evidence:
        parts.append(f"标准依据（关键原文）：{item.evidence}")
    parts.append(f"标准章节原文：\n{gold_text[:REFERENCE_CHARS]}")
    return "\n".join(parts)


def cited_block(citations: Sequence[dict[str, Any]]) -> str:
    if not citations:
        return "（回答没有引用）"
    return "\n\n".join(
        f"[{c['index']}] {' > '.join(c['section_path'])}\n{str(c['parent_content'])[:CITED_CHARS]}"
        for c in citations
    )


@dataclass
class Judgement:
    refused: bool
    correctness: Correctness
    citations: dict[int, bool]  # 编号 → 是否有依据
    reason: str
    parsed: bool


def parse_judgement(data: dict[str, Any], cited: Sequence[int]) -> Judgement:
    """裁判输出校验：解析不了的字段按最不利的结果记（incorrect、引用不支持），并标 parsed=False。"""
    correctness = data.get("correctness")
    refused = data.get("refused")
    raw = data.get("citations")
    supported: dict[int, bool] = {}
    if isinstance(raw, list):
        for c in raw:
            if isinstance(c, dict) and isinstance(c.get("index"), int):
                supported[c["index"]] = c.get("supported") is True
    parsed = correctness in CORRECTNESS_SCORE and isinstance(refused, bool)
    # 回答里真实出现的编号都要有判断；裁判漏判的按「不支持」记
    citations = {i: supported.get(i, False) for i in cited}
    if any(i not in supported for i in cited):
        parsed = False
    return Judgement(
        refused=refused if isinstance(refused, bool) else False,
        correctness=correctness if correctness in CORRECTNESS_SCORE else "incorrect",
        citations=citations,
        reason=str(data.get("reason") or "")[:300],
        parsed=parsed,
    )


@dataclass
class E2EResult:
    item: E2EItem
    answer: str
    citations: list[dict[str, Any]]
    judgement: Judgement
    latency_ms: float
    no_hits: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def as_detail(self) -> dict[str, Any]:
        j = self.judgement
        return {
            "id": self.item.id,
            "question": self.item.question,
            "answerable": self.item.answerable,
            "answer": self.answer,
            "cited_sections": [
                {"index": c["index"], "path": c["section_path"]} for c in self.citations
            ],
            "judge": {
                "refused": j.refused,
                "correctness": j.correctness,
                "citations": {str(k): v for k, v in j.citations.items()},
                "reason": j.reason,
                "parsed": j.parsed,
            },
            "no_hits": self.no_hits,
            "latency_ms": self.latency_ms,
        }


def summarize(results: Sequence[E2EResult]) -> dict[str, Any]:
    ans = [r for r in results if r.item.answerable]
    unans = [r for r in results if not r.item.answerable]
    cites = [ok for r in ans for ok in r.judgement.citations.values()]
    answered = [r for r in ans if not r.judgement.refused]

    def rate(n: float, d: int) -> float | None:
        return round(n / d, 4) if d else None

    latencies = [r.latency_ms for r in results]
    return {
        "n": len(results),
        "answerable": {
            "n": len(ans),
            # correct 记 1、partial 记 0.5
            "correctness": rate(
                sum(CORRECTNESS_SCORE[r.judgement.correctness] for r in ans), len(ans)
            )
            if ans
            else None,
            "correct_rate": rate(sum(r.judgement.correctness == "correct" for r in ans), len(ans)),
            "partial_rate": rate(sum(r.judgement.correctness == "partial" for r in ans), len(ans)),
            # 该答却说没找到
            "false_refusal_rate": rate(sum(r.judgement.refused for r in ans), len(ans)),
            # 回答了但一个引用都没有
            "uncited_rate": rate(sum(not r.citations for r in answered), len(answered)),
        },
        # 引用忠实度：所有引用里，裁判认为原文支持的比例
        "citation_precision": rate(sum(cites), len(cites)),
        "citations_total": len(cites),
        "unanswerable": {
            "n": len(unans),
            "refusal_rate": rate(sum(r.judgement.refused for r in unans), len(unans)),
            # 没拒答还编了规定
            "hallucination_rate": rate(
                sum(r.judgement.correctness == "incorrect" for r in unans), len(unans)
            ),
        },
        "judge_unparsed": sum(not r.judgement.parsed for r in results),
        "latency_ms": {
            "p50": round(M.percentile(latencies, 50), 1),
            "p95": round(M.percentile(latencies, 95), 1),
        },
    }


# ---------- 人工抽检 ----------

HUMAN_FIELDS = [
    "run_id",
    "id",
    "answerable",
    "question",
    "reference",
    "answer",
    "cited",
    "judge_correctness",
    "judge_refused",
    "judge_citations",
    "human_correctness",
    "human_refused",
    "human_citations_ok",
    "note",
]


def sample_for_human(
    results: Sequence[E2EResult], ratio: float = 0.2, seed: int = 0
) -> list[E2EResult]:
    """按「有答案 / 无答案」分层随机抽样，每层至少 1 题；固定种子，可复现。"""
    rng = random.Random(seed)
    out: list[E2EResult] = []
    for layer in (True, False):
        group = sorted((r for r in results if r.item.answerable is layer), key=lambda r: r.item.id)
        if not group:
            continue
        k = max(1, round(len(group) * ratio))
        out += sorted(rng.sample(group, k), key=lambda r: r.item.id)
    return out


def write_human_csv(
    path: Path, run_id: int, sample: Sequence[E2EResult], references: dict[str, str]
) -> None:
    """Excel 能直接打开（UTF-8 BOM）。标注人只填 human_* 三列和 note。"""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HUMAN_FIELDS)
        w.writeheader()
        for r in sample:
            j = r.judgement
            w.writerow(
                {
                    "run_id": run_id,
                    "id": r.item.id,
                    "answerable": "是" if r.item.answerable else "否",
                    "question": r.item.question,
                    "reference": references.get(r.item.id, "")[:REFERENCE_CHARS],
                    "answer": r.answer,
                    "cited": cited_block(r.citations),
                    "judge_correctness": j.correctness,
                    "judge_refused": "是" if j.refused else "否",
                    "judge_citations": "、".join(
                        f"[{k}]{'有依据' if v else '无依据'}" for k, v in j.citations.items()
                    ),
                    "human_correctness": "",
                    "human_refused": "",
                    "human_citations_ok": "",
                    "note": "",
                }
            )


YES = {"是", "y", "yes", "true", "1"}
NO = {"否", "n", "no", "false", "0"}


def _yes_no(value: str) -> bool | None:
    v = value.strip().lower()
    return True if v in YES else False if v in NO else None


def read_human_csv(path: Path) -> tuple[int, list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{path.name} 是空的")
    run_ids = {r["run_id"] for r in rows}
    if len(run_ids) != 1:
        raise ValueError("一个文件只能对应一次评测运行")
    return int(run_ids.pop()), rows


def agreement(rows: Sequence[dict[str, str]]) -> dict[str, Any]:
    """裁判与人工的一致率。只统计人工已经填写的题；正确性另算 Cohen's kappa（扣除偶然一致）。"""
    labeled = [r for r in rows if r["human_correctness"].strip() in CORRECTNESS_SCORE]
    if not labeled:
        raise ValueError("还没有人工标注（human_correctness 填 correct / partial / incorrect）")
    judge = [r["judge_correctness"] for r in labeled]
    human = [r["human_correctness"].strip() for r in labeled]
    refused = [
        (_yes_no(r["judge_refused"]), _yes_no(r["human_refused"]))
        for r in labeled
        if _yes_no(r["human_refused"]) is not None
    ]
    cite_all_ok = [
        ("无依据" not in r["judge_citations"], _yes_no(r["human_citations_ok"]))
        for r in labeled
        if r["judge_citations"] and _yes_no(r["human_citations_ok"]) is not None
    ]
    return {
        "n": len(labeled),
        "of": len(rows),
        "correctness_agreement": round(
            sum(a == b for a, b in zip(judge, human, strict=True)) / len(labeled), 4
        ),
        "correctness_kappa": cohen_kappa(judge, human, list(CORRECTNESS_SCORE)),
        "refused_agreement": (
            round(sum(a == b for a, b in refused) / len(refused), 4) if refused else None
        ),
        # 裁判「全部引用都有依据」与人工判断是否一致
        "citations_agreement": (
            round(sum(a == b for a, b in cite_all_ok) / len(cite_all_ok), 4)
            if cite_all_ok
            else None
        ),
    }


def cohen_kappa(a: Sequence[str], b: Sequence[str], labels: Sequence[str]) -> float | None:
    """两位标注者的 Cohen's kappa；期望一致率为 1 时（只出现一个标签）无定义，返回 None。"""
    n = len(a)
    if n == 0:
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    expected = sum((a.count(lab) / n) * (b.count(lab) / n) for lab in labels)
    if expected == 1:
        return None
    return round((observed - expected) / (1 - expected), 4)
