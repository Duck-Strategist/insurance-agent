"""对话图（spec §6.1）：

    START ─┬─ route ─┬─ clause_qa: prepare（定位产品、改写查询）→ answer（检索 + 带引用生成）
           │         ├─ advisor: collect → recommend → present → review（interrupt，M6）
           │         ├─ claim: claim_prepare（被保险人、名下保单）→ claim_answer（M7）
           │         └─ chitchat / out_of_scope：固定话术
           └─ extract_profile（M7，与路由并行）：抽取画像增量，推送 profile_suggestion

约束（spec §6.3，故事 2）：
- LLM 只能从候选产品里选 id，代码校验；查哪几份条款由代码根据产品决定；
- 检索参数（k、召回条数）在 RetrievalSettings 里固定，不交给 LLM；
- 回答只能引用本轮检索到的章节，引用由代码从 [编号] 解析，编号越界的直接丢弃。

流式输出（stream_mode=["messages", "custom"]）：
- answer 节点里回答模型的 token 走 messages 流（按节点名过滤，路由/改写的 JSON 不外发）；
- 状态、引用、固定话术走 custom 流（get_stream_writer）。
"""

import asyncio
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal, Protocol, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer, Command, interrupt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import prompts
from app.agent.advisor import (
    DEFAULT_CATEGORIES,
    INSURED_LABELS,
    Slots,
    ask_for,
    build_plan,
    describe_slots,
    merge_slots,
    missing_slots,
    prefill,
    profile_notice,
)
from app.agent.llm import parse_json_object
from app.agent.profile import build_suggestion, held_coverage, profile_slots
from app.core.config import LlmSettings
from app.core.logging import get_logger
from app.models import PlanStatus
from app.rag.reranker import RerankError
from app.rag.retriever import Hit, SearchMode, SearchResult
from app.schemas.profile import ProfileData
from app.services.plan_service import PlanService
from app.services.pricing_service import load_candidates
from app.services.profile_service import ProfileService, member_label, to_data

logger = get_logger(__name__)

Intent = Literal["clause_qa", "advisor", "claim", "chitchat", "other"]
INTENTS: set[str] = {"clause_qa", "advisor", "claim", "chitchat", "other"}

# 纯寒暄用规则拦截，不调 LLM（spec §6.1）
GREETING = re.compile(
    r"^\s*(你好|您好|hi|hello|hey|嗨|哈喽|在吗|在么|早上好|下午好|晚上好)[\s!！。.~～?？]*$", re.I
)
CITATION_MARK = re.compile(r"\[(\d{1,2})\]")


class SearchBackend(Protocol):
    async def search(
        self,
        query: str,
        *,
        mode: SearchMode,
        document_ids: Sequence[int] | None = None,
        alt_queries: Sequence[str] = (),
    ) -> SearchResult: ...


@dataclass
class AgentDeps:
    session_factory: async_sessionmaker[AsyncSession]
    retriever: SearchBackend
    json_llm: BaseChatModel
    answer_llm: BaseChatModel
    settings: LlmSettings
    # 画像抽取用的模型，默认同 json_llm；和路由并行调用，测试里单独给一个假模型，
    # 不打乱路由、改写的脚本顺序
    profile_llm: BaseChatModel | None = None


class ProductRef(TypedDict):
    id: int
    name: str
    category: str
    document_id: int | None  # 关联的可检索条款；未入库为 None


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    intent: str
    # 跨轮保留：处理"那它等待期多久"这类省略主语的追问
    last_workflow: str | None
    product_ids: list[int]
    # 顾问（M6）：需求槽位跨轮保留；plan / plan_id 是当前待确认的方案
    slots: Slots
    plan: dict[str, Any] | None
    plan_id: int | None
    # 本轮内部使用
    catalog: list[ProductRef]
    query: str
    claim_coverage: list[str]  # 理赔：给模型看的保单信息（产品、保额、生效日期）
    # 路由沿用了上一轮的产品（用户这句没提产品）
    products_reused: bool
    # 上一轮理赔咨询的被保险人：追问换了人时，不能沿用上一个人的保单
    claim_insured: str | None


# ---- 工具函数 ----


def last_question(state: AgentState) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""


def format_history(messages: Sequence[AnyMessage], turns: int, max_chars: int = 300) -> str:
    """最近 turns 轮对话（不含最新一句提问），用于路由和改写。"""
    past = list(messages)[:-1]
    lines = []
    for msg in past[-turns * 2 :]:
        role = "用户" if isinstance(msg, HumanMessage) else "顾问"
        content = str(msg.content).replace("\n", " ")
        lines.append(f"{role}：{content[:max_chars]}")
    return "\n".join(lines) or "（无）"


def extract_citations(answer: str, hits: Sequence[Hit]) -> list[dict[str, Any]]:
    """按回答里 [n] 首次出现的顺序取出被引用的章节；编号越界（模型编造的）直接丢弃。"""
    seen: list[int] = []
    for m in CITATION_MARK.finditer(answer):
        n = int(m.group(1))
        if 1 <= n <= len(hits) and n not in seen:
            seen.append(n)
    return [
        {
            "index": n,
            "document_id": hits[n - 1].document_id,
            "doc_title": hits[n - 1].doc_title,
            "section_path": hits[n - 1].section_path,
            "snippet": hits[n - 1].snippet,
            "parent_content": hits[n - 1].parent_content,
        }
        for n in seen
    ]


def format_sections(hits: Sequence[Hit], max_chars: int) -> str:
    blocks = []
    for i, h in enumerate(hits, start=1):
        path = " > ".join(h.section_path) or "（无章节标题）"
        content = h.parent_content
        if len(content) > max_chars:
            # 父块太长时以命中的子块为中心截取，保证命中片段在上下文里
            pos = max(content.find(h.snippet[:50]), 0)
            start = max(0, min(pos - max_chars // 3, len(content) - max_chars))
            content = content[start : start + max_chars] + "……"
        blocks.append(f"[{i}] 《{h.doc_title}》{path}\n{content}")
    return "\n\n".join(blocks)


async def load_catalog(session_factory: async_sessionmaker[AsyncSession]) -> list[ProductRef]:
    async with session_factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT p.id, p.name, p.category, d.id AS document_id
                FROM products p
                LEFT JOIN documents d
                  ON d.file_name = p.clause_file AND d.indexed_hash IS NOT NULL
                WHERE p.status = 'active'
                ORDER BY p.id
                """
            )
        )
        return [
            ProductRef(id=r.id, name=r.name, category=r.category, document_id=r.document_id)
            for r in rows.all()
        ]


def _as_ids(raw: object) -> list[int]:
    """模型有时把 id 写成字符串 "3"；其余类型（小数、布尔、名称）都不接受。"""
    if not isinstance(raw, list):
        return []
    ids: list[int] = []
    for item in raw:
        if isinstance(item, int) and not isinstance(item, bool):
            ids.append(item)
        elif isinstance(item, str) and item.strip().isdigit():
            ids.append(int(item))
    return list(dict.fromkeys(ids))


async def rewrite_query(
    json_llm: BaseChatModel, question: str, *, products: Sequence[str], history: str = "（无）"
) -> str:
    """结合对话历史把追问补全成独立的问题（只补全指代，不换术语，spec D39）。
    评测脚本也用它，保证线上线下一致。改写失败就返回原问题：不能因为改写挂掉整轮回答。"""
    prompt = prompts.REWRITE.format(
        products="、".join(products), history=history, question=question
    )
    resp = await json_llm.ainvoke([HumanMessage(content=prompt)])
    query = str(parse_json_object(str(resp.content)).get("query") or "").strip()
    return query[:200] or question


# 方案说明里出现金额就不用模型的话：金额只能来自报价引擎（方案卡片），D11 / D47
AMOUNT = re.compile(
    r"\d[\d,，.]*\s*(元|万|千|块)|[¥￥]\s*\d"
    # 中文数字写的金额：「三千多元」「一万免赔额」「保额三十万」。
    # 不单看「万」：「千万别忽视」「一万个理由」不是金额，误判会把好好的说明换成模板
    r"|[零一二两三四五六七八九十百千万]+多?\s*(元|块)"
    r"|[一二两三四五六七八九十百千]+万(元|的?(保额|保费|免赔额|预算))"
    r"|(保额|保费|免赔额|预算)[^，。；\n]{0,4}[一二两三四五六七八九十百千]+万"
)
# 「投保」会让用户以为确认方案就是买了保险；提示词里禁止了，但模型仍偶尔会写（M6 实测）
MISLEADING = ("投保",)


def _category_of(plan: dict[str, Any], product_id: int) -> str:
    return str(next(i["category"] for i in plan["items"] if i["product_id"] == product_id))


def edit_label(plan: dict[str, Any], swap: Sequence[int], drop: Sequence[str]) -> str:
    """卡片操作写进对话记录的那句话（前端显示同样的文字）。"""
    parts = []
    if swap:
        names = [i["product_name"] for i in plan["items"] if i["product_id"] in swap]
        parts.append("换一款：" + "、".join(names))
    if drop:
        labels = [i["category_label"] for i in plan["items"] if i["category"] in drop]
        parts.append("不要：" + "、".join(labels))
    return "；".join(parts)


def summarize_plan(plan: dict[str, Any]) -> str:
    lines = [
        f"- {i['category_label']}：{i['product_name']}，年交 {i['annual_premium']} 元"
        + (f"，保额 {i['sum_assured']} 元" if i.get("sum_assured") else "")
        for i in plan.get("items", [])
    ]
    return "\n".join(lines) or "（无）"


def _dropped_reason(reason: str) -> str:
    # 原因里带着「最低需 X 元」：给模型的版本去掉金额，免得它照抄
    return "超出预算" if "超出剩余预算" in reason else "不符合投保条件（如年龄）"


async def present_plan(llm: BaseChatModel, plan: dict[str, Any]) -> str:
    """让模型写几句方案说明；失败、为空或写了金额时用固定模板。"""
    slots = Slots(**plan["slots"])  # type: ignore[typeddict-item]
    items = "\n".join(
        "- {}：{}（{}）".format(
            i["category_label"], i["product_name"], "、".join(ln["title"] for ln in i["lines"])
        )
        for i in plan["items"]
    )
    dropped = "\n".join(
        f"- {d['category_label']}：{_dropped_reason(d['reason'])}" for d in plan["dropped"]
    )
    fallback = prompts.ADVISOR_PRESENT_FALLBACK.format(
        slots=describe_slots(slots),
        categories="、".join(i["category_label"] for i in plan["items"]),
    )
    try:
        resp = await llm.ainvoke(
            [
                SystemMessage(content=prompts.ADVISOR_PRESENT_SYSTEM),
                HumanMessage(
                    content=prompts.ADVISOR_PRESENT_USER.format(
                        slots=describe_slots(slots), items=items, dropped=dropped or "（无）"
                    )
                ),
            ]
        )
    except Exception:
        logger.warning("agent.present_failed", exc_info=True)
        return fallback
    text_ = str(resp.content).strip()
    reason = (
        "empty"
        if not text_
        else "amount"
        if AMOUNT.search(text_)
        else "misleading"
        if any(w in text_ for w in MISLEADING)
        else None
    )
    if reason:
        logger.info("agent.present_fallback", reason=reason)
        return fallback
    return text_


async def load_profile(
    session_factory: async_sessionmaker[AsyncSession], user_id: int | None
) -> ProfileData:
    if user_id is None:
        return ProfileData()
    async with session_factory() as session:
        return to_data(await ProfileService(session).get(user_id))


def _user_id(config: RunnableConfig) -> int | None:
    uid = config.get("configurable", {}).get("user_id")
    return uid if isinstance(uid, int) else None


def merge_hits(groups: Sequence[Sequence[Hit]], limit: int) -> list[Hit]:
    """几路检索结果按组的先后合并，同一章节（父块）只保留一次。"""
    out: list[Hit] = []
    seen: set[int] = set()
    for group in groups:
        for h in group:
            if h.parent_id not in seen and len(out) < limit:
                seen.add(h.parent_id)
                out.append(h)
    return out


def _who(insured: str) -> str:
    if insured == "self":
        return "您"
    # 朋友、同事等家人以外的人：说「您他人」不通
    return "被保险人" if insured == "other" else "您" + INSURED_LABELS[insured]


@dataclass
class RouteDecision:
    intent: str
    product_ids: list[int]  # 已校验：都在产品目录里
    parsed: bool  # 模型输出能解析且意图合法；False 时意图是兜底的 clause_qa
    by_rule: bool = False  # 寒暄规则命中，没有调用模型


async def classify(
    json_llm: BaseChatModel,
    catalog: Sequence[ProductRef],
    question: str,
    *,
    history: str = "（无）",
    last_workflow: str | None = None,
    last_products: Sequence[int] = (),
    config: RunnableConfig | None = None,
) -> RouteDecision:
    """意图路由：寒暄走规则，其余交给模型，产品 id 由代码校验。
    对话图的 route 节点和路由评测（app.eval routing）共用这一个函数。"""
    if GREETING.match(question):
        return RouteDecision("chitchat", [], parsed=True, by_rule=True)
    valid_ids = {p["id"] for p in catalog}
    prompt = prompts.ROUTER.format(
        products="\n".join(f"- {p['id']}：{p['name']}" for p in catalog) or "（无）",
        last_workflow=last_workflow or "无",
        last_products=list(last_products) or "无",
        history=history,
        question=question,
    )
    resp = await json_llm.ainvoke([HumanMessage(content=prompt)], config=config)
    data = parse_json_object(str(resp.content))
    intent = data.get("intent")
    parsed = intent in INTENTS
    if not parsed:
        # 解析失败：落到条款问答，最多多问一句"哪款产品"
        logger.warning("agent.route_unparsed", raw=str(resp.content)[:200])
        intent = "clause_qa"
    # LLM 只能从候选里选：不是整数、不在目录里的 id 一律丢弃（防编造）
    product_ids = [i for i in _as_ids(data.get("product_ids")) if i in valid_ids]
    return RouteDecision(str(intent), product_ids, parsed)


def resolve_products(
    decision: RouteDecision, last_workflow: str | None, last_products: Sequence[int]
) -> tuple[list[int], bool]:
    """路由后的产品：这句没提产品、且还在同一个业务里追问时，沿用上一轮的产品。
    返回 (产品 id, 是否沿用)。route 节点和路由评测共用。"""
    if (
        not decision.product_ids
        and decision.intent in ("clause_qa", "claim")
        and last_workflow == decision.intent
        and last_products
    ):
        return list(last_products), True
    return decision.product_ids, False


@dataclass
class ClauseAnswer:
    content: str
    citations: list[dict[str, Any]]
    hits: list[Hit]


async def answer_from_clauses(
    retriever: SearchBackend,
    answer_llm: BaseChatModel,
    question: str,
    query: str,
    document_ids: Sequence[int],
    context_chars: int,
    on_status: Callable[[str], None] = lambda _: None,
    config: RunnableConfig | None = None,
) -> ClauseAnswer | None:
    """检索 + 带引用生成；检索不到返回 None。对话图的 answer 节点和端到端评测共用。"""
    # 改写后的查询为主（重排用它），原问题一起召回：改写会丢掉与条款原文一致的措辞（spec D39）
    alt = [question] if question != query else []
    try:
        result = await retriever.search(
            query, mode=SearchMode.HYBRID_RERANK, document_ids=document_ids, alt_queries=alt
        )
    except RerankError as exc:
        # 重排服务故障时退化为不重排的混合检索，回答质量略降但不中断对话
        logger.warning("agent.rerank_failed", error=str(exc))
        result = await retriever.search(
            query, mode=SearchMode.HYBRID, document_ids=document_ids, alt_queries=alt
        )
    hits = result.hits
    if not hits:
        return None
    on_status("正在生成回答…")
    asked = question if query == question else f"{question}\n（结合上下文理解为：{query}）"
    resp = await answer_llm.ainvoke(
        [
            SystemMessage(content=prompts.ANSWER_SYSTEM),
            HumanMessage(
                content=prompts.ANSWER_USER.format(
                    sections=format_sections(hits, context_chars), n=len(hits), question=asked
                )
            ),
        ],
        config=config,
    )
    content = str(resp.content)
    return ClauseAnswer(content, extract_citations(content, hits), hits)


def reply(text_: str, **updates: Any) -> dict[str, Any]:
    """固定话术：通过 custom 流整段发给前端，并写入对话历史。"""
    get_stream_writer()({"type": "token", "text": text_})
    return {"messages": [AIMessage(content=text_)], **updates}


# ---- 图 ----


def build_graph(
    deps: AgentDeps, checkpointer: Checkpointer = None
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    cfg = deps.settings

    async def route(state: AgentState) -> dict[str, Any]:
        catalog = await load_catalog(deps.session_factory)
        valid_ids = {p["id"] for p in catalog}
        last_workflow = state.get("last_workflow")
        last_products = [i for i in state.get("product_ids", []) if i in valid_ids]
        decision = await classify(
            deps.json_llm,
            catalog,
            last_question(state),
            history=format_history(state["messages"], cfg.history_turns),
            last_workflow=last_workflow,
            last_products=last_products,
        )
        if decision.by_rule:
            return {"intent": decision.intent, "catalog": catalog}
        intent = decision.intent
        product_ids, reused = resolve_products(decision, last_workflow, last_products)
        logger.info("agent.route", intent=intent, product_ids=product_ids)
        return {
            "intent": intent,
            "product_ids": product_ids,
            "products_reused": reused,
            "catalog": catalog,
        }

    def after_route(state: AgentState) -> str:
        return {
            "clause_qa": "prepare",
            "chitchat": "chitchat",
            "advisor": "advisor_collect",
            "claim": "claim_prepare",
        }.get(state["intent"], "out_of_scope")

    async def chitchat(state: AgentState) -> dict[str, Any]:
        return reply(prompts.GREETING)

    async def out_of_scope(state: AgentState) -> dict[str, Any]:
        return reply(prompts.OUT_OF_SCOPE)

    async def prepare(state: AgentState) -> dict[str, Any]:
        """定位产品；缺产品就追问，条款未入库就说明；否则把问题改写成检索查询。"""
        catalog = state.get("catalog", [])
        chosen = [p for p in catalog if p["id"] in state.get("product_ids", [])]
        if not chosen:
            names = "、".join(p["name"] for p in catalog if p["document_id"] is not None)
            # 记下业务，用户下一句只回产品名时还能接上原来的问题
            return reply(
                prompts.CLARIFY_PRODUCT.format(names=names or "（暂无）"),
                last_workflow="clause_qa",
                query="",
            )
        missing = [p["name"] for p in chosen if p["document_id"] is None]
        if len(missing) == len(chosen):
            return reply(
                prompts.NO_DOCUMENT.format(names="、".join(missing)),
                last_workflow="clause_qa",
                query="",
            )

        question = last_question(state)
        history = format_history(state["messages"], cfg.history_turns)
        if len(state["messages"]) <= 1:
            # 第一句话没有上文可补全，直接用原问题检索，省一次 LLM 调用（spec D39）
            return {"query": question}
        get_stream_writer()({"type": "status", "text": "正在理解问题…"})
        query = await rewrite_query(
            deps.json_llm, question, products=[p["name"] for p in chosen], history=history
        )
        return {"query": query}

    def after_prepare(state: AgentState) -> str:
        return "answer" if state.get("query") else END

    async def answer(state: AgentState) -> dict[str, Any]:
        writer = get_stream_writer()
        question = last_question(state)
        query = state["query"]
        doc_ids = sorted(
            {
                p["document_id"]
                for p in state.get("catalog", [])
                if p["id"] in state.get("product_ids", []) and p["document_id"] is not None
            }
        )
        writer({"type": "status", "text": "正在检索条款…"})
        result = await answer_from_clauses(
            deps.retriever,
            deps.answer_llm,
            question,
            query,
            doc_ids,
            cfg.context_chars_per_section,
            on_status=lambda text_: writer({"type": "status", "text": text_}),
        )
        if result is None:
            return reply(prompts.NOT_FOUND, last_workflow="clause_qa", query="")
        content, citations = result.content, result.citations
        writer({"type": "citations", "items": citations})
        return {
            "messages": [AIMessage(content=content, additional_kwargs={"citations": citations})],
            "last_workflow": "clause_qa",
            "query": "",
        }

    # ---- 顾问：需求采集 → 方案 → [interrupt 确认] ----

    async def advisor_collect(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """从这句话里抽取需求槽位，缺的用画像补上（M7）；仍缺必填项就追问，齐了进入配方案。"""
        slots = state.get("slots") or Slots()
        resp = await deps.json_llm.ainvoke(
            [
                HumanMessage(
                    content=prompts.ADVISOR_EXTRACT.format(
                        slots=json.dumps(slots, ensure_ascii=False) or "{}",
                        history=format_history(state["messages"], cfg.history_turns),
                        question=last_question(state),
                    )
                )
            ]
        )
        slots = merge_slots(slots, parse_json_object(str(resp.content)))
        slots = await fill_from_profile(slots, config)
        logger.info("agent.advisor_slots", slots=slots)
        missing = missing_slots(slots)
        if missing:
            return reply(ask_missing(slots, missing), slots=slots, last_workflow="advisor")
        return {"slots": slots, "last_workflow": "advisor"}

    async def fill_from_profile(slots: Slots, config: RunnableConfig) -> Slots:
        if not missing_slots(slots):
            return slots
        profile = await load_profile(deps.session_factory, _user_id(config))
        return prefill(slots, profile_slots(profile, slots.get("insured", "self"), date.today()))

    def ask_missing(slots: Slots, missing: list[str]) -> str:
        """画像带入的信息在提示里说过了，「已了解」只列用户自己说的，不重复。"""
        from_profile = set(slots.get("from_profile", []))
        told = Slots(**{k: v for k, v in slots.items() if k not in from_profile})  # type: ignore[typeddict-item]
        known = describe_slots(told)
        return profile_notice(slots) + (f"已了解：{known}。" if known else "") + ask_for(missing)

    def after_collect(state: AgentState) -> str:
        return "advisor_recommend" if not missing_slots(state.get("slots") or Slots()) else END

    async def advisor_recommend(state: AgentState) -> dict[str, Any]:
        get_stream_writer()({"type": "status", "text": "正在按预算配置方案并报价…"})
        slots = state["slots"]
        async with deps.session_factory() as session:
            candidates = await load_candidates(session)
        plan = build_plan(candidates, slots)
        if not plan["items"]:
            reasons = "；".join(f"{d['category_label']}（{d['reason']}）" for d in plan["dropped"])
            return reply(
                prompts.ADVISOR_NO_PLAN.format(slots=describe_slots(slots), reasons=reasons),
                plan=None,
                last_workflow="advisor",
            )
        return {"plan": plan}

    def after_recommend(state: AgentState) -> str:
        return "advisor_present" if state.get("plan") else END

    async def advisor_present(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """方案落库（pending）、写说明、推送方案卡片。interrupt 放在下一个节点：
        恢复执行时被中断的节点会从头重跑，放在这里会重复落库、重复调用模型。"""
        writer = get_stream_writer()
        plan, slots = state["plan"], state["slots"]
        assert plan is not None  # after_recommend 保证
        user_id = config["configurable"]["user_id"]
        thread_id = config["configurable"]["thread_id"]
        # 先生成说明再落库：调用模型时连接断开（节点被取消）不会留下无法确认的 pending 方案
        # 画像带入了哪些信息写在说明前面（代码生成，不经过模型；金额检查只针对模型写的部分）
        intro = profile_notice(slots) + await present_plan(deps.answer_llm, plan)
        async with deps.session_factory() as session:
            row = await PlanService(session).create_pending(user_id, thread_id, plan)
        card = {**plan, "plan_id": row.id}
        writer({"type": "token", "text": intro})
        writer({"type": "plan_card", "plan": card})
        return {
            "messages": [AIMessage(content=intro, additional_kwargs={"plan": card})],
            "plan_id": row.id,
            "slots": slots,
            "last_workflow": "advisor",
        }

    async def advisor_review(
        state: AgentState, config: RunnableConfig
    ) -> Command[
        Literal["advisor_recommend", "advisor_review", "route", "extract_profile", "__end__"]
    ]:
        """等用户确认（interrupt）。

        恢复值来自方案卡片的按钮（confirm / cancel / edit），或用户直接打字（message）。
        """
        plan_id = state["plan_id"]
        plan = state.get("plan")
        assert plan_id is not None and plan is not None  # advisor_present 保证
        decision: dict[str, Any] = interrupt({"plan_id": plan_id})
        user_id = config["configurable"]["user_id"]
        writer = get_stream_writer()
        action = decision.get("action")
        said: list[AnyMessage] = []
        slot_changes: dict[str, Any] = {}

        if action == "message":
            text_ = str(decision.get("text", ""))
            said = [HumanMessage(content=text_)]
            resp = await deps.json_llm.ainvoke(
                [
                    HumanMessage(
                        content=prompts.ADVISOR_REVIEW.format(
                            slots=json.dumps(state["slots"], ensure_ascii=False),
                            plan=summarize_plan(plan),
                            question=text_,
                        )
                    )
                ]
            )
            data = parse_json_object(str(resp.content))
            action = data.get("decision")
            if action not in ("confirm", "cancel", "modify", "other"):
                logger.warning("agent.review_unparsed", raw=str(resp.content)[:200])
                action = "other"
            raw_slots = data.get("slots")
            slot_changes = raw_slots if isinstance(raw_slots, dict) else {}
        elif action == "confirm":
            said = [HumanMessage(content="确认方案")]
        elif action == "cancel":
            said = [HumanMessage(content="取消方案")]

        async def move(to: PlanStatus) -> bool:
            async with deps.session_factory() as session:
                ok = await PlanService(session).transition(plan_id, user_id, to)
            if ok:
                writer({"type": "plan_status", "plan_id": plan_id, "status": to.value})
            return ok

        if action in ("confirm", "cancel"):
            to = PlanStatus.CONFIRMED if action == "confirm" else PlanStatus.CANCELLED
            done = await move(to)
            text_ = (
                (prompts.ADVISOR_CONFIRMED if action == "confirm" else prompts.ADVISOR_CANCELLED)
                if done
                else prompts.ADVISOR_ALREADY_HANDLED
            )
            out = reply(text_)
            # 方案有了结果就清空需求：下一个方案可能是给别人配的（「给我老婆也配一份」），
            # 不能悄悄沿用这次的预算、险种和去掉的产品。转去问别的（expired）时保留，
            # 用户还能说「还是刚才的条件」
            return Command(
                update={
                    "messages": [*said, *out["messages"]],
                    "plan": None,
                    "plan_id": None,
                    "slots": Slots(),
                },
                goto="__end__",
            )

        if action == "edit":
            plan_products = {i["product_id"]: i["product_name"] for i in plan["items"]}
            plan_categories = {i["category"]: i["category_label"] for i in plan["items"]}
            swap = [i for i in _as_ids(decision.get("remove_product_ids")) if i in plan_products]
            raw_cats = decision.get("remove_categories")
            drop = [c for c in plan_categories if isinstance(raw_cats, list) and c in raw_cats]
            # 同一项既换又不要时，以「不要这个险种」为准
            swap = [i for i in swap if _category_of(plan, i) not in drop]
            if not swap and not drop:
                out = reply(prompts.ADVISOR_NOTHING_CHANGED)
                return Command(update={"messages": out["messages"]}, goto="advisor_review")
            said = [HumanMessage(content=edit_label(plan, swap, drop))]
            slots = Slots(**state["slots"])
            if swap:
                slots["excluded_product_ids"] = sorted(
                    {*slots.get("excluded_product_ids", []), *swap}
                )
            if drop:
                current = slots.get("categories") or list(DEFAULT_CATEGORIES)
                slots["categories"] = [c for c in current if c not in drop]
                if not slots["categories"]:
                    # 全都不要了：等于取消
                    del slots["categories"]
                    done = await move(PlanStatus.CANCELLED)
                    text_ = prompts.ADVISOR_CANCELLED if done else prompts.ADVISOR_ALREADY_HANDLED
                    out = reply(text_)
                    return Command(
                        update={
                            "messages": [*said, *out["messages"]],
                            "plan": None,
                            "plan_id": None,
                            "slots": Slots(),
                        },
                        goto="__end__",
                    )
            await move(PlanStatus.SUPERSEDED)
            return Command(
                update={"messages": said, "slots": slots, "plan": None, "plan_id": None},
                goto="advisor_recommend",
            )

        if action == "modify":
            slots = merge_slots(state["slots"], slot_changes)
            if slots == state["slots"]:
                # 听不懂要改什么：说明一下，方案保持待确认，继续等
                out = reply(prompts.ADVISOR_UNCLEAR_CHANGE)
                return Command(
                    update={"messages": [*said, *out["messages"]]}, goto="advisor_review"
                )
            await move(PlanStatus.SUPERSEDED)
            # 换了被保险人（「还是给我老婆配吧」）：用画像补上这个人的信息，补不齐就问
            slots = await fill_from_profile(slots, config)
            missing = missing_slots(slots)
            if missing:
                out = reply(ask_missing(slots, missing))
                return Command(
                    update={
                        "messages": [*said, *out["messages"]],
                        "slots": slots,
                        "plan": None,
                        "plan_id": None,
                        "last_workflow": "advisor",
                    },
                    goto=["extract_profile"],
                )
            writer({"type": "status", "text": "正在按新的需求调整方案…"})
            return Command(
                update={"messages": said, "slots": slots, "plan": None, "plan_id": None},
                goto=["advisor_recommend", "extract_profile"],
            )

        # 和方案无关（问条款等）：方案作废为 expired，这句话交给路由按普通消息处理；
        # 打字的话同样要抽取画像（按钮操作没有新信息，不抽）
        await move(PlanStatus.EXPIRED)
        return Command(
            update={"messages": said, "plan": None, "plan_id": None},
            goto=["route", "extract_profile"],
        )

    # ---- 理赔咨询（M7，spec §6.2、D18）----

    async def claim_prepare(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """确定出险的人和要查的保单：用户提到的产品优先，否则用画像里这个人名下登记的
        库内产品；都没有就追问（并建议去画像登记），只登记了库外产品就给通用指引。"""
        get_stream_writer()({"type": "status", "text": "正在理解问题…"})
        question = last_question(state)
        resp = await deps.json_llm.ainvoke(
            [
                HumanMessage(
                    content=prompts.CLAIM_EXTRACT.format(
                        history=format_history(state["messages"], cfg.history_turns),
                        question=question,
                    )
                )
            ]
        )
        data = parse_json_object(str(resp.content))
        insured = data.get("insured")
        insured = insured if insured in INSURED_LABELS else "self"
        query = str(data.get("query") or "").strip()[:200] or question

        profile = await load_profile(deps.session_factory, _user_id(config))
        by_id = {p["id"]: p for p in state.get("catalog", [])}
        held = held_coverage(profile, insured)
        mentioned = [i for i in state.get("product_ids", []) if i in by_id]
        if state.get("products_reused") and insured != state.get("claim_insured"):
            # 「那我老婆住院呢」：路由沿用了上一轮（另一个人）的产品，改查这个人名下的保单
            mentioned = []
        chosen = mentioned or list(
            dict.fromkeys(c.product_id for c in held if c.product_id in by_id)
        )
        if not chosen:
            off = [c.product_name for c in held if c.product_id is None]
            if off:
                text_ = prompts.CLAIM_OFF_CATALOG.format(who=_who(insured), names="、".join(off))
            else:
                names = "、".join(p["name"] for p in by_id.values() if p["document_id"] is not None)
                text_ = prompts.CLAIM_NEED_COVERAGE.format(who=_who(insured), names=names)
            return reply(
                text_, last_workflow="claim", product_ids=[], query="", claim_insured=insured
            )
        products = [by_id[i] for i in chosen if i is not None]
        if all(p["document_id"] is None for p in products):
            return reply(
                prompts.NO_DOCUMENT.format(names="、".join(p["name"] for p in products)),
                last_workflow="claim",
                query="",
                claim_insured=insured,
            )

        members = {m.id: member_label(m) for m in profile.family}
        lines = []
        for p in products:
            regs = [c for c in profile.existing_coverage if c.product_id == p["id"]]
            if not regs:
                lines.append(f"- {p['name']}（用户在对话中提到，画像里没有登记保额和生效日期）")
            for c in regs:
                who = "本人" if c.insured == "self" else members.get(c.insured, "家人")
                extra = [f"被保险人：{who}"]
                if c.sum_assured is not None:
                    extra.append(f"保额 {c.sum_assured:,.2f} 元")
                start = c.start_date
                if start is None:
                    extra.append("生效日期未登记")
                else:
                    # 天数由代码算好：实测模型自己推算「生效日 + 90 天」会算错日期
                    days = (date.today() - start).days
                    extra.append(
                        f"生效日期 {start.isoformat()}"
                        + (f"，到今天已生效 {days} 天" if days >= 0 else "，还未生效")
                    )
                lines.append(f"- {p['name']}（{'，'.join(extra)}）")
        return {
            "product_ids": [p["id"] for p in products],
            "query": query,
            "claim_coverage": lines,
            "claim_insured": insured,
        }

    def after_claim_prepare(state: AgentState) -> str:
        return "claim_answer" if state.get("query") else END

    async def claim_search(query: str, doc_ids: list[int], alt: Sequence[str]) -> list[Hit]:
        try:
            result = await deps.retriever.search(
                query, mode=SearchMode.HYBRID_RERANK, document_ids=doc_ids, alt_queries=alt
            )
        except RerankError as exc:
            logger.warning("agent.rerank_failed", error=str(exc))
            result = await deps.retriever.search(
                query, mode=SearchMode.HYBRID, document_ids=doc_ids, alt_queries=alt
            )
        return result.hits

    async def claim_answer(state: AgentState) -> dict[str, Any]:
        """四路检索并行：问题本身、问题 + 责任免除、等待期、理赔材料。「能不能赔」的答案常在
        责任免除和等待期里，而用户不会这么问（spec Q14）；材料清单的章节和问题的措辞几乎不重合。
        只检索问题本身时，实测等待期章节进不了前 5，模型拿着保单生效日期也判断不了等待期。"""
        writer = get_stream_writer()
        question, query = last_question(state), state["query"]
        doc_ids = sorted(
            {
                p["document_id"]
                for p in state.get("catalog", [])
                if p["id"] in state.get("product_ids", []) and p["document_id"] is not None
            }
        )
        writer({"type": "status", "text": "正在检索保单条款…"})
        alt = [question] if question != query else []
        main, exclusions, waiting, materials = await asyncio.gather(
            claim_search(query, doc_ids, alt),
            claim_search(query + prompts.CLAIM_EXCLUSION_SUFFIX, doc_ids, ()),
            claim_search(prompts.CLAIM_WAITING_QUERY, doc_ids, ()),
            claim_search(prompts.CLAIM_MATERIALS_QUERY, doc_ids, ()),
        )
        hits = merge_hits([main[:4], exclusions[:2], waiting[:1], materials[:2]], limit=9)
        if not hits:
            return reply(prompts.NOT_FOUND, last_workflow="claim", query="")

        writer({"type": "status", "text": "正在整理理赔要点…"})
        asked = question if query == question else f"{question}\n（结合上下文理解为：{query}）"
        resp = await deps.answer_llm.ainvoke(
            [
                SystemMessage(content=prompts.CLAIM_SYSTEM),
                HumanMessage(
                    content=prompts.CLAIM_USER.format(
                        today=date.today().isoformat(),
                        coverage="\n".join(state.get("claim_coverage", [])) or "（无）",
                        sections=format_sections(hits, cfg.context_chars_per_section),
                        n=len(hits),
                        question=asked,
                    )
                ),
            ]
        )
        content = str(resp.content)
        if prompts.CLAIM_DISCLAIMER not in content:
            # 「以保险公司审核为准」由代码保证，不依赖模型记得写
            tail = "\n\n" + prompts.CLAIM_DISCLAIMER
            writer({"type": "token", "text": tail})
            content += tail
        citations = extract_citations(content, hits)
        writer({"type": "citations", "items": citations})
        return {
            "messages": [AIMessage(content=content, additional_kwargs={"citations": citations})],
            "last_workflow": "claim",
            "query": "",
        }

    # ---- 画像抽取（M7，spec D20）：与路由并行，只推送建议，不写库 ----

    async def extract_profile(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        user_id = _user_id(config)
        question = last_question(state)
        if user_id is None or not question or GREETING.match(question):
            return {}
        try:
            current = await load_profile(deps.session_factory, user_id)
            catalog = await load_catalog(deps.session_factory)
            llm = deps.profile_llm or deps.json_llm
            resp = await llm.ainvoke(
                [
                    HumanMessage(
                        content=prompts.PROFILE_EXTRACT.format(
                            products="\n".join(f"- {p['id']}：{p['name']}" for p in catalog),
                            history=format_history(state["messages"], cfg.history_turns),
                            question=question,
                            today=date.today().isoformat(),
                        )
                    )
                ]
            )
            suggestion = build_suggestion(
                current,
                parse_json_object(str(resp.content)),
                {p["id"]: (p["name"], p["category"]) for p in catalog},
                date.today(),
            )
        except Exception:
            # 抽取是附带功能：失败只记日志，不影响这一轮的回答
            logger.warning("agent.profile_extract_failed", exc_info=True)
            return {}
        if suggestion is not None:
            logger.info("agent.profile_suggestion", fields=[i.label for i in suggestion.items])
            get_stream_writer()(
                {"type": "profile_suggestion", "suggestion": suggestion.model_dump(mode="json")}
            )
        return {}

    graph = StateGraph(AgentState)
    graph.add_node("route", route)
    graph.add_node("extract_profile", extract_profile)
    graph.add_node("chitchat", chitchat)
    graph.add_node("claim_prepare", claim_prepare)
    graph.add_node("claim_answer", claim_answer)
    graph.add_node("out_of_scope", out_of_scope)
    graph.add_node("prepare", prepare)
    graph.add_node("answer", answer)
    graph.add_node("advisor_collect", advisor_collect)
    graph.add_node("advisor_recommend", advisor_recommend)
    graph.add_node("advisor_present", advisor_present)
    graph.add_node("advisor_review", advisor_review)
    graph.add_edge(START, "route")
    graph.add_edge(START, "extract_profile")
    graph.add_edge("extract_profile", END)
    graph.add_conditional_edges(
        "route",
        after_route,
        ["prepare", "chitchat", "claim_prepare", "out_of_scope", "advisor_collect"],
    )
    graph.add_conditional_edges("claim_prepare", after_claim_prepare, ["claim_answer", END])
    graph.add_conditional_edges("advisor_collect", after_collect, ["advisor_recommend", END])
    graph.add_conditional_edges("advisor_recommend", after_recommend, ["advisor_present", END])
    graph.add_edge("advisor_present", "advisor_review")
    graph.add_conditional_edges("prepare", after_prepare, ["answer", END])
    for node in ("chitchat", "out_of_scope", "answer", "claim_answer"):
        graph.add_edge(node, END)
    return graph.compile(checkpointer=checkpointer)
