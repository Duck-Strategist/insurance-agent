"""条款检索：向量召回 + 全文召回 → RRF 融合 → 重排 → 按父块去重。

三种模式用于对照实验（spec §5.3）：
- vector：只用 pgvector 余弦相似度；
- hybrid：向量 + 全文两路召回，用 RRF 融合；
- hybrid_rerank：在 hybrid 的候选上再调用重排模型。

返回的是**父块**级别的结果：命中的子块回溯到父块（章节正文），同一父块只保留得分最高的子块。
k、召回条数等参数由代码固定（RetrievalSettings），不交给 LLM（spec §6.3）。
"""

import math
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePath

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import RetrievalSettings
from app.rag.embedder import Embedder
from app.rag.reranker import Reranker
from app.rag.tokenizer import tokenize


class SearchMode(StrEnum):
    VECTOR = "vector"
    HYBRID = "hybrid"
    HYBRID_RERANK = "hybrid_rerank"


@dataclass
class Hit:
    parent_id: int
    child_id: int  # 父块内得分最高的子块
    document_id: int
    file_name: str
    section_path: list[str]
    snippet: str  # 子块原文（命中片段）
    parent_content: str  # 父块原文（回答上下文）
    score: float

    @property
    def doc_title(self) -> str:
        return PurePath(self.file_name).stem


@dataclass
class SearchResult:
    hits: list[Hit]
    # 各阶段耗时（毫秒），用于评测里的延迟拆分
    timings: dict[str, float] = field(default_factory=dict)
    # 调试信息：每一路召回的子块 id（按名次）
    vector_ids: list[int] = field(default_factory=list)
    fulltext_ids: list[int] = field(default_factory=list)


def rrf_fuse(
    rankings: Sequence[Sequence[int]], k: int, weights: Sequence[float] | None = None
) -> list[tuple[int, float]]:
    """加权 Reciprocal Rank Fusion：score = Σ w_i / (k + rank_i)，rank 从 1 开始。

    只用名次、不用原始分数，所以两路分数量纲不同（余弦距离 vs 词频）也能直接融合。
    """
    weights = weights or [1.0] * len(rankings)
    scores: dict[int, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


# tsquery 里有特殊含义的字符都去掉；分词结果本身只含字母数字和汉字，这里是兜底
_UNSAFE = re.compile(r"[^\w一-鿿]")


def query_terms(query: str) -> list[str]:
    """查询分词（与入库共用 tokenize），去重并保持顺序。"""
    seen: dict[str, None] = {}
    for word in tokenize(query).split():
        clean = _UNSAFE.sub("", word)
        if clean:
            seen.setdefault(clean, None)
    return list(seen)


def bm25_idf(df: int, n: int) -> float:
    return math.log((n - df + 0.5) / (df + 0.5) + 1.0)


class Retriever:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: Embedder,
        reranker: Reranker | None,
        settings: RetrievalSettings,
    ) -> None:
        self._sessions = session_factory
        self._embedder = embedder
        self._reranker = reranker
        self._settings = settings
        # 词的文档频率（含有该词的子块数），算 IDF 用；入库后会变，定时刷新
        self._df: dict[str, int] = {}
        self._df_total = 0
        self._df_loaded_at = 0.0

    async def search(
        self,
        query: str,
        *,
        mode: SearchMode = SearchMode.HYBRID_RERANK,
        document_ids: Sequence[int] | None = None,
        top_k: int | None = None,
        query_vector: list[float] | None = None,
    ) -> SearchResult:
        """document_ids 为 None 表示不限条款；空列表表示没有可查的条款，直接返回空。"""
        cfg = self._settings
        top_k = top_k or cfg.top_k
        timings: dict[str, float] = {}
        if document_ids is not None and not document_ids:
            return SearchResult([], timings)
        if mode is SearchMode.HYBRID_RERANK and self._reranker is None:
            raise ValueError("未配置重排模型，不能使用 hybrid_rerank")

        t0 = time.perf_counter()
        if query_vector is None:
            query_vector = (await self._embedder.embed([query]))[0]
        timings["embed_ms"] = _ms(t0)

        t0 = time.perf_counter()
        async with self._sessions() as session:
            vector_ids = await self._vector_recall(session, query_vector, document_ids)
            fulltext_ids: list[int] = []
            if mode is SearchMode.VECTOR:
                # 向量距离已经是排序依据；分数用 RRF 同样的公式，便于和其他模式比较
                ranked = [(cid, 1.0 / (cfg.rrf_k + i)) for i, cid in enumerate(vector_ids, 1)]
            else:
                fulltext_ids = await self._fulltext_recall(session, query, document_ids)
                ranked = rrf_fuse([vector_ids, fulltext_ids], cfg.rrf_k, [1.0, cfg.fulltext_weight])
            if mode is SearchMode.HYBRID_RERANK:
                ranked = ranked[: cfg.rerank_candidates]
            rows = await self._load_children(session, [cid for cid, _ in ranked])
            await session.commit()  # 结束只读事务，重排的网络调用期间不占数据库连接
        # 召回和取详情是两条语句（READ COMMITTED）：中间若有重新入库提交，旧块已被删，跳过即可
        ranked = [(cid, score) for cid, score in ranked if cid in rows]
        timings["recall_ms"] = _ms(t0)

        if mode is SearchMode.HYBRID_RERANK:
            assert self._reranker is not None
            t0 = time.perf_counter()
            candidates = [cid for cid, _ in ranked]
            reranked = await self._reranker.rerank(
                query, [_rerank_text(rows[cid]) for cid in candidates]
            )
            timings["rerank_ms"] = _ms(t0)
            ranked = [(candidates[r.index], r.score) for r in reranked]

        # 子块 → 父块去重，保留每个父块第一次（最高分）出现的子块
        best: dict[int, tuple[int, float]] = {}
        for cid, score in ranked:
            pid = rows[cid].parent_id
            if pid not in best:
                best[pid] = (cid, score)
            if len(best) == top_k:
                break
        async with self._sessions() as session:
            parents = await self._load_parents(session, list(best))

        hits = [
            Hit(
                parent_id=pid,
                child_id=cid,
                document_id=rows[cid].document_id,
                file_name=rows[cid].file_name,
                section_path=list(rows[cid].section_path),
                snippet=rows[cid].content,
                parent_content=parents[pid],
                score=score,
            )
            for pid, (cid, score) in best.items()
            if pid in parents  # 同上：重排期间文档被重新入库
        ]
        timings["total_ms"] = sum(v for k, v in timings.items() if k != "total_ms")
        return SearchResult(hits, timings, vector_ids, fulltext_ids)

    async def embed_query(self, query: str) -> list[float]:
        return (await self._embedder.embed([query]))[0]

    # ---- 召回 ----

    async def _vector_recall(
        self, session: AsyncSession, vector: list[float], document_ids: Sequence[int] | None
    ) -> list[int]:
        cfg = self._settings
        # HNSW 是"先找近邻再过滤"：默认只找 ef_search=40 个近邻，加了条款过滤后可能剩下很少几条。
        # pgvector 0.8 的 iterative_scan 会在过滤后不够时继续往下找（relaxed_order：结果可能
        # 略微乱序，所以取回距离后在 Python 里重新排序）。SET LOCAL 只在当前事务内有效。
        await session.execute(text(f"SET LOCAL hnsw.ef_search = {int(cfg.hnsw_ef_search)}"))
        await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        filter_sql = "AND c.document_id = ANY(:doc_ids)" if document_ids is not None else ""
        rows = await session.execute(
            text(
                f"""
                SELECT c.id, c.embedding <=> CAST(:vec AS vector) AS dist
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.level = 'child' AND d.indexed_hash IS NOT NULL {filter_sql}
                ORDER BY dist
                LIMIT :n
                """
            ),
            {
                "vec": _vector_literal(vector),
                "n": cfg.recall_k,
                **({"doc_ids": list(document_ids)} if document_ids is not None else {}),
            },
        )
        return [cid for cid, _ in sorted(rows.all(), key=lambda r: (r[1], r[0]))]

    async def _fulltext_recall(
        self, session: AsyncSession, query: str, document_ids: Sequence[int] | None
    ) -> list[int]:
        """全文召回，按「命中词的 IDF 之和」排序。

        PostgreSQL 自带的 ts_rank 不考虑词的稀有程度：「的」「条款」和「等待期」一样重要，
        而每个子块的前缀里都有条款名，用 ts_rank 时几乎所有块都同分。这里改用 BM25 的 IDF
        给每个命中的词加权（不含词频与长度归一化，子块长度本来就接近）。
        """
        cfg = self._settings
        terms = query_terms(query)
        if not terms:
            return []
        df, total = await self._term_df(session)
        if total == 0:
            return []
        weighted = [
            (t, bm25_idf(df.get(t, 0), total))
            for t in terms
            if df.get(t, 0) / total <= cfg.fulltext_max_df_ratio
        ]
        weighted = [(t, w) for t, w in weighted if df.get(t, 0) > 0]
        if not weighted:
            return []

        params: dict[str, object] = {"n": cfg.recall_k}
        score_parts = []
        for i, (term, weight) in enumerate(weighted):
            params[f"t{i}"] = term
            params[f"w{i}"] = weight
            score_parts.append(f"(c.tsv @@ to_tsquery('simple', :t{i}))::int * :w{i}")
        params["any_q"] = " | ".join(t for t, _ in weighted)
        filter_sql = ""
        if document_ids is not None:
            filter_sql = "AND c.document_id = ANY(:doc_ids)"
            params["doc_ids"] = list(document_ids)
        rows = await session.execute(
            text(
                f"""
                SELECT c.id, ({" + ".join(score_parts)}) AS score
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.level = 'child' AND d.indexed_hash IS NOT NULL {filter_sql}
                  AND c.tsv @@ to_tsquery('simple', :any_q)
                ORDER BY score DESC, c.id
                LIMIT :n
                """
            ),
            params,
        )
        return [cid for cid, _ in rows.all()]

    async def _term_df(self, session: AsyncSession) -> tuple[dict[str, int], int]:
        if time.monotonic() - self._df_loaded_at > self._settings.df_cache_seconds:
            rows = await session.execute(
                text(
                    "SELECT word, ndoc FROM ts_stat("
                    "'SELECT c.tsv FROM chunks c JOIN documents d ON d.id = c.document_id "
                    "WHERE c.level = ''child'' AND d.indexed_hash IS NOT NULL')"
                )
            )
            self._df = {w: n for w, n in rows.all()}
            self._df_total = (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id "
                        "WHERE c.level = 'child' AND d.indexed_hash IS NOT NULL"
                    )
                )
                or 0
            )
            self._df_loaded_at = time.monotonic()
        return self._df, self._df_total

    # ---- 取详情 ----

    @staticmethod
    async def _load_children(session: AsyncSession, ids: list[int]) -> dict[int, "_ChildRow"]:
        if not ids:
            return {}
        rows = await session.execute(
            text(
                """
                SELECT c.id, c.parent_id, c.document_id, d.file_name, c.section_path, c.content
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.id = ANY(:ids)
                """
            ),
            {"ids": ids},
        )
        return {r.id: _ChildRow(*r) for r in rows.all()}

    @staticmethod
    async def _load_parents(session: AsyncSession, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        rows = await session.execute(
            text("SELECT id, content FROM chunks WHERE id = ANY(:ids)"), {"ids": ids}
        )
        return {pid: content for pid, content in rows.all()}


@dataclass
class _ChildRow:
    id: int
    parent_id: int
    document_id: int
    file_name: str
    section_path: list[str]
    content: str


def _rerank_text(row: _ChildRow) -> str:
    # 与入库时的向量化文本一致：带上条款名和章节路径，重排模型才知道这段话讲的是哪一节
    prefix = PurePath(row.file_name).stem
    if row.section_path:
        prefix += "｜" + " > ".join(row.section_path)
    return f"{prefix}\n{row.content}"


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
