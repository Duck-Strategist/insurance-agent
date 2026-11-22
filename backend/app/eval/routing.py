"""路由评测（spec §8）：标注意图集上跑线上同一个路由函数（graph.classify + resolve_products），
算准确率、各意图的精确率 / 召回率 / F1、混淆矩阵和产品识别准确率，写入 eval_runs。

评测集分两份（spec Q16 的教训）：
- routing_dev.jsonl：开发集，调路由提示词时可以看、可以拿来对照；
- routing_test.jsonl：测试集，只用来报告数字，看过的错题不能回流到提示词里。
"""

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import (
    INTENTS,
    ProductRef,
    RouteDecision,
    classify,
    format_history,
    resolve_products,
)
from app.eval import metrics as M
from app.eval.retry import with_retries
from app.models import EvalRun

# 混淆矩阵的行列顺序
LABELS = ["clause_qa", "advisor", "claim", "chitchat", "other"]
assert set(LABELS) == INTENTS
# 只有这两个业务用到产品：产品识别准确率只在这两类题上统计
PRODUCT_INTENTS = ("clause_qa", "claim")


class RoutingItem(BaseModel):
    id: str
    question: str
    intent: Literal["clause_qa", "advisor", "claim", "chitchat", "other"]
    products: list[str] = Field(default_factory=list)  # 路由后应得到的产品（名称）
    history: list[tuple[Literal["user", "assistant"], str]] = Field(default_factory=list)
    last_workflow: str | None = None
    last_products: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


def load_items(path: Path) -> list[RoutingItem]:
    items = [
        RoutingItem.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [i.id for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path.name} 里有重复的题目 id")
    return items


def unknown_products(items: Sequence[RoutingItem], catalog: Sequence[ProductRef]) -> list[str]:
    """评测集里写的产品名必须都在产品目录里，否则产品准确率会悄悄变低。"""
    names = {p["name"] for p in catalog}
    return sorted(
        {f"{i.id}: {n}" for i in items for n in (*i.products, *i.last_products) if n not in names}
    )


def messages_of(item: RoutingItem) -> list[AnyMessage]:
    msgs: list[AnyMessage] = [
        HumanMessage(content=text) if role == "user" else AIMessage(content=text)
        for role, text in item.history
    ]
    return [*msgs, HumanMessage(content=item.question)]


@dataclass
class RouteResult:
    item: RoutingItem
    intent: str
    products: list[str]
    parsed: bool
    latency_ms: float

    @property
    def intent_ok(self) -> bool:
        return self.intent == self.item.intent

    @property
    def products_ok(self) -> bool:
        return sorted(self.products) == sorted(self.item.products)

    def as_detail(self) -> dict[str, Any]:
        return {
            "id": self.item.id,
            "question": self.item.question,
            "gold": self.item.intent,
            "pred": self.intent,
            "gold_products": self.item.products,
            "pred_products": self.products,
            "parsed": self.parsed,
            "multi_turn": bool(self.item.history),
            "tags": self.item.tags,
            "latency_ms": self.latency_ms,
        }


async def run_items(
    json_llm: BaseChatModel,
    catalog: Sequence[ProductRef],
    items: Sequence[RoutingItem],
    *,
    history_turns: int,
    concurrency: int = 5,
    config: RunnableConfig | None = None,
) -> list[RouteResult]:
    by_name = {p["name"]: p["id"] for p in catalog}
    by_id = {p["id"]: p["name"] for p in catalog}
    sem = asyncio.Semaphore(concurrency)

    async def one(item: RoutingItem) -> RouteResult:
        last_products = [by_name[n] for n in item.last_products]

        async def call() -> tuple[RouteDecision, float]:
            t0 = time.perf_counter()
            decision = await classify(
                json_llm,
                catalog,
                item.question,
                history=format_history(messages_of(item), history_turns),
                last_workflow=item.last_workflow,
                last_products=last_products,
                config=config,
            )
            return decision, round((time.perf_counter() - t0) * 1000, 1)

        async with sem:
            decision, latency = await with_retries(call, label=item.id)
        ids, _ = resolve_products(decision, item.last_workflow, last_products)
        return RouteResult(item, decision.intent, [by_id[i] for i in ids], decision.parsed, latency)

    return list(await asyncio.gather(*(one(i) for i in items)))


def summarize(results: Sequence[RouteResult]) -> dict[str, Any]:
    gold = [r.item.intent for r in results]
    pred = [r.intent for r in results]
    matrix = M.confusion_matrix(gold, pred, LABELS)
    scores = M.per_label_scores(matrix)

    def acc(rs: Sequence[RouteResult]) -> float | None:
        return round(sum(r.intent_ok for r in rs) / len(rs), 4) if rs else None

    product_items = [r for r in results if r.item.intent in PRODUCT_INTENTS]
    latencies = [r.latency_ms for r in results]
    return {
        "n": len(results),
        "accuracy": acc(results),
        "macro_f1": M.macro_f1(scores),
        "multi_turn": {
            "n": sum(bool(r.item.history) for r in results),
            "accuracy": acc([r for r in results if r.item.history]),
        },
        "single_turn_accuracy": acc([r for r in results if not r.item.history]),
        # 条款问答、理赔题：路由后（含沿用上一轮）的产品集合与标注完全一致
        "product_accuracy": (
            round(sum(r.products_ok for r in product_items) / len(product_items), 4)
            if product_items
            else None
        ),
        "product_n": len(product_items),
        "unparsed": sum(not r.parsed for r in results),
        "per_intent": scores,
        "confusion": matrix,
        "latency_ms": {
            "p50": round(M.percentile(latencies, 50), 1),
            "p95": round(M.percentile(latencies, 95), 1),
        },
    }


async def save_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    dataset: str,
    dataset_hash: str,
    config: dict[str, Any],
    results: Sequence[RouteResult],
) -> tuple[int, dict[str, Any]]:
    metrics = summarize(results)
    async with session_factory() as session, session.begin():
        run = EvalRun(
            suite="routing",
            dataset=dataset,
            dataset_hash=dataset_hash,
            config=config,
            metrics=metrics,
            details=[r.as_detail() for r in results],
        )
        session.add(run)
        await session.flush()
        return run.id, metrics


def render(name: str, run_id: int, m: dict[str, Any], results: Sequence[RouteResult]) -> list[str]:
    """Markdown 报告：总体指标、混淆矩阵、各意图分数、错题清单。"""
    lines = [
        f"## {name}（{m['n']} 题，eval_run #{run_id}）",
        "",
        f"- 意图准确率 **{m['accuracy']:.3f}**，宏平均 F1 {m['macro_f1']:.3f}；"
        f"单轮 {m['single_turn_accuracy']:.3f}，多轮 {m['multi_turn']['accuracy']:.3f}"
        f"（{m['multi_turn']['n']} 题）",
        f"- 产品识别准确率（条款问答、理赔 {m['product_n']} 题）{m['product_accuracy']:.3f}；"
        f"模型输出无法解析 {m['unparsed']} 次",
        f"- 单次路由延迟 P50 {m['latency_ms']['p50']} ms，P95 {m['latency_ms']['p95']} ms",
        "",
        "混淆矩阵（行：标准，列：预测）",
        "",
        "| | " + " | ".join(LABELS) + " |",
        "|---" * (len(LABELS) + 1) + "|",
    ]
    for g in LABELS:
        lines.append(f"| **{g}** | " + " | ".join(str(m["confusion"][g][p]) for p in LABELS) + " |")
    lines += ["", "| 意图 | 精确率 | 召回率 | F1 | 题数 |", "|---|---|---|---|---|"]
    for label, s in m["per_intent"].items():
        lines.append(
            f"| {label} | {s['precision']:.3f} | {s['recall']:.3f} | {s['f1']:.3f} "
            f"| {s['support']} |"
        )
    wrong = [r for r in results if not r.intent_ok]
    lines += ["", f"### 意图错误（{len(wrong)}）", ""]
    for r in wrong:
        ctx = f"（上一轮 {r.item.last_workflow}）" if r.item.last_workflow else ""
        lines.append(f"- `{r.item.id}` {r.item.intent} → **{r.intent}**{ctx}：{r.item.question}")
    bad_products = [r for r in results if r.item.intent in PRODUCT_INTENTS and not r.products_ok]
    lines += ["", f"### 产品不一致（{len(bad_products)}）", ""]
    for r in bad_products:
        lines.append(
            f"- `{r.item.id}` 标准 {r.item.products or '无'} → 预测 {r.products or '无'}："
            f"{r.item.question}"
        )
    lines.append("")
    return lines
