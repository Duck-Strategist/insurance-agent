"""检索评测：同一评测集上跑三种检索配置（spec §5.3），结果写入 eval_runs。

- 范围（scope）：document = 只在题目对应的条款里检索（对话中由代码先定位产品，这是线上的真实情况）；
  global = 不限条款（衡量跨产品时会不会串）。
- 每题的查询向量只算一次，三种配置共用：配置之间的差别只来自召回与重排，不受向量化接口抖动影响。
  向量化耗时单独记录，总延迟 = 向量化 + 检索器内部的墙钟时间（召回、重排、取父块）。
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import RetrievalSettings
from app.eval import metrics as M
from app.eval.dataset import RetrievalItem
from app.models import Document, EvalRun, Product
from app.rag.retriever import Retriever, SearchMode

Scope = Literal["document", "global"]
EVAL_TOP_K = 10  # 评测取前 10 个父块，用于 MRR@10；Recall@1/3/5 取其前缀


@dataclass
class ItemResult:
    item: RetrievalItem
    rank: int | None
    timings: dict[str, float]
    retrieved: list[tuple[str, list[str]]]  # (条款, 章节路径)，按名次
    query: str  # 实际检索用的查询（开启改写时与原问题不同）

    def as_detail(self) -> dict[str, Any]:
        return {
            "id": self.item.id,
            "question": self.item.question,
            **({"query": self.query} if self.query != self.item.question else {}),
            "file_name": self.item.file_name,
            "gold": self.item.gold,
            "rank": self.rank,
            "timings": self.timings,
            "retrieved": [{"doc": PurePath(f).stem, "path": p} for f, p in self.retrieved[:5]],
        }


async def document_ids_by_name(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, int]:
    async with session_factory() as session:
        rows = await session.execute(
            select(Document.file_name, Document.id).where(Document.indexed_hash.is_not(None))
        )
        return {name: doc_id for name, doc_id in rows.tuples().all()}


async def evaluate(
    retriever: Retriever,
    items: list[RetrievalItem],
    modes: list[SearchMode],
    scope: Scope,
    doc_ids: dict[str, int],
    vectors: dict[str, list[float]],
    embed_ms: dict[str, float],
    queries: dict[str, str] | None = None,
    alt_vectors: dict[str, list[float]] | None = None,
) -> dict[SearchMode, list[ItemResult]]:
    """queries：题目 id → 检索用的查询（查询改写的结果）；不传则用原问题。
    alt_vectors：给出时把原问题作为补充查询一起召回（与线上对话一致，spec D39）。"""
    missing = sorted({i.file_name for i in items} - doc_ids.keys())
    if missing:
        raise ValueError(f"评测集引用的条款未入库：{missing}")
    out: dict[SearchMode, list[ItemResult]] = {m: [] for m in modes}
    for item in items:
        for mode in modes:
            query = (queries or {}).get(item.id, item.question)
            res = await retriever.search(
                query,
                mode=mode,
                document_ids=[doc_ids[item.file_name]] if scope == "document" else None,
                top_k=EVAL_TOP_K,
                query_vector=vectors[item.id],
                alt_queries=[item.question] if alt_vectors is not None else (),
                alt_vectors=[alt_vectors[item.id]] if alt_vectors is not None else None,
            )
            # 查询向量是预先算好传进去的：检索器内部的 embed_ms 约为 0，
            # 换成预先测得的向量化耗时，总耗时 = 向量化 + 检索器内部的墙钟时间（不含向量化）
            timings = {**res.timings, "embed_ms": embed_ms[item.id]}
            timings["total_ms"] = round(
                embed_ms[item.id] + res.timings["total_ms"] - res.timings["embed_ms"], 1
            )
            retrieved = [(h.file_name, h.section_path) for h in res.hits]
            rank = M.first_hit_rank([(f, tuple(p)) for f, p in retrieved], item.gold_keys())  # type: ignore[arg-type]
            out[mode].append(ItemResult(item, rank, timings, retrieved, query))
    return out


def summarize(results: list[ItemResult]) -> dict[str, Any]:
    ranks = [r.rank for r in results]

    def lat(key: str) -> dict[str, float]:
        values = [r.timings[key] for r in results if key in r.timings]
        if not values:
            return {}
        return {
            "p50": round(M.percentile(values, 50), 1),
            "p95": round(M.percentile(values, 95), 1),
        }

    by_doc: dict[str, list[int | None]] = defaultdict(list)
    for r in results:
        by_doc[PurePath(r.item.file_name).stem].append(r.rank)
    return {
        "n": len(results),
        "recall@1": round(M.recall_at(ranks, 1), 4),
        "recall@3": round(M.recall_at(ranks, 3), 4),
        "recall@5": round(M.recall_at(ranks, 5), 4),
        "recall@10": round(M.recall_at(ranks, 10), 4),
        "mrr@10": round(M.mrr(ranks, 10), 4),
        "latency_ms": {
            "total": lat("total_ms"),
            "embed": lat("embed_ms"),
            "recall": lat("recall_ms"),
            "rerank": lat("rerank_ms"),
            "load": lat("load_ms"),
        },
        "recall@5_by_doc": {
            doc: {"n": len(rs), "recall@5": round(M.recall_at(rs, 5), 4)}
            for doc, rs in sorted(by_doc.items())
        },
    }


async def save_runs(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    dataset: str,
    dataset_hash: str,
    scope: Scope,
    settings: RetrievalSettings,
    extra_config: dict[str, Any],
    results: dict[SearchMode, list[ItemResult]],
) -> dict[SearchMode, tuple[int, dict[str, Any]]]:
    saved: dict[SearchMode, tuple[int, dict[str, Any]]] = {}
    async with session_factory() as session, session.begin():
        for mode, rs in results.items():
            metrics = summarize(rs)
            run = EvalRun(
                suite="retrieval",
                dataset=dataset,
                dataset_hash=dataset_hash,
                config={
                    "mode": mode.value,
                    "scope": scope,
                    **settings.model_dump(),
                    **extra_config,
                },
                metrics=metrics,
                details=[r.as_detail() for r in rs],
            )
            session.add(run)
            await session.flush()
            saved[mode] = (run.id, metrics)
    return saved


async def product_names_by_file(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, list[str]]:
    """条款文件名 → 使用这份条款的产品名（改写提示词里要写产品名，和线上一致）。"""
    async with session_factory() as session:
        rows = await session.execute(
            select(Product.clause_file, Product.name).where(Product.clause_file.is_not(None))
        )
        names: dict[str, list[str]] = defaultdict(list)
        for file_name, name in rows.tuples().all():
            if file_name:
                names[file_name].append(name)
        return dict(names)
