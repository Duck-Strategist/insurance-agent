"""顾问（M6）：槽位校验、方案生成（真实费率表）、方案说明的金额检查、
需求采集 → 方案卡片 → interrupt → 确认 / 调整 / 去掉某项 / 转去问别的 的完整流程。"""

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import prompts
from app.agent.advisor import (
    Candidate,
    Slots,
    ask_for,
    build_plan,
    merge_slots,
    missing_slots,
)
from app.agent.graph import present_plan
from app.models import Plan, Product
from app.pricing.engine import rule_for
from app.pricing.tables import RATES_DIR, load_all_books
from app.seed import load_products, seed_products
from app.services.pricing_service import load_rate_books
from tests.test_agent import ScriptedLLM, make_runtime, route
from tests.test_chat_api import api, box, factory, parse_sse, token_for  # noqa: F401

# ---------- 槽位 ----------


def test_merge_slots_validates_each_field() -> None:
    slots = merge_slots(
        Slots(),
        {
            "age": "30",
            "gender": "male",
            "social_security": True,
            "budget": 5000,
            "categories": ["accident", "car", "medical"],
            "ci_sum_assured": 500000,
        },
    )
    assert slots == {
        "age": 30,
        "gender": "male",
        "social_security": True,
        "budget": "5000.00",
        "categories": ["medical", "accident"],  # 未知险种丢弃，按优先级排序
        "ci_sum_assured": 500000,
    }
    # 不合法的值丢弃并保留原值：模型编出 200 岁、负预算、"男" 都不会覆盖
    bad = merge_slots(
        slots,
        {
            "age": 200,
            "gender": "男",
            "social_security": "yes",
            "budget": -1,
            "categories": ["car"],
            "ci_sum_assured": 100,
        },
    )
    assert bad == slots
    assert merge_slots(Slots(), {"age": True, "budget": "abc"}) == {}


def test_missing_slots_and_question() -> None:
    assert missing_slots(Slots(age=30, gender="male")) == ["social_security", "budget"]
    assert ask_for(["social_security", "budget"]) == (
        "为了给您配置合适的方案，还需要了解：是否有社保（医保）、每年大概能接受的保费预算。"
    )


# ---------- 方案（真实费率表，不连数据库） ----------

BOOKS = load_all_books()
SEED = {p["name"]: p for p in load_products()}
CANDIDATES = [
    Candidate(i, name, SEED[name]["category"], rule_for(name), book, SEED[name]["min_premium"])  # type: ignore[arg-type]
    for i, (name, book) in enumerate(BOOKS.items(), start=1)
]
ADULT = Slots(age=30, gender="male", social_security=True, budget="5000.00")


def names(plan: dict[str, Any]) -> list[str]:
    return [i["product_name"] for i in plan["items"]]


def test_plan_picks_one_product_per_category_within_budget() -> None:
    plan = build_plan(CANDIDATES, ADULT)
    assert [i["category"] for i in plan["items"]] == ["medical", "critical_illness", "accident"]
    assert Decimal(plan["total_annual_premium"]) <= Decimal("5000")
    ci = plan["items"][1]
    assert ci["sum_assured"] == "300000.00"  # 50 万超预算，退到 30 万
    # 每项都带报价引擎的明细和费率版本，可以复现
    assert ci["rate_version"] and "第" in ci["lines"][0]["basis"]


def test_smaller_budget_lowers_ci_sum_assured_then_drops_it() -> None:
    plan = build_plan(CANDIDATES, Slots(**{**ADULT, "budget": "2000.00"}))  # type: ignore[typeddict-item]
    assert plan["items"][1]["sum_assured"] == "100000.00"
    tiny = build_plan(CANDIDATES, Slots(**{**ADULT, "budget": "500.00"}))  # type: ignore[typeddict-item]
    assert [i["category"] for i in tiny["items"]] == ["medical", "accident"]
    dropped = tiny["dropped"][0]
    assert dropped["category"] == "critical_illness" and "超出剩余预算" in dropped["reason"]


def test_ci_sum_assured_request_is_a_cap() -> None:
    plan = build_plan(
        CANDIDATES, Slots(**{**ADULT, "budget": "50000.00", "ci_sum_assured": 200000})
    )  # type: ignore[typeddict-item]
    ci = next(i for i in plan["items"] if i["category"] == "critical_illness")
    assert ci["sum_assured"] == "200000.00"


def test_categories_exclusions_children_and_life() -> None:
    plan = build_plan(CANDIDATES, Slots(**{**ADULT, "categories": ["accident"]}))  # type: ignore[typeddict-item]
    assert [i["category"] for i in plan["items"]] == ["accident"]
    medical = next(i for i in build_plan(CANDIDATES, ADULT)["items"])
    without = build_plan(
        CANDIDATES,
        Slots(**{**ADULT, "excluded_product_ids": [medical["product_id"]]}),  # type: ignore[typeddict-item]
    )
    assert medical["product_name"] not in names(without)
    kid = build_plan(CANDIDATES, Slots(age=5, gender="female", social_security=True, budget="3000"))
    assert "人保小学童2号Pro学平险" in names(kid)  # 小蜜蜂 18 岁起保
    assert all(i["category"] != "life" for i in build_plan(CANDIDATES, ADULT)["items"])
    life = build_plan(
        CANDIDATES,
        Slots(
            **{**ADULT, "budget": "30000", "categories": ["life"], "life_annual_premium": "20000"}
        ),  # type: ignore[typeddict-item]
    )
    assert life["items"][0]["annual_premium"] == "20000.00"


# ---------- 方案说明：写了金额就换成模板 ----------


async def test_present_plan_falls_back_when_model_writes_amounts() -> None:
    plan = build_plan(CANDIDATES, ADULT)
    ok = await present_plan(ScriptedLLM(["医疗险报销住院费用，重疾险弥补收入损失。"]), plan)
    assert ok == "医疗险报销住院费用，重疾险弥补收入损失。"
    for text in (
        "重疾保额 30 万，很划算",
        "合计 3,983 元",
        "每年只要￥184",
        "可以随时确认投保",
        "重疾保额三十万，每年三千多元",
        "医疗险有一万免赔额",
        "重疾选了三十万的保额",
    ):
        out = await present_plan(ScriptedLLM([text]), plan)
        assert out.startswith("根据您的情况（30 周岁，男，有社保，年预算 5,000 元）")
    # 不是金额的「万」「千」不该误判：误判会把好好的说明换成模板
    for text in ("千万别忽视重疾保障。", "意外险能应对一万种意外里的常见情形。"):
        assert await present_plan(ScriptedLLM([text]), plan) == text

    class Boom(ScriptedLLM):
        async def ainvoke(self, *a: Any, **kw: Any) -> Any:
            raise RuntimeError("upstream down")

    assert (await present_plan(Boom([]), plan)).startswith("根据您的情况")


# ---------- 完整流程（数据库 + 假 LLM，走 HTTP 接口） ----------

FLOW_RATE_FILES = (
    "xiangshou-2-personal.json",
    "xiaomifeng6.json",
    "yilian-youmeng.json",
)


@pytest.fixture
async def advisor_products(db_session: AsyncSession, tmp_path: Path) -> dict[str, int]:
    """事务内：清空产品，写入种子产品，只加载流程测试用到的费率表（医疗、重疾、意外各一款）。"""
    await db_session.execute(delete(Product))
    await seed_products(db_session)
    for name in FLOW_RATE_FILES:
        shutil.copy(RATES_DIR / name, tmp_path / name)
    await load_rate_books(db_session, tmp_path)
    rows = await db_session.execute(select(Product.name, Product.id))
    return {n: i for n, i in rows.all()}


def extract(**slots: Any) -> str:
    return json.dumps(slots)


def review(decision: str, **slots: Any) -> str:
    return json.dumps({"decision": decision, "slots": slots})


async def chat(api: AsyncClient, headers: dict[str, str], message: str, thread: str | None = None):  # type: ignore[no-untyped-def]  # noqa: F811
    body: dict[str, Any] = {"message": message}
    if thread:
        body["thread_id"] = thread
    resp = await api.post("/api/v1/chat", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return parse_sse(resp.text)


def text_of(events: list[tuple[str, dict[str, Any]]]) -> str:
    return "".join(d["text"] for e, d in events if e == "token")


def card_of(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    return next(d["plan"] for e, d in events if e == "plan_card")


async def plan_status(db: AsyncSession, plan_id: int) -> str:
    db.expire_all()
    return (await db.get(Plan, plan_id)).status  # type: ignore[union-attr]


async def test_collect_then_card_then_confirm(
    api: AsyncClient,  # noqa: F811
    box: Any,  # noqa: F811
    factory: async_sessionmaker[AsyncSession],  # noqa: F811
    db_session: AsyncSession,
    advisor_products: dict[str, int],
) -> None:
    rt, json_llm, answer_llm, _ = make_runtime(
        factory,
        json_replies=[
            route("advisor", []),
            extract(age=30),
            route("advisor", []),
            extract(gender="male", social_security=True, budget=5000),
        ],
        answer_replies=["医疗险报销大额住院费用，重疾险弥补收入损失，意外险覆盖意外风险。"],
    )
    box.runtime = rt
    h = await token_for(api, "advisor1")

    first = await chat(api, h, "帮我配一份保险，我 30 岁")
    thread = next(d["id"] for e, d in first if e == "thread")
    assert text_of(first) == "已了解：30 周岁。" + ask_for(["gender", "social_security", "budget"])
    assert not any(e == "plan_card" for e, _ in first)

    second = await chat(api, h, "男，有社保，预算五千", thread)
    card = card_of(second)
    assert [i["category"] for i in card["items"]] == ["medical", "critical_illness", "accident"]
    assert text_of(second).startswith("医疗险报销大额住院费用")
    assert await plan_status(db_session, card["plan_id"]) == "pending"
    # 抽取提示词里带着已知的槽位：第二句只补了性别、社保、预算
    assert '"age": 30' in json_llm.prompts_seen[3]

    # 历史消息里有方案卡片和它的状态
    msgs = (await api.get(f"/api/v1/threads/{thread}/messages", headers=h)).json()
    assert msgs[-1]["plan"]["plan_id"] == card["plan_id"]
    assert msgs[-1]["plan"]["status"] == "pending"

    # 旧的 / 不存在的方案 id 不能操作
    stale = {"thread_id": thread, "plan_id": card["plan_id"] + 999, "action": "confirm"}
    assert (await api.post("/api/v1/chat/resume", json=stale, headers=h)).status_code == 409

    resp = await api.post(
        "/api/v1/chat/resume",
        json={"thread_id": thread, "plan_id": card["plan_id"], "action": "confirm"},
        headers=h,
    )
    events = parse_sse(resp.text)
    assert ("plan_status", {"plan_id": card["plan_id"], "status": "confirmed"}) in events
    assert text_of(events) == prompts.ADVISOR_CONFIRMED
    assert await plan_status(db_session, card["plan_id"]) == "confirmed"
    # 方案有了结果就清空需求：下一个方案（可能是给别人配的）重新采集
    state = await rt.graph.aget_state({"configurable": {"thread_id": thread}})
    assert state.values["slots"] == {}

    # 确认后不能再操作；我的方案里能看到
    again = {"thread_id": thread, "plan_id": card["plan_id"], "action": "cancel"}
    assert (await api.post("/api/v1/chat/resume", json=again, headers=h)).status_code == 409
    plans = (await api.get("/api/v1/plans", headers=h)).json()
    assert [p["id"] for p in plans] == [card["plan_id"]]
    assert plans[0]["status"] == "confirmed"
    msgs = (await api.get(f"/api/v1/threads/{thread}/messages", headers=h)).json()
    assert [m["content"] for m in msgs[-2:]] == ["确认方案", prompts.ADVISOR_CONFIRMED]
    # 别人看不到这个方案
    other = await token_for(api, "advisor_other")
    assert (await api.get(f"/api/v1/plans/{card['plan_id']}", headers=other)).status_code == 404
    assert answer_llm.prompts_seen  # 方案说明调用了回答模型


async def test_free_text_modify_then_ask_something_else(
    api: AsyncClient,  # noqa: F811
    box: Any,  # noqa: F811
    factory: async_sessionmaker[AsyncSession],  # noqa: F811
    db_session: AsyncSession,
    advisor_products: dict[str, int],
) -> None:
    rt, json_llm, _, _ = make_runtime(
        factory,
        json_replies=[
            route("advisor", []),
            extract(age=30, gender="male", social_security=True, budget=5000),
            review("modify"),  # 听不懂要怎么改：提示一下，继续等
            review("modify", budget=600),  # 改预算：旧方案作废，生成新方案
            review("other"),  # 转去问条款：方案过期，按普通消息路由
            route("clause_qa", []),
        ],
        answer_replies=["说明一", "说明二"],
    )
    box.runtime = rt
    h = await token_for(api, "advisor2")
    first = await chat(api, h, "30 岁男，有社保，预算 5000，帮我配保险")
    thread = next(d["id"] for e, d in first if e == "thread")
    old = card_of(first)

    unclear = await chat(api, h, "嗯……再看看", thread)
    assert text_of(unclear) == prompts.ADVISOR_UNCLEAR_CHANGE
    assert await plan_status(db_session, old["plan_id"]) == "pending"

    changed = await chat(api, h, "预算改成 600", thread)
    new = card_of(changed)
    assert await plan_status(db_session, old["plan_id"]) == "superseded"
    assert await plan_status(db_session, new["plan_id"]) == "pending"
    assert new["slots"]["budget"] == "600.00"
    assert Decimal(new["total_annual_premium"]) <= 600
    assert ("plan_status", {"plan_id": old["plan_id"], "status": "superseded"}) in changed
    # 审核提示词里带着当前方案，模型能据此理解"预算改成 600"
    assert "当前方案" in json_llm.prompts_seen[3]

    elsewhere = await chat(api, h, "等待期多久？", thread)
    assert await plan_status(db_session, new["plan_id"]) == "expired"
    assert "请问您想了解哪款产品" in text_of(elsewhere)
    state = await rt.graph.aget_state({"configurable": {"thread_id": thread}})
    assert not state.interrupts and state.values["last_workflow"] == "clause_qa"


async def test_edit_removes_product_and_regenerates(
    api: AsyncClient,  # noqa: F811
    box: Any,  # noqa: F811
    factory: async_sessionmaker[AsyncSession],  # noqa: F811
    db_session: AsyncSession,
    advisor_products: dict[str, int],
) -> None:
    rt, _, _, _ = make_runtime(
        factory,
        json_replies=[
            route("advisor", []),
            extract(age=30, gender="female", social_security=False, budget=5000),
        ],
        answer_replies=["说明一", "说明二"],
    )
    box.runtime = rt
    h = await token_for(api, "advisor3")
    first = await chat(api, h, "30 岁女，没社保，预算 5000")
    thread = next(d["id"] for e, d in first if e == "thread")
    card = card_of(first)
    accident = next(i for i in card["items"] if i["category"] == "accident")

    resp = await api.post(
        "/api/v1/chat/resume",
        json={
            "thread_id": thread,
            "plan_id": card["plan_id"],
            "action": "edit",
            "remove_product_ids": [accident["product_id"]],
        },
        headers=h,
    )
    new = card_of(parse_sse(resp.text))
    assert accident["product_name"] not in [i["product_name"] for i in new["items"]]
    assert await plan_status(db_session, card["plan_id"]) == "superseded"
    # 意外险只有一款候选，去掉后这个险种没配上，并说明原因
    assert [d["category"] for d in new["dropped"]] == ["accident"]
    msgs = (await api.get(f"/api/v1/threads/{thread}/messages", headers=h)).json()
    assert f"换一款：{accident['product_name']}" in [m["content"] for m in msgs]
    statuses = {m["plan"]["plan_id"]: m["plan"]["status"] for m in msgs if m["plan"]}
    assert statuses == {card["plan_id"]: "superseded", new["plan_id"]: "pending"}


async def test_no_plan_when_budget_too_small(
    api: AsyncClient,  # noqa: F811
    box: Any,  # noqa: F811
    factory: async_sessionmaker[AsyncSession],  # noqa: F811
    advisor_products: dict[str, int],
) -> None:
    rt, _, answer_llm, _ = make_runtime(
        factory,
        json_replies=[
            route("advisor", []),
            extract(age=30, gender="male", social_security=True, budget=10),
        ],
    )
    box.runtime = rt
    h = await token_for(api, "advisor4")
    events = await chat(api, h, "30 岁男有社保，预算 10 块")
    assert text_of(events).startswith(
        "按您目前的条件（30 周岁，男，有社保，年预算 10 元）暂时配不出方案"
    )
    assert not any(e == "plan_card" for e, _ in events)
    assert answer_llm.prompts_seen == []


async def test_history_message_without_plan_has_null_plan() -> None:
    from app.services.chat_service import _with_status

    assert _with_status(None, {}) is None
    assert _with_status({"plan_id": 3}, {3: "confirmed"}) == {"plan_id": 3, "status": "confirmed"}
    assert AIMessage("x").additional_kwargs.get("plan") is None


async def test_deleting_thread_expires_its_pending_plan(
    api: AsyncClient,  # noqa: F811
    box: Any,  # noqa: F811
    factory: async_sessionmaker[AsyncSession],  # noqa: F811
    db_session: AsyncSession,
    advisor_products: dict[str, int],
) -> None:
    """待确认的方案只能通过会话的中断确认；会话删掉后不能还挂着「待确认」。"""
    rt, _, _, _ = make_runtime(
        factory,
        json_replies=[
            route("advisor", []),
            extract(age=30, gender="male", social_security=True, budget=5000),
        ],
        answer_replies=["说明"],
    )
    box.runtime = rt
    h = await token_for(api, "advisor5")
    events = await chat(api, h, "30 岁男有社保，预算 5000")
    thread = next(d["id"] for e, d in events if e == "thread")
    card = card_of(events)
    assert (await api.delete(f"/api/v1/threads/{thread}", headers=h)).status_code == 204
    assert await plan_status(db_session, card["plan_id"]) == "expired"
    assert (await api.get("/api/v1/plans", headers=h)).json() == []


async def test_edit_drops_category_and_empty_edit_says_so(
    api: AsyncClient,  # noqa: F811
    box: Any,  # noqa: F811
    factory: async_sessionmaker[AsyncSession],  # noqa: F811
    db_session: AsyncSession,
    advisor_products: dict[str, int],
) -> None:
    rt, _, _, _ = make_runtime(
        factory,
        json_replies=[
            route("advisor", []),
            extract(age=30, gender="male", social_security=True, budget=5000),
        ],
        answer_replies=["说明一", "说明二"],
    )
    box.runtime = rt
    h = await token_for(api, "advisor6")
    first = await chat(api, h, "30 岁男有社保，预算 5000")
    thread = next(d["id"] for e, d in first if e == "thread")
    card = card_of(first)

    async def resume(**body: Any) -> list[tuple[str, dict[str, Any]]]:
        payload = {"thread_id": thread, "plan_id": card["plan_id"], "action": "edit", **body}
        resp = await api.post("/api/v1/chat/resume", json=payload, headers=h)
        assert resp.status_code == 200, resp.text
        return parse_sse(resp.text)

    # 什么都没选（或选的不在方案里）：说一声，方案保持待确认
    nothing = await resume(remove_product_ids=[999999])
    assert text_of(nothing) == prompts.ADVISOR_NOTHING_CHANGED
    assert await plan_status(db_session, card["plan_id"]) == "pending"
    assert (await api.post(
        "/api/v1/chat/resume",
        json={"thread_id": thread, "plan_id": card["plan_id"], "action": "edit",
              "remove_categories": ["car"]},
        headers=h,
    )).status_code == 422  # fmt: skip

    # 不要意外险：新方案里没有意外险，也不算「没配上」
    dropped = await resume(remove_categories=["accident"])
    new = card_of(dropped)
    assert [i["category"] for i in new["items"]] == ["medical", "critical_illness"]
    assert new["dropped"] == []
    assert new["slots"]["categories"] == ["medical", "critical_illness"]
    msgs = (await api.get(f"/api/v1/threads/{thread}/messages", headers=h)).json()
    assert "不要：意外险" in [m["content"] for m in msgs]

    # 剩下的险种全都不要：等于取消
    card = new
    gone = await resume(remove_categories=["medical", "critical_illness"])
    assert text_of(gone) == prompts.ADVISOR_CANCELLED
    assert await plan_status(db_session, new["plan_id"]) == "cancelled"


def test_edit_label() -> None:
    from app.agent.graph import edit_label

    plan = {
        "items": [
            {"product_id": 1, "product_name": "甲医疗", "category": "medical",
             "category_label": "医疗险"},
            {"product_id": 2, "product_name": "乙意外", "category": "accident",
             "category_label": "意外险"},
        ]
    }  # fmt: skip
    assert edit_label(plan, [1], ["accident"]) == "换一款：甲医疗；不要：意外险"
