"""检索：RRF、查询分词、重排客户端、评测指标的单测，以及检索器的数据库集成测试（不访问网络）。"""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import RetrievalSettings
from app.eval import metrics as M
from app.eval.generate import Section, evidence_in_section, sample_evenly
from app.models import Chunk, ChunkLevel, Document
from app.models.document import EMBEDDING_DIM
from app.rag import reranker as reranker_module
from app.rag.reranker import DashScopeReranker, RerankAuthError, RerankError, RerankResult
from app.rag.retriever import Retriever, SearchMode, bm25_idf, query_terms, rrf_fuse
from app.rag.tokenizer import tokenize

# ---- 纯函数 ----


def test_rrf_rewards_items_ranked_high_in_both_lists() -> None:
    fused = rrf_fuse([[1, 2, 3], [3, 1, 4]], k=60)
    ids = [i for i, _ in fused]
    # 1 在两路都靠前，排第一；3 两路都有，排在只出现一次的 2 前面
    assert ids[:3] == [1, 3, 2]
    assert ids[-1] == 4


def test_rrf_weight_reduces_influence_of_a_list() -> None:
    # 向量一路认为 1 最相关，全文一路认为 2 最相关；降低全文权重后 1 胜出
    assert rrf_fuse([[1, 2], [2, 1]], k=60, weights=[1.0, 1.0])[0][1] == pytest.approx(
        rrf_fuse([[1, 2], [2, 1]], k=60, weights=[1.0, 1.0])[1][1]
    )
    assert rrf_fuse([[1, 2], [2, 1]], k=60, weights=[1.0, 0.5])[0][0] == 1
    assert rrf_fuse([[1, 2], [2, 1]], k=60, weights=[0.5, 1.0])[0][0] == 2


def test_query_terms_dedupes_and_strips_tsquery_operators() -> None:
    terms = query_terms("等待期多久？等待期！a|b & c:*")
    assert terms.count("等待期") == 1
    assert all(ch not in "".join(terms) for ch in "|&:*!？")


def test_idf_decreases_with_document_frequency() -> None:
    assert bm25_idf(1, 1000) > bm25_idf(100, 1000) > bm25_idf(900, 1000) > 0


def test_metrics() -> None:
    gold = {"b"}
    assert M.first_hit_rank(["a", "b", "c"], gold) == 2
    assert M.first_hit_rank(["a", "c"], gold) is None
    ranks = [1, 2, None, 6]
    assert M.recall_at(ranks, 1) == 0.25
    assert M.recall_at(ranks, 5) == 0.5
    assert M.mrr(ranks, 5) == pytest.approx((1 + 0.5) / 4)
    assert M.mrr(ranks, 10) == pytest.approx((1 + 0.5 + 1 / 6) / 4)
    assert M.percentile([1, 2, 3, 4], 50) == 2.5
    assert M.percentile([10.0], 95) == 10.0
    assert M.longest_common_substring("等待期是多久", "本合同等待期为90天") == 3
    assert M.longest_common_substring("", "x") == 0


def test_evidence_check_ignores_whitespace_and_table_tags() -> None:
    content = "<table><tr><td>等待期</td><td>90 天</td></tr></table>\n本合同 犹豫期为15日。"
    assert evidence_in_section("本合同犹豫期为15日", content)
    assert evidence_in_section("等待期90天", content)
    assert not evidence_in_section("等待期为180天", content)
    assert not evidence_in_section("犹豫期", content)  # 太短，没有区分度


def test_sample_evenly_covers_whole_document() -> None:
    body = "正" * 100
    sections = [Section("a.pdf", [f"第{i}条"], body, i) for i in range(10)]
    sections.append(Section("a.pdf", ["空"], "空", 99))  # 正文太短，不参与抽样
    picked = sample_evenly(sections, 3)
    assert [s.first_seq for s in picked] == [0, 4, 9]  # 头、中、尾


# ---- 重排客户端 ----


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(reranker_module.asyncio, "sleep", instant)


def make_reranker(handler) -> DashScopeReranker:  # type: ignore[no-untyped-def]
    return DashScopeReranker(
        "test-key", base_url="https://example.test/api/v1", transport=httpx.MockTransport(handler)
    )


async def test_reranker_sorts_by_score_and_requests_all_documents() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path.endswith("/text-rerank/text-rerank")
        assert body["parameters"]["top_n"] == 3
        results = [
            {"index": 0, "relevance_score": 0.1},
            {"index": 2, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.5},
        ]
        return httpx.Response(200, json={"output": {"results": results}})

    r = make_reranker(handler)
    assert await r.rerank("q", ["a", "b", "c"]) == [
        RerankResult(2, 0.9),
        RerankResult(1, 0.5),
        RerankResult(0, 0.1),
    ]
    await r.aclose()


async def test_reranker_auth_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401)

    with pytest.raises(RerankAuthError):
        await make_reranker(handler).rerank("q", ["a"])
    assert calls == 1


async def test_reranker_rejects_out_of_range_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"output": {"results": [{"index": 5, "relevance_score": 1.0}]}}
        )

    with pytest.raises(RerankError, match="越界"):
        await make_reranker(handler).rerank("q", ["a"])


async def test_reranker_skips_call_for_empty_candidates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("不应发请求")

    assert await make_reranker(handler).rerank("q", []) == []


# ---- 检索器（数据库集成） ----


def onehot(i: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[i] = 1.0
    return v


class FakeEmbedder:
    dim = EMBEDDING_DIM

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vectors[t] for t in texts]


class ReverseReranker:
    """把候选倒过来排，用来确认最终顺序来自重排而不是召回。"""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def rerank(self, query: str, documents: list[str]) -> list[RerankResult]:
        self.seen = documents
        n = len(documents)
        return [RerankResult(i, float(i) / n) for i in reversed(range(n))]


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.connect() as conn:
        outer = await conn.begin()
        yield async_sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        await outer.rollback()


async def add_doc(
    session: AsyncSession, name: str, sections: list[tuple[str, list[tuple[str, int]]]], *, indexed
) -> Document:
    """sections: [(章节标题, [(子块正文, 向量维度下标), ...]), ...]"""
    doc = Document(file_name=name, file_hash="h", indexed_hash="h" if indexed else None)
    session.add(doc)
    await session.flush()
    seq = 0
    for title, children in sections:
        parent = Chunk(
            document_id=doc.id,
            level=ChunkLevel.PARENT,
            seq=seq,
            section_path=[title],
            content=f"{title}\n" + "\n".join(c for c, _ in children),
        )
        session.add(parent)
        await session.flush()
        for content, dim in children:
            seq += 1
            session.add(
                Chunk(
                    document_id=doc.id,
                    parent_id=parent.id,
                    level=ChunkLevel.CHILD,
                    seq=seq,
                    section_path=[title],
                    content=content,
                    embedding=onehot(dim),
                    tsv=func.to_tsvector("simple", tokenize(f"{name}｜{title}\n{content}")),
                )
            )
        seq += 1
    await session.flush()
    return doc


@pytest.fixture
async def docs(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with session_factory() as s, s.begin():
        a = await add_doc(
            s,
            "检索测试甲.pdf",
            [
                # 同一父块两个子块都与查询向量接近：结果里只能出现一次
                ("第一条 等待期", [("甲甲等待期为九十天。", 1), ("甲甲等待期内确诊不赔。", 1)]),
                ("第二条 犹豫期", [("甲甲犹豫期十五日。", 2)]),
                # 向量离得远，但含查询里的稀有词，只有全文召回能找到
                ("第三条 宽限期", [("甲甲宽限期六十日，稀有词嘎嘣脆。", 900)]),
            ],
            indexed=True,
        )
        b = await add_doc(
            s, "检索测试乙.pdf", [("第一条 等待期", [("乙乙等待期一百八十天。", 1)])], indexed=True
        )
        c = await add_doc(
            s, "检索测试丙.pdf", [("第一条 等待期", [("丙丙从未入库成功。", 1)])], indexed=False
        )
    return {"a": a.id, "b": b.id, "c": c.id}


def make_retriever(
    factory: async_sessionmaker[AsyncSession], reranker: ReverseReranker | None = None
) -> Retriever:
    near = [0.0] * EMBEDDING_DIM
    near[1], near[2] = 0.9, 0.4  # 最接近 1 号维度，其次 2 号
    embedder = FakeEmbedder({"等待期 嘎嘣脆": near})
    return Retriever(factory, embedder, reranker, RetrievalSettings(recall_k=3))


async def test_vector_returns_parent_level_hits_deduplicated(
    session_factory: async_sessionmaker[AsyncSession], docs: dict[str, int]
) -> None:
    res = await make_retriever(session_factory).search(
        "等待期 嘎嘣脆", mode=SearchMode.VECTOR, document_ids=[docs["a"]]
    )
    paths = [h.section_path for h in res.hits]
    assert paths[:2] == [["第一条 等待期"], ["第二条 犹豫期"]]
    assert len(paths) == len({tuple(p) for p in paths})  # 每个父块只出现一次
    assert res.hits[0].parent_content.startswith("第一条 等待期")
    assert res.hits[0].doc_title == "检索测试甲"
    assert {"embed_ms", "recall_ms", "total_ms"} <= res.timings.keys()


async def test_document_filter_and_unindexed_documents(
    session_factory: async_sessionmaker[AsyncSession], docs: dict[str, int]
) -> None:
    r = make_retriever(session_factory)
    only_b = await r.search("等待期 嘎嘣脆", mode=SearchMode.VECTOR, document_ids=[docs["b"]])
    assert {h.document_id for h in only_b.hits} == {docs["b"]}
    # 丙没有 indexed_hash：即使显式指定也查不到（可检索看 indexed_hash，不看 status）
    only_c = await r.search("等待期 嘎嘣脆", mode=SearchMode.HYBRID, document_ids=[docs["c"]])
    assert only_c.hits == []
    # 空列表表示没有可查的条款，不能退化成"不限条款"
    assert (await r.search("等待期 嘎嘣脆", document_ids=[])).hits == []


async def test_hybrid_finds_rare_term_that_vector_misses(
    session_factory: async_sessionmaker[AsyncSession], docs: dict[str, int]
) -> None:
    r = make_retriever(session_factory)
    vec = await r.search("等待期 嘎嘣脆", mode=SearchMode.VECTOR, document_ids=[docs["a"]], top_k=2)
    assert ["第三条 宽限期"] not in [h.section_path for h in vec.hits]
    hyb = await r.search("等待期 嘎嘣脆", mode=SearchMode.HYBRID, document_ids=[docs["a"]], top_k=2)
    assert ["第三条 宽限期"] in [h.section_path for h in hyb.hits]
    assert hyb.fulltext_ids  # 全文一路确实召回了


async def test_rerank_decides_final_order_and_sees_section_prefix(
    session_factory: async_sessionmaker[AsyncSession], docs: dict[str, int]
) -> None:
    reranker = ReverseReranker()
    r = make_retriever(session_factory, reranker)
    hyb = await r.search("等待期 嘎嘣脆", mode=SearchMode.HYBRID, document_ids=[docs["a"]])
    rr = await r.search("等待期 嘎嘣脆", mode=SearchMode.HYBRID_RERANK, document_ids=[docs["a"]])
    assert [h.section_path for h in rr.hits] != [h.section_path for h in hyb.hits]
    assert "rerank_ms" in rr.timings
    # 送去重排的文本带「条款名｜章节路径」前缀，与入库时的向量化文本一致
    assert all(d.startswith("检索测试甲｜第") for d in reranker.seen)


async def test_rerank_mode_requires_reranker(
    session_factory: async_sessionmaker[AsyncSession], docs: dict[str, int]
) -> None:
    with pytest.raises(ValueError, match="重排"):
        await make_retriever(session_factory).search(
            "等待期 嘎嘣脆", mode=SearchMode.HYBRID_RERANK, document_ids=[docs["a"]]
        )


async def test_document_reindexed_during_rerank_does_not_crash(
    session_factory: async_sessionmaker[AsyncSession], docs: dict[str, int]
) -> None:
    """重排期间文档被重新入库（旧块被删）：返回剩下的结果，而不是 KeyError。"""

    class DeletingReranker(ReverseReranker):
        async def rerank(self, query: str, documents: list[str]) -> list[RerankResult]:
            async with session_factory() as s, s.begin():
                await s.execute(delete(Chunk).where(Chunk.document_id == docs["a"]))
            return await super().rerank(query, documents)

    r = make_retriever(session_factory, DeletingReranker())
    res = await r.search("等待期 嘎嘣脆", mode=SearchMode.HYBRID_RERANK, document_ids=[docs["a"]])
    assert res.hits == []


async def test_terms_missing_from_cached_stats_still_recall(
    session_factory: async_sessionmaker[AsyncSession], docs: dict[str, int]
) -> None:
    """词频缓存之后才入库的条款：它独有的词不在缓存里，也必须能被全文召回（不等缓存过期）。"""
    r = make_retriever(session_factory)
    await r.search("等待期 嘎嘣脆", mode=SearchMode.HYBRID, document_ids=[docs["a"]])  # 加载缓存
    async with session_factory() as s, s.begin():
        new = await add_doc(
            s, "检索测试丁.pdf", [("第一条 新词", [("丁丁独有词咕噜噜。", 700)])], indexed=True
        )
    r._embedder = FakeEmbedder({"咕噜噜": onehot(5)})  # type: ignore[assignment]
    res = await r.search("咕噜噜", mode=SearchMode.HYBRID, document_ids=[new.id])
    assert res.fulltext_ids, "缓存里没有的词被丢掉了"


async def test_invalidate_term_stats_forces_reload(
    session_factory: async_sessionmaker[AsyncSession], docs: dict[str, int]
) -> None:
    r = make_retriever(session_factory)
    await r.search("等待期 嘎嘣脆", mode=SearchMode.HYBRID, document_ids=[docs["a"]])
    assert r._df_loaded_at is not None
    r.invalidate_term_stats()
    assert r._df_loaded_at is None
    await r.search("等待期 嘎嘣脆", mode=SearchMode.HYBRID, document_ids=[docs["a"]])
    assert r._df_loaded_at is not None


async def test_total_is_wall_clock_including_parent_load(
    session_factory: async_sessionmaker[AsyncSession], docs: dict[str, int]
) -> None:
    res = await make_retriever(session_factory, ReverseReranker()).search(
        "等待期 嘎嘣脆", mode=SearchMode.HYBRID_RERANK, document_ids=[docs["a"]]
    )
    t = res.timings
    assert "load_ms" in t
    stages = t["embed_ms"] + t["recall_ms"] + t["rerank_ms"] + t["load_ms"]
    assert t["total_ms"] >= stages - 0.5  # 各阶段四舍五入到 0.1 ms


# ---- 评测命令行 ----


async def test_generation_auth_error_stops_batch_and_cancels_siblings() -> None:
    """第一题 401 时立即停止：其余仍在等待响应的请求被取消，而不是继续跑到客户端被关闭。"""
    import asyncio

    import httpx as _httpx

    from app.eval.generate import GenerationAuthError, QuestionGenerator, generate_items

    calls = 0
    cancelled = 0
    never = asyncio.Event()

    async def handler(request: _httpx.Request) -> _httpx.Response:
        nonlocal calls, cancelled
        calls += 1
        if calls == 1:
            return _httpx.Response(401, text="invalid key")
        try:
            # 模拟慢请求，只有被取消才会结束（no_sleep 夹具让 asyncio.sleep 立即返回，不能用它）
            await never.wait()
        except asyncio.CancelledError:
            cancelled += 1
            raise
        return _httpx.Response(200)

    gen = QuestionGenerator(
        "k", base_url="https://example.test/v1", model="m", transport=_httpx.MockTransport(handler)
    )
    sections = [Section("a.pdf", [f"第{i}条"], "正" * 100, i) for i in range(20)]
    with pytest.raises(GenerationAuthError):
        async with asyncio.timeout(5):
            await generate_items(sections, gen)
    await gen.aclose()
    # 并发上限 5：第一题释放名额时可能还有 1 个排队任务抢先发出请求；
    # 关键是除第一题外的所有请求都被取消，其余排队任务从未发出
    assert calls <= 6
    assert cancelled == calls - 1


async def test_empty_dataset_rejected_before_running(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.eval.__main__ import cmd_retrieval

    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    assert await cmd_retrieval([empty], [SearchMode.VECTOR], "document", None) == 2


def test_default_reports_dir_falls_back_to_data_dir(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import RagSettings
    from app.eval.__main__ import default_reports_dir

    data = tmp_path / "data"
    data.mkdir()
    # 没有仓库的 eval/ 目录（容器里）：写到数据目录下
    assert default_reports_dir(RagSettings(data_dir=data)) == data / "eval" / "reports"
    (tmp_path / "eval").mkdir()
    assert default_reports_dir(RagSettings(data_dir=data)) == tmp_path / "eval" / "reports"


async def test_alt_query_recall_is_fused_and_rerank_uses_main_query(
    session_factory: async_sessionmaker[AsyncSession], docs: dict[str, int]
) -> None:
    """补充查询（原问题）召回到的块也进入候选；重排只用主查询（改写后的查询）。"""
    seen_queries: list[str] = []

    class RecordingReranker(ReverseReranker):
        async def rerank(self, query: str, documents: list[str]) -> list[RerankResult]:
            seen_queries.append(query)
            return await super().rerank(query, documents)

    near = [0.0] * EMBEDDING_DIM
    near[1] = 1.0
    r = Retriever(
        session_factory,
        # 一次请求同时算主查询和补充查询：顺序必须与传入一致
        FakeEmbedder({"等待期": near, "嘎嘣脆": onehot(900)}),
        RecordingReranker(),
        RetrievalSettings(recall_k=1),
    )
    main_only = await r.search("等待期", mode=SearchMode.HYBRID, document_ids=[docs["a"]])
    assert ["第三条 宽限期"] not in [h.section_path for h in main_only.hits]

    both = await r.search(
        "等待期", mode=SearchMode.HYBRID_RERANK, document_ids=[docs["a"]], alt_queries=["嘎嘣脆"]
    )
    assert ["第三条 宽限期"] in [h.section_path for h in both.hits]
    assert seen_queries == ["等待期"]
    # 与主查询相同的补充查询被忽略，不重复召回
    same = await r.search(
        "等待期", mode=SearchMode.VECTOR, document_ids=[docs["a"]], alt_queries=["等待期"]
    )
    assert [h.section_path for h in same.hits] == [h.section_path for h in main_only.hits][:1]
