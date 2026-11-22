"""对话图：用假 LLM、假检索器和内存 checkpointer 测试路由、追问、改写、带引用生成与流式事件。"""

import json
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agent import prompts
from app.agent.graph import (
    GREETING,
    AgentDeps,
    _as_ids,
    build_graph,
    extract_citations,
    format_history,
    format_sections,
)
from app.agent.llm import parse_json_object
from app.agent.runtime import AgentRuntime, psycopg_conninfo
from app.core.config import LlmSettings
from app.models import Document, Product
from app.rag.reranker import RerankError
from app.rag.retriever import Hit, SearchMode, SearchResult
from app.services.chat_service import make_title, sse, stream_answer

# ---- 纯函数 ----


def test_parse_json_object_tolerates_fences_and_chatter() -> None:
    assert parse_json_object('{"intent": "clause_qa"}') == {"intent": "clause_qa"}
    assert parse_json_object('```json\n{"query": "等待期"}\n```') == {"query": "等待期"}
    assert parse_json_object('好的，结果如下：{"query": "免赔额"} 希望有帮助') == {
        "query": "免赔额"
    }
    assert parse_json_object("不是 JSON") == {}
    assert parse_json_object("[1, 2]") == {}


def test_as_ids_accepts_ints_and_digit_strings_only() -> None:
    assert _as_ids([3, "5", " 7 ", 3]) == [3, 5, 7]
    assert _as_ids([True, 1.5, "达尔文", None]) == []
    assert _as_ids("3") == []


def test_greeting_rule() -> None:
    assert GREETING.match("你好！")
    assert GREETING.match(" Hello ")
    assert not GREETING.match("你好，等待期多久")


def hit(i: int, path: str = "第一条", content: str = "正文") -> Hit:
    return Hit(
        i, i * 10, 1, "某条款.pdf", [path], f"片段{i}", f"{path}\n{content}{i}", 1.0 - i / 10
    )


def test_extract_citations_in_order_dedup_and_drop_out_of_range() -> None:
    hits = [hit(1, "等待期"), hit(2, "免责"), hit(3, "犹豫期")]
    cites = extract_citations("等待期 90 天[2]，免责见[1][2]，另见[7]和[0]。", hits)
    assert [c["index"] for c in cites] == [2, 1]  # 首次出现顺序；[7]、[0] 越界丢弃
    assert cites[0]["section_path"] == ["免责"]
    assert cites[0]["doc_title"] == "某条款"


def test_format_sections_keeps_hit_snippet_when_truncating() -> None:
    long = "甲" * 3000 + "命中片段在这里" + "乙" * 3000
    h = Hit(1, 2, 1, "x.pdf", ["长章节"], "命中片段在这里", long, 1.0)
    out = format_sections([h], max_chars=500)
    assert "[1] 《x》长章节" in out
    assert "命中片段在这里" in out
    assert len(out) < 600


def test_format_history_excludes_latest_question() -> None:
    msgs = [HumanMessage("问1"), AIMessage("答1"), HumanMessage("问2")]
    assert format_history(msgs, turns=3) == "用户：问1\n顾问：答1"
    assert format_history([HumanMessage("只有一句")], turns=3) == "（无）"


def test_misc_helpers() -> None:
    assert (
        psycopg_conninfo("postgresql+asyncpg://u:p%40w@h:5433/db")
        == "postgresql://u:p%40w@h:5433/db"
    )
    assert sse("token", {"text": "你好"}) == 'event: token\ndata: {"text": "你好"}\n\n'
    assert make_title("  等待期\n多久 ") == "等待期 多久"
    assert make_title("长" * 40) == "长" * 30 + "…"


# ---- 图（数据库里放一个测试产品，LLM 和检索器用假实现） ----


class ScriptedLLM(GenericFakeChatModel):
    """按顺序返回预设回复，并记下每次收到的提示词。"""

    prompts_seen: list[str] = []

    def __init__(self, replies: Sequence[str]) -> None:
        super().__init__(messages=iter([AIMessage(r) for r in replies]))
        self.prompts_seen = []

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        self.prompts_seen.append(str(input[-1].content))
        return await super().ainvoke(input, config, **kwargs)


class FakeRetriever:
    def __init__(self, hits: list[Hit], rerank_fails: bool = False) -> None:
        self.hits = hits
        self.rerank_fails = rerank_fails
        self.calls: list[tuple[str, SearchMode, list[int] | None]] = []
        self.alt_queries: list[list[str]] = []

    async def search(
        self,
        query: str,
        *,
        mode: SearchMode,
        document_ids: Sequence[int] | None = None,
        alt_queries: Sequence[str] = (),
    ) -> SearchResult:
        self.calls.append((query, mode, list(document_ids) if document_ids is not None else None))
        self.alt_queries.append(list(alt_queries))
        if self.rerank_fails and mode is SearchMode.HYBRID_RERANK:
            raise RerankError("重排失败 HTTP 500")
        return SearchResult(self.hits, {})


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.connect() as conn:
        outer = await conn.begin()
        yield async_sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        await outer.rollback()


@pytest.fixture
async def products(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """一个有已入库条款的产品，一个条款未入库的产品，返回 {名字: id}，以及条款的 document id。"""
    async with session_factory() as s, s.begin():
        doc = Document(file_name="对话测试条款.pdf", file_hash="h", indexed_hash="h")
        s.add(doc)
        ok = Product(
            name="对话测试医疗险",
            category="medical",
            insurer="测试保险",
            min_premium=Decimal("100"),
            clause_file="对话测试条款.pdf",
        )
        missing = Product(
            name="对话测试未入库险",
            category="accident",
            insurer="测试保险",
            min_premium=Decimal("50"),
            clause_file="不存在的条款.pdf",
        )
        s.add_all([ok, missing])
        await s.flush()
        return {"ok": ok.id, "missing": missing.id, "doc": doc.id}


def make_runtime(
    factory: async_sessionmaker[AsyncSession],
    json_replies: Sequence[str],
    answer_replies: Sequence[str] = (),
    retriever: FakeRetriever | None = None,
) -> tuple[AgentRuntime, ScriptedLLM, ScriptedLLM, FakeRetriever]:
    json_llm, answer_llm = ScriptedLLM(json_replies), ScriptedLLM(answer_replies)
    retr = retriever or FakeRetriever([hit(1, "第三条 等待期"), hit(2, "第九条 责任免除")])
    saver = InMemorySaver()
    graph = build_graph(AgentDeps(factory, retr, json_llm, answer_llm, LlmSettings()), saver)
    return AgentRuntime(graph, saver), json_llm, answer_llm, retr


def route(intent: str, ids: list[Any]) -> str:
    return json.dumps({"intent": intent, "product_ids": ids})


async def ask(rt: AgentRuntime, text: str, thread: str = "t") -> list[tuple[str, dict[str, Any]]]:
    """跑一轮，返回解析后的 SSE 事件。"""
    events = []
    async for raw in stream_answer(rt, thread, text):
        head, data = raw.strip().split("\n")
        events.append((head.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


async def state(rt: AgentRuntime, thread: str = "t") -> dict[str, Any]:
    return (await rt.graph.aget_state({"configurable": {"thread_id": thread}})).values


def text_of(events: list[tuple[str, dict[str, Any]]]) -> str:
    return "".join(d["text"] for e, d in events if e == "token")


async def test_greeting_is_answered_by_rule_without_llm(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    rt, json_llm, _, _ = make_runtime(session_factory, json_replies=[])
    events = await ask(rt, "你好")
    assert text_of(events) == prompts.GREETING
    assert events[-1] == ("done", {})
    assert json_llm.prompts_seen == []  # 没有调用 LLM


async def test_full_clause_qa_flow_clarify_then_answer_with_citations(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    rt, json_llm, answer_llm, retr = make_runtime(
        session_factory,
        json_replies=[
            route("clause_qa", []),  # 第 1 轮：没说产品
            route("clause_qa", [products["ok"]]),  # 第 2 轮：只回了产品名
            json.dumps({"query": "等待期 天数"}),  # 改写
        ],
        answer_replies=["等待期 为 90 天[1] ，详见 [5] 不存在"],
    )
    first = await ask(rt, "等待期多久？")
    assert "请问您想了解哪款产品" in text_of(first)
    assert "对话测试医疗险" in text_of(first)
    assert "对话测试未入库险" not in text_of(first)  # 只列可查询的产品
    assert (await state(rt))["last_workflow"] == "clause_qa"
    assert retr.calls == []

    second = await ask(rt, "对话测试医疗险")
    kinds = [e for e, _ in second]
    assert kinds[:3] == ["status", "status", "status"]
    assert kinds[-2:] == ["citations", "done"]
    assert text_of(second) == "等待期 为 90 天[1] ，详见 [5] 不存在"
    # 回答模型的 token 逐段流式发出，且没有重复发送完整消息
    assert kinds.count("token") > 1
    # 引用只含真实检索到的 [1]，模型编造的 [5] 被丢弃
    cites = dict(second)["citations"]["items"]
    assert [c["index"] for c in cites] == [1]
    assert cites[0]["section_path"] == ["第三条 等待期"]
    # 检索参数由代码决定：改写后的查询 + 产品对应的条款 + 默认带重排
    assert retr.calls == [("等待期 天数", SearchMode.HYBRID_RERANK, [products["doc"]])]
    # 原问题作为补充查询一起召回
    assert retr.alt_queries == [["对话测试医疗险"]]
    # 改写时带上了历史，能看到第 1 轮的问题
    assert "等待期多久" in json_llm.prompts_seen[-1]
    # 回答提示词里同时有原问题和改写结果
    assert "（结合上下文理解为：等待期 天数）" in answer_llm.prompts_seen[-1]

    msgs = (await state(rt))["messages"]
    assert [type(m).__name__ for m in msgs] == [
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
        "AIMessage",
    ]
    assert msgs[-1].additional_kwargs["citations"] == cites


async def test_router_cannot_invent_products(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    rt, _, _, retr = make_runtime(
        session_factory,
        json_replies=[route("clause_qa", [999999999, "abc", True])],
    )
    events = await ask(rt, "达尔文 999 号等待期多久")
    assert "请问您想了解哪款产品" in text_of(events)  # 编造的 id 被丢弃，转为追问
    assert retr.calls == []


async def test_invented_ids_are_dropped_even_next_to_real_ones(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    rt, _, _, retr = make_runtime(
        session_factory,
        json_replies=[route("clause_qa", [999999999, products["ok"]])],
        answer_replies=["答[1]"],
    )
    await ask(rt, "对话测试医疗险等待期")
    # 编造的 id 不能进入状态：否则下一轮追问会沿用它
    assert (await state(rt))["product_ids"] == [products["ok"]]
    assert retr.calls[0][2] == [products["doc"]]


async def test_follow_up_keeps_previous_product(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    pid = products["ok"]
    rt, _, _, retr = make_runtime(
        session_factory,
        json_replies=[
            route("clause_qa", [str(pid)]),  # 字符串 id 也接受；第一句不改写
            route("clause_qa", []),  # 追问没提产品
            json.dumps({"query": "生孩子住院能报吗"}),  # 补全指代
        ],
        answer_replies=["答一[1]", "答二[2]"],
    )
    await ask(rt, "对话测试医疗险等待期多久")
    await ask(rt, "那生孩子呢")
    assert [c[2] for c in retr.calls] == [[products["doc"]], [products["doc"]]]
    assert [c[0] for c in retr.calls] == ["对话测试医疗险等待期多久", "生孩子住院能报吗"]
    assert retr.alt_queries == [[], ["那生孩子呢"]]
    assert (await state(rt))["product_ids"] == [pid]


async def test_unparseable_router_output_falls_back_to_clause_qa(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    rt, _, _, _ = make_runtime(session_factory, json_replies=["抱歉我不知道"])
    events = await ask(rt, "这个怎么赔")
    assert "请问您想了解哪款产品" in text_of(events)


async def test_first_turn_searches_original_question_without_rewrite(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    """第一句没有上文：不调用改写（评测里原问题检索效果最好，还省一次 LLM 调用）。"""
    rt, json_llm, answer_llm, retr = make_runtime(
        session_factory,
        json_replies=[route("clause_qa", [products["ok"]])],
        answer_replies=["答[1]"],
    )
    events = await ask(rt, "对话测试医疗险犹豫期多久")
    assert len(json_llm.prompts_seen) == 1  # 只有路由
    assert retr.calls[0][0] == "对话测试医疗险犹豫期多久"
    assert retr.alt_queries == [[]]  # 查询与原问题相同时不重复召回
    assert "结合上下文理解为" not in answer_llm.prompts_seen[-1]
    assert ("status", {"text": "正在理解问题…"}) not in events


async def test_rewrite_failure_uses_original_question(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    rt, _, _, retr = make_runtime(
        session_factory,
        json_replies=[
            route("clause_qa", []),  # 第 1 轮追问产品
            route("clause_qa", [products["ok"]]),
            "不是 JSON",  # 改写输出坏了
        ],
        answer_replies=["答[1]"],
    )
    await ask(rt, "犹豫期多久")
    await ask(rt, "对话测试医疗险的呢")
    assert retr.calls[0][0] == "对话测试医疗险的呢"
    assert retr.alt_queries == [[]]


async def test_rerank_failure_falls_back_to_hybrid(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    rt, _, _, retr = make_runtime(
        session_factory,
        json_replies=[route("clause_qa", [products["ok"]])],
        answer_replies=["答[1]"],
        retriever=FakeRetriever([hit(1)], rerank_fails=True),
    )
    events = await ask(rt, "对话测试医疗险等待期")
    assert [c[1] for c in retr.calls] == [SearchMode.HYBRID_RERANK, SearchMode.HYBRID]
    assert events[-1] == ("done", {})


async def test_no_hits_and_unindexed_product_get_fixed_replies(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    rt, _, answer_llm, _ = make_runtime(
        session_factory,
        json_replies=[
            route("clause_qa", [products["ok"]]),
            route("clause_qa", [products["missing"]]),
        ],
        retriever=FakeRetriever([]),
    )
    assert text_of(await ask(rt, "对话测试医疗险管火星旅行吗")) == prompts.NOT_FOUND
    assert answer_llm.prompts_seen == []  # 没有检索结果时不调用回答模型
    assert "还没有入库" in text_of(await ask(rt, "对话测试未入库险等待期多久"))


async def test_unimplemented_intents_get_coming_soon(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    rt, _, _, retr = make_runtime(
        session_factory, json_replies=[route("claim", []), route("other", [])]
    )
    assert text_of(await ask(rt, "住院了怎么理赔")) == prompts.COMING_SOON["claim"]
    assert (await state(rt))["last_workflow"] == "claim"
    assert text_of(await ask(rt, "今天天气怎么样")) == prompts.OUT_OF_SCOPE
    assert retr.calls == []


async def test_stream_error_hides_details(
    session_factory: async_sessionmaker[AsyncSession], products: dict[str, int]
) -> None:
    class Boom(FakeRetriever):
        async def search(self, *a: Any, **kw: Any) -> SearchResult:
            raise RuntimeError("secret upstream detail")

    rt, _, _, _ = make_runtime(
        session_factory,
        json_replies=[route("clause_qa", [products["ok"]])],
        retriever=Boom([]),
    )
    events = await ask(rt, "对话测试医疗险等待期")
    assert events[-1] == ("error", {"message": "回答生成失败，请稍后重试"})
    assert "secret" not in json.dumps(events, ensure_ascii=False)
