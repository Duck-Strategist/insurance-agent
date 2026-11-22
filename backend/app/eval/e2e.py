"""端到端评测（spec §8、D12）：条款问答的回答质量。

流程：每道题用线上同一个函数（graph.answer_from_clauses：检索 + 带引用生成）回答，
再由另一个、更强的模型当裁判（LLM-as-judge），对照标准章节原文打分：
- 正确性：correct / partial / incorrect（只对条款里有答案的题）；
- 引用忠实度：回答里每个 [n] 标注的内容，第 n 段原文里是否有依据；
- 拒答：条款里没有答案的题应该说「条款中没有找到」，有答案的题不应该拒答。

裁判和人工的一致率：按题目分层抽 20% 导出 CSV，人工标注后用 `agreement` 命令计算，
结果写回 eval_runs 记录（metrics.human 等）。人工没标完之前，这个数字是「待测」。

裁判版本（config.judge_version）：
- v1（#147）：只给裁判标准章节和被引用的段落，裁判自己说引用有没有依据。人工抽检发现它在正确性上偏严
  （回答里来自其他章节的正确信息被当成「额外信息」扣分），在引用上偏松（spec Q23）；
- v2（#157）：给裁判回答模型看到的全部段落（编号与回答一致）；「多说」不扣分，说错才扣；
  每个引用要逐字摘录第 n 段里的依据，由代码核对摘录确实在第 n 段里，核对不上就算没有依据。
  开发集（v1 抽检的 17 题）上矫枉过正：全部判 correct（漏掉了人工判 incorrect 的一题），
  引用核对又过严（摘录里的字面换行符、上标、半角标点、把两句拼成一句都会核对失败）；
- v3：要求先逐句核对回答里的具体说法（适用范围、条件、数字），「多说」只有在原文有依据时才不扣分；
  段落给全文；摘录按句切开逐句核对，忽略上标、字面换行和全半角标点差异。
"""

import csv
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.eval import metrics as M
from app.eval.generate import normalize

Correctness = Literal["correct", "partial", "incorrect"]
CORRECTNESS_SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
REFERENCE_CHARS = 2500
CITED_CHARS = 1500  # 人工抽检 CSV 里展示的段落长度
JUDGE_PASSAGE_CHARS = 4000  # 给裁判的段落长度：裁判要看全文才能判断说法有没有依据
JUDGE_VERSION = "v3"


class E2EItem(BaseModel):
    id: str
    question: str
    file_name: str
    answerable: bool
    gold: list[list[str]] = Field(default_factory=list)  # 有答案的题：标准章节路径
    evidence: str = ""  # 生成题的原文依据
    source: str = ""  # 来自哪个评测集的哪道题，或 handwritten
    note: str = ""


JUDGE_SYSTEM = (
    """你是保险条款问答的评审员。根据给出的条款原文，严格、公正地评价一个回答。只输出 JSON。"""
)

JUDGE_USER = """问题：{question}

{reference_block}

回答模型看到的全部条款原文（编号与回答中的 [n] 一致）：
{passages}

待评价的回答：
{answer}

请按下面的步骤评价：
1. 逐句看回答里的每个具体说法（适用于哪些责任、什么条件下赔、天数比例次数金额），
   在标准依据和上面的原文里找依据；与原文矛盾、或者找不到依据的说法都写进 errors。
   「仅」「只」「不影响」「全额」这类限定说法要找到原文明确这么说才算有依据；
2. 再根据 errors 和结论判断 correctness；
3. 最后逐个核对引用。

输出 JSON：
{{
  "refused": 回答的整体意思是否是条款中没有找到 / 无法回答（true/false），
  "errors": ["回答里与条款原文矛盾、或在上面所有原文里都找不到依据的具体说法，逐条列出；
              没有就是空列表"],
  "correctness": "correct" / "partial" / "incorrect"，
  "citations": [{{"index": 编号,
                 "claim": "回答里标了这个编号的那句话",
                 "quote": "第 index 段原文里能支持这句话的原文，逐字摘录（不要改写、不要拼接）；
                           第 index 段里找不到就写空字符串"}}],
  "reason": "一句话理由"
}}

correctness 的标准（有答案的题）：
- correct：回答了问题，结论和关键数字（天数、比例、次数、金额、年龄）与标准依据一致，errors 为空。
  回答比标准依据多说的内容，只有在上面的原文里有依据时才不扣分；
- partial：结论对，但漏了回答这个问题必需的条件（例如只有满足某条件才赔），
  或者 errors 里有次要的错误；
- incorrect：结论错、关键数字错、errors 里有会误导用户的说法，或者条款里有答案却说没找到。
条款里没有答案的题：明确说没找到、没有编造具体规定为 correct；
给出了具体答案或编造了规定为 incorrect。

citations：回答中出现的每一个编号都要列出，一个编号出现在几句话里就选最主要的一句。
quote 必须是第 index 段里能直接支持 claim 的原文；
同一主题但不能支持这句话的原文不算，写空字符串。"""


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


def passages_block(passages: dict[int, tuple[list[str], str]]) -> str:
    """回答模型看到的全部段落，编号与回答一致。"""
    if not passages:
        return "（没有检索到条款原文）"
    return "\n\n".join(
        f"[{i}] {' > '.join(path)}\n{content[:JUDGE_PASSAGE_CHARS]}"
        for i, (path, content) in sorted(passages.items())
    )


@dataclass
class Judgement:
    refused: bool
    correctness: Correctness
    citations: dict[int, bool]  # 编号 → 是否有依据
    reason: str
    parsed: bool
    errors: list[str] = field(default_factory=list)
    # 编号 → 裁判摘录的依据（v2）；摘录核对不上的不算有依据
    quotes: dict[int, str] = field(default_factory=dict)


_SUP = re.compile(r"<sup>.*?</sup>", re.S)
# 裁判在 JSON 里写的换行有时会变成字面的 \n 两个字符
_LITERAL_BREAK = re.compile(r"\\[nrt]")
_HALF_TO_FULL = str.maketrans(",;:()!?", "，；：（）！？")
_SENTENCE = re.compile(r"[。；;\n]|……|\.\.\.")
MIN_FRAGMENT = 6


def _norm(text: str) -> str:
    return normalize(_LITERAL_BREAK.sub("", _SUP.sub("", text))).translate(_HALF_TO_FULL)


def quote_in_passage(quote: str, passage: str) -> bool:
    """摘录按句切开，每一句（至少 6 个字的）都要逐字出现在段落里。
    允许裁判把同一段里不相邻的几句摘在一起；忽略空白、HTML 标签、上标、字面换行和全半角标点。"""
    body = _norm(passage)
    pieces = _SENTENCE.split(_LITERAL_BREAK.sub("\n", quote))
    fragments = [f for f in (_norm(x) for x in pieces) if len(f) >= MIN_FRAGMENT]
    return bool(fragments) and all(f in body for f in fragments)


def parse_judgement(
    data: dict[str, Any], cited: Sequence[int], passages: dict[int, tuple[list[str], str]]
) -> Judgement:
    """裁判输出校验。解析不了的字段按最不利的结果记（incorrect、引用不支持），并标 parsed=False。

    引用是否有依据不听裁判一句话：要有摘录，且摘录（忽略空白和表格标签，至少 6 个字）
    确实出现在第 n 段原文里。v1 裁判在引用上偏松，人工抽检里 2 处分歧都是它说有依据、人工说没有。"""
    correctness = data.get("correctness")
    refused = data.get("refused")
    quotes: dict[int, str] = {}
    raw = data.get("citations")
    if isinstance(raw, list):
        for c in raw:
            if isinstance(c, dict) and isinstance(c.get("index"), int):
                quotes[c["index"]] = str(c.get("quote") or "").strip()
    citations = {
        i: bool(quotes.get(i)) and i in passages and quote_in_passage(quotes[i], passages[i][1])
        for i in cited
    }
    raw_errors = data.get("errors")
    errors = (
        [str(e)[:200] for e in raw_errors if str(e).strip()] if isinstance(raw_errors, list) else []
    )
    # 回答里真实出现的编号都要有判断；裁判漏判的按「不支持」记
    parsed = (
        correctness in CORRECTNESS_SCORE
        and isinstance(refused, bool)
        and all(i in quotes for i in cited)
    )
    return Judgement(
        refused=refused if isinstance(refused, bool) else False,
        correctness=correctness if correctness in CORRECTNESS_SCORE else "incorrect",
        citations=citations,
        reason=str(data.get("reason") or "")[:300],
        parsed=parsed,
        errors=errors[:10],
        quotes={i: q[:300] for i, q in quotes.items() if i in cited},
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
                "errors": j.errors,
                "quotes": {str(k): v for k, v in j.quotes.items()},
            },
            # 回答模型看到的全部段落（编号 → 章节路径），换裁判重判时据此还原上下文
            "context": self.extra.get("context", []),
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
    results: Sequence[E2EResult],
    ratio: float = 0.2,
    seed: int = 0,
    exclude: frozenset[str] = frozenset(),
) -> list[E2EResult]:
    """按「有答案 / 无答案」分层随机抽样，每层至少 1 题；固定种子，可复现。

    exclude：已经抽过、看过的题（如调裁判时用作开发集的那批），从剩下的题里抽，
    每层的题数仍按全部题目的比例算。"""
    rng = random.Random(seed)
    out: list[E2EResult] = []
    for layer in (True, False):
        layer_all = [r for r in results if r.item.answerable is layer]
        group = sorted((r for r in layer_all if r.item.id not in exclude), key=lambda r: r.item.id)
        if not group:
            continue
        k = min(len(group), max(1, round(len(layer_all) * ratio)))
        out += sorted(rng.sample(group, k), key=lambda r: r.item.id)
    return out


def write_human_csv(
    path: Path,
    run_id: int,
    sample: Sequence[E2EResult],
    references: dict[str, str],
    *,
    blind: bool = False,
) -> None:
    """Excel 能直接打开（UTF-8 BOM）。标注人只填 human_* 三列和 note。
    blind=True 时裁判的三列留空：标注人看不到裁判结论，不会先入为主；
    算一致率时按 run 的逐题结果取裁判结论。"""
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
                    **judge_columns(j, blind=blind),
                    "human_correctness": "",
                    "human_refused": "",
                    "human_citations_ok": "",
                    "note": "",
                }
            )


def judge_columns(j: Judgement | dict[str, Any], *, blind: bool = False) -> dict[str, str]:
    """CSV 里裁判的三列；j 可以是 Judgement，也可以是 eval_runs.details 里的 judge 字典。"""
    if blind:
        return {"judge_correctness": "", "judge_refused": "", "judge_citations": ""}
    if isinstance(j, Judgement):
        correctness, refused = j.correctness, j.refused
        cites = {str(k): v for k, v in j.citations.items()}
    else:
        correctness, refused, cites = j["correctness"], j["refused"], j["citations"]
    return {
        "judge_correctness": correctness,
        "judge_refused": "是" if refused else "否",
        "judge_citations": "、".join(
            f"[{k}]{'有依据' if v else '无依据'}" for k, v in cites.items()
        ),
    }


def with_judge(
    rows: Sequence[dict[str, str]], details: Sequence[dict[str, Any]]
) -> list[dict[str, str]]:
    """人工标注 + 某次运行的裁判结论（按题目 id 对上）。同一批人工标注可以和不同版本的裁判比。"""
    by_id = {d["id"]: d for d in details}
    missing = [r["id"] for r in rows if r["id"] not in by_id]
    if missing:
        raise ValueError("这次运行里没有这些题：" + "、".join(missing))
    return [{**r, **judge_columns(by_id[r["id"]]["judge"])} for r in rows]


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
