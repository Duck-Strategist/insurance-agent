"""画像（M7）：抽取结果 → 建议增量、建议合并、画像接口、对话里推送建议、
顾问从画像预填、理赔咨询。"""

import json
import re
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import prompts
from app.agent.advisor import Slots, merge_slots, prefill, profile_notice
from app.agent.profile import (
    build_suggestion,
    estimate_birth,
    held_coverage,
    profile_slots,
)
from app.models import User
from app.rag.retriever import SearchMode
from app.schemas.profile import (
    Coverage,
    FamilyMember,
    ProfileData,
    ProfilePatch,
    age_on,
)
from app.services.chat_service import stream_answer
from app.services.profile_service import ProfileService, merge_patch
from tests.test_advisor import advisor_products, card_of, chat, extract  # noqa: F401
from tests.test_agent import (  # noqa: F401
    ConstLLM,
    FakeRetriever,
    hit,
    make_runtime,
    products,
    route,
    session_factory,
    text_of,
)
from tests.test_chat_api import api, box, factory, token_for  # noqa: F401

TODAY = date(2026, 11, 22)
CATALOG = {7: ("达尔文12号重大疾病保险", "critical_illness"), 9: ("小蜜蜂6号", "accident")}


def labels(s: Any) -> list[str]:
    return [i.label for i in s.items]


# ---------- 纯函数 ----------


def test_estimate_birth_keeps_age_now() -> None:
    for age in (0, 1, 30, 105):
        assert age_on(estimate_birth(age, TODAY), TODAY) == age
    leap = date(2028, 2, 29)
    assert age_on(estimate_birth(31, leap), leap) == 31


def test_suggestion_only_contains_new_or_changed_facts() -> None:
    current = ProfileData(
        birth_date=date(1996, 3, 1), gender="male", social_security=True, city="杭州"
    )
    raw = {
        "self": {
            "age": 30,  # 与已存的出生日期一致（30 周岁）：不建议
            "gender": "male",
            "social_security": False,  # 变了
            "city": "上海",
            "annual_budget": "5000",
        }
    }
    s = build_suggestion(current, raw, CATALOG, TODAY)
    assert s is not None
    assert labels(s) == ["社保", "城市", "年保费预算"]
    assert s.patch == {"social_security": False, "city": "上海", "annual_budget": "5000.00"}
    # 什么都没变时没有建议
    assert build_suggestion(current, {"self": {"age": 30, "city": "杭州"}}, CATALOG, TODAY) is None
    assert build_suggestion(current, {}, CATALOG, TODAY) is None


def test_suggestion_rejects_invalid_values() -> None:
    raw = {
        "self": {"age": 200, "gender": "男", "social_security": "yes", "annual_budget": 1.5e9},
        "family": [{"relation": "friend", "age": 30}, "x"],
        "coverage": [{"insured": "boss", "product_name": "某险"}, {"insured": "self"}],
    }
    assert build_suggestion(ProfileData(), raw, CATALOG, TODAY) is None


def test_budget_said_while_talking_about_family_is_not_saved_as_own() -> None:
    raw = {
        "self": {"annual_budget": 3000},
        "family": [{"relation": "spouse", "age": 30, "gender": "female"}],
    }
    s = build_suggestion(ProfileData(), raw, CATALOG, TODAY)
    assert s is not None and labels(s) == ["新增家庭成员"]
    assert "annual_budget" not in s.patch


def test_age_becomes_estimated_birth_date() -> None:
    s = build_suggestion(ProfileData(), {"self": {"age": 30}}, CATALOG, TODAY)
    assert s is not None and s.patch["birth_date_estimated"] is True
    assert age_on(date.fromisoformat(s.patch["birth_date"]), TODAY) == 30
    assert s.items[0].value == "30 周岁（按年龄估算出生日期）"
    exact = build_suggestion(ProfileData(), {"self": {"birth_date": "1990-05-06"}}, CATALOG, TODAY)
    assert exact is not None and exact.patch["birth_date_estimated"] is False


def test_family_members_are_matched_not_duplicated() -> None:
    wife = FamilyMember(id="w1", relation="spouse", gender="female")
    son = FamilyMember(id="s1", relation="child", gender="male", birth_date=date(2021, 1, 1))
    current = ProfileData(family=[wife, son])
    raw = {
        "family": [
            {"relation": "spouse", "age": 32},  # 更新已有配偶
            {"relation": "child", "gender": "male", "age": 5},  # 就是 s1，没有新信息
            {"relation": "child", "gender": "female", "age": 2, "name": "女儿"},  # 新成员
        ]
    }
    s = build_suggestion(current, raw, CATALOG, TODAY)
    assert s is not None
    assert labels(s) == ["更新家庭成员", "新增家庭成员"]
    ids = [m["id"] for m in s.patch["family"]]
    assert ids[0] == "w1" and re.fullmatch(r"[0-9a-f]{8}", ids[1])
    assert s.items[0].value == "配偶，32 周岁（估算），女"


def test_coverage_uses_catalog_ids_and_member_ids() -> None:
    current = ProfileData(
        family=[FamilyMember(id="w1", relation="spouse")],
        existing_coverage=[Coverage(insured="self", product_id=9, product_name="小蜜蜂6号")],
    )
    raw = {
        "coverage": [
            {"insured": "spouse", "product_id": 7, "product_name": "达尔文", "sum_assured": 300000},
            {"insured": "self", "product_id": 9},  # 已登记过
            # 编造的 id 不在目录里：当成库外产品，用模型给的名字
            {"insured": "self", "product_id": 999, "product_name": "某公司百万医疗"},
            {"insured": "parent", "product_name": "防癌险", "start_date": "2024-01-01"},
        ]
    }
    s = build_suggestion(current, raw, CATALOG, TODAY)
    assert s is not None
    cov = s.patch["existing_coverage"]
    assert cov[0] == {
        "insured": "w1",
        "product_id": 7,
        "product_name": "达尔文12号重大疾病保险",  # 名字以产品库为准
        "category": "critical_illness",
        "sum_assured": "300000.00",
        "start_date": None,
    }
    assert cov[1]["product_id"] is None and cov[1]["product_name"] == "某公司百万医疗"
    # 父母还不在画像里：同时建议新增这位成员，保障指向它
    parent = next(m for m in s.patch["family"] if m["relation"] == "parent")
    assert cov[2]["insured"] == parent["id"]
    assert "配偶 · 达尔文12号重大疾病保险（保额 30 万元）" in [i.value for i in s.items]
    ProfilePatch.model_validate(s.patch)


def test_coverage_for_ambiguous_child_is_skipped() -> None:
    kids = [FamilyMember(id="a", relation="child"), FamilyMember(id="b", relation="child")]
    raw = {"coverage": [{"insured": "child", "product_id": 9}]}
    assert build_suggestion(ProfileData(family=kids), raw, CATALOG, TODAY) is None


def test_merge_patch_upserts_and_never_deletes() -> None:
    current = ProfileData(
        city="杭州",
        family=[FamilyMember(id="w1", relation="spouse", name="老婆", gender="female")],
        existing_coverage=[Coverage(insured="self", product_id=9, product_name="小蜜蜂6号")],
    )
    patch = ProfilePatch.model_validate(
        {
            "gender": "male",
            "family": [
                # 建议生成时画像里还没有配偶（新 id）：合并到已有配偶，不加第二位
                {"id": "n1", "relation": "spouse", "birth_date": "1994-01-01",
                 "birth_date_estimated": True},
                {"id": "k1", "relation": "child"},
            ],
            "existing_coverage": [
                {"insured": "n1", "product_id": 7, "product_name": "x"},
                {"insured": "self", "product_id": 9, "product_name": "小蜜蜂6号",
                 "sum_assured": "500000"},
            ],
        }
    )  # fmt: skip
    merged = merge_patch(current, patch)
    assert merged.city == "杭州" and merged.gender == "male"
    wife = merged.family[0]
    assert (wife.id, wife.name, wife.gender) == ("w1", "老婆", "female")
    assert wife.birth_date == date(1994, 1, 1) and wife.birth_date_estimated
    assert [m.id for m in merged.family] == ["w1", "k1"]
    assert [(c.insured, c.product_id) for c in merged.existing_coverage] == [
        ("self", 9),
        ("w1", 7),
    ]
    assert merged.existing_coverage[0].sum_assured == Decimal("500000.00")


def test_profile_slots_and_prefill() -> None:
    profile = ProfileData(
        birth_date=date(1996, 1, 1),
        gender="male",
        social_security=True,
        annual_budget=Decimal("5000"),
        family=[
            FamilyMember(id="w", relation="spouse", birth_date=date(1998, 6, 1), gender="female"),
            FamilyMember(id="a", relation="child"),
            FamilyMember(id="b", relation="child"),
        ],
    )
    assert profile_slots(profile, "self", TODAY) == {
        "budget": "5000.00",
        "age": 30,
        "gender": "male",
        "social_security": True,
    }
    # 给家人配：不带入预算（画像里是本人的年预算），社保没登记就不带
    assert profile_slots(profile, "spouse", TODAY) == {"age": 28, "gender": "female"}
    assert profile_slots(profile, "child", TODAY) == {}  # 两个孩子，不知道是谁

    slots = prefill(Slots(age=31), profile_slots(profile, "self", TODAY))
    assert slots["age"] == 31  # 用户说过的不覆盖
    assert slots["from_profile"] == ["gender", "social_security", "budget"]
    assert (
        profile_notice(slots)
        == "已从您的画像带入：男，有社保，年预算 5,000 元；如有变化请直接告诉我。"
    )
    # 用户改口：这一项不再算画像带入
    assert merge_slots(slots, {"budget": 8000})["from_profile"] == ["gender", "social_security"]
    # 换了被保险人：上一个人的年龄、性别、社保都作废（年龄 31 是用户说的也一样），
    # 画像带入的预算作废；同一句里说的新被保险人的信息照常写入
    switched = merge_slots(slots, {"insured": "spouse", "gender": "female"})
    assert switched == {"insured": "spouse", "from_profile": [], "gender": "female"}
    told = merge_slots(Slots(age=31, budget="3000.00"), {"insured": "child"})
    assert told == {"budget": "3000.00", "insured": "child", "from_profile": []}
    # 家人以外的人：画像里没有，不预填
    assert profile_slots(profile, "other", TODAY) == {}


def test_held_coverage_by_insured() -> None:
    profile = ProfileData(
        family=[FamilyMember(id="w", relation="spouse")],
        existing_coverage=[
            Coverage(insured="self", product_name="甲"),
            Coverage(insured="w", product_name="乙"),
        ],
    )
    assert [c.product_name for c in held_coverage(profile, "self")] == ["甲"]
    assert [c.product_name for c in held_coverage(profile, "spouse")] == ["乙"]
    assert held_coverage(profile, "parent") == []


# ---------- 画像接口 ----------


async def test_profile_api_roundtrip_and_sources(
    api: AsyncClient,  # noqa: F811
    advisor_products: dict[str, int],  # noqa: F811
) -> None:
    h = await token_for(api, "profile1")
    empty = (await api.get("/api/v1/profile", headers=h)).json()
    assert empty["updated_at"] is None and empty["family"] == [] and empty["field_sources"] == {}

    darwin = advisor_products["达尔文12号重大疾病保险"]
    body = {
        "birth_date": "1996-01-01",
        "gender": "male",
        "social_security": True,
        "annual_budget": "5000",
        "family": [{"id": "w1", "relation": "spouse", "gender": "female"}],
        "existing_coverage": [
            # 产品名以库为准，前端传错也会被纠正
            {"insured": "w1", "product_id": darwin, "product_name": "随便写"},
            {"insured": "self", "product_name": "公司团险", "sum_assured": 100000},
        ],
    }
    out = (await api.put("/api/v1/profile", json=body, headers=h)).json()
    assert out["annual_budget"] == "5000.00"
    assert out["existing_coverage"][0]["product_name"] == "达尔文12号重大疾病保险"
    assert out["existing_coverage"][0]["category"] == "critical_illness"
    assert out["existing_coverage"][1]["sum_assured"] == "100000.00"
    assert out["field_sources"] == {
        "birth_date": "manual",
        "gender": "manual",
        "social_security": "manual",
        "annual_budget": "manual",
        "family": "manual",
        "existing_coverage": "manual",
    }

    # 对话建议：只合并、标来源；年龄估算的出生日期标为 chat_age
    patch = {"birth_date": "1995-05-22", "birth_date_estimated": True, "city": "杭州"}
    out = (await api.post("/api/v1/profile/suggestions", json=patch, headers=h)).json()
    assert out["city"] == "杭州" and out["gender"] == "male"
    assert out["field_sources"]["birth_date"] == "chat_age"
    assert out["field_sources"]["city"] == "chat"
    assert out["field_sources"]["gender"] == "manual"  # 没变的字段保留原来源
    assert len(out["existing_coverage"]) == 2

    # 清空字段时去掉来源
    body["birth_date"] = None
    out = (await api.put("/api/v1/profile", json={**body, "city": "杭州"}, headers=h)).json()
    assert "birth_date" not in out["field_sources"] and out["age"] is None

    # 别人看不到
    other = await token_for(api, "profile2")
    assert (await api.get("/api/v1/profile", headers=other)).json()["updated_at"] is None


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"existing_coverage": [{"insured": "x1", "product_name": "甲"}]}, "不在家庭成员里"),
        (
            {"family": [{"id": "a", "relation": "spouse"}, {"id": "b", "relation": "spouse"}]},
            "只能登记一位配偶",
        ),
        ({"family": [{"id": "a", "relation": "child"}, {"id": "a", "relation": "child"}]}, "重复"),
        ({"annual_budget": 5000.5}, "字符串或整数"),
        ({"birth_date": "2099-01-01"}, "不能晚于今天"),
        ({"occupation_class": 7}, ""),
        (
            {
                "existing_coverage": [
                    {"insured": "self", "product_id": 999999999, "product_name": "x"}
                ]
            },
            "产品不存在",
        ),  # fmt: skip
        ({"unknown": 1}, ""),
    ],
)
async def test_profile_validation(
    api: AsyncClient,  # noqa: F811
    body: dict[str, Any],
    message: str,
) -> None:
    h = await token_for(api, "profile3")
    resp = await api.put("/api/v1/profile", json=body, headers=h)
    assert resp.status_code == 422, resp.text
    assert message in json.dumps(resp.json(), ensure_ascii=False)


async def test_stale_suggestion_for_deleted_member_is_rejected(
    api: AsyncClient,  # noqa: F811
) -> None:
    h = await token_for(api, "profile4")
    patch = {"existing_coverage": [{"insured": "gone1", "product_name": "甲"}]}
    resp = await api.post("/api/v1/profile/suggestions", json=patch, headers=h)
    assert resp.status_code == 422
    assert "不在家庭成员里" in resp.json()["detail"]
    assert (await api.get("/api/v1/profile", headers=h)).json()["updated_at"] is None


async def test_profile_requires_login(api: AsyncClient) -> None:  # noqa: F811
    assert (await api.get("/api/v1/profile")).status_code == 401
    assert (await api.put("/api/v1/profile", json={})).status_code == 401


# ---------- 对话：推送画像建议 ----------


async def make_user(sf: async_sessionmaker[AsyncSession], name: str) -> int:
    async with sf() as s:
        user = User(username=name, password_hash="x")
        s.add(user)
        await s.commit()
        return user.id


async def ask_as(rt: Any, text: str, user_id: int, thread: str = "t") -> list[tuple[str, Any]]:
    events = []
    async for raw in stream_answer(rt, thread, text, user_id):
        head, data = raw.strip().split("\n")
        events.append((head.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


async def test_profile_suggestion_streamed_in_parallel_with_answer(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811
    products: dict[str, int],  # noqa: F811
) -> None:
    uid = await make_user(session_factory, "suggest1")
    profile_llm = ConstLLM(json.dumps({"self": {"age": 30, "city": "杭州"}}))
    rt, _, _, _ = make_runtime(
        session_factory,
        json_replies=[route("other", [])],
        profile_llm=profile_llm,
    )
    events = await ask_as(rt, "我 30 岁住杭州，今天天气怎么样", uid)
    assert text_of(events) == prompts.OUT_OF_SCOPE
    sugg = next(d["suggestion"] for e, d in events if e == "profile_suggestion")
    assert [i["label"] for i in sugg["items"]] == ["年龄", "城市"]
    assert sugg["patch"]["city"] == "杭州"
    assert events[-1] == ("done", {})
    # 抽取提示词里有产品目录，产品 id 只能从里面选
    assert f"- {products['ok']}：对话测试医疗险" in profile_llm.prompts_seen[0]
    assert f"今天是 {date.today().isoformat()}" in profile_llm.prompts_seen[0]
    # 只是建议：没有写库
    async with session_factory() as s:
        assert await ProfileService(s).get(uid) is None


async def test_profile_extraction_skipped_or_swallowed(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811
    products: dict[str, int],  # noqa: F811
) -> None:
    uid = await make_user(session_factory, "suggest2")

    class Boom(ConstLLM):
        async def ainvoke(self, *a: Any, **kw: Any) -> Any:
            raise RuntimeError("upstream down")

    rt, _, _, _ = make_runtime(
        session_factory, json_replies=[route("other", [])], profile_llm=Boom()
    )
    events = await ask_as(rt, "今天天气怎么样", uid)
    assert events[-1] == ("done", {})  # 抽取失败不影响回答
    assert not any(e == "profile_suggestion" for e, _ in events)

    quiet = ConstLLM(json.dumps({"self": {"city": "杭州"}}))
    rt, _, _, _ = make_runtime(session_factory, json_replies=[], profile_llm=quiet)
    await ask_as(rt, "你好", uid, thread="t2")
    async for _ in stream_answer(rt, "t3", "你好"):  # 没有用户（未登录的调用）
        pass
    assert quiet.prompts_seen == []  # 寒暄、没有用户时都不抽取


# ---------- 顾问：新会话自动带入画像（M7 验收） ----------


async def test_new_thread_prefills_advisor_from_profile(
    api: AsyncClient,  # noqa: F811
    box: Any,  # noqa: F811
    factory: async_sessionmaker[AsyncSession],  # noqa: F811
    advisor_products: dict[str, int],  # noqa: F811
) -> None:
    rt, json_llm, _, _ = make_runtime(
        factory,
        json_replies=[route("advisor", []), extract()],  # 这句话里什么都没说
        answer_replies=["医疗险报销住院费用。"],
    )
    box.runtime = rt
    h = await token_for(api, "prefill1")
    profile = {
        "birth_date": "1996-01-01",
        "gender": "female",
        "social_security": True,
        "annual_budget": "5000",
    }
    assert (await api.put("/api/v1/profile", json=profile, headers=h)).status_code == 200

    events = await chat(api, h, "帮我配一份保险")
    card = card_of(events)
    age = age_on(date(1996, 1, 1), date.today())
    assert card["slots"]["age"] == age and card["slots"]["gender"] == "female"
    assert card["slots"]["from_profile"] == ["age", "gender", "social_security", "budget"]
    assert text_of(events).startswith(
        f"已从您的画像带入：{age} 周岁，女，有社保，年预算 5,000 元；如有变化请直接告诉我。"
        "医疗险报销住院费用。"
    )


async def test_advisor_for_spouse_uses_member_and_asks_budget(
    api: AsyncClient,  # noqa: F811
    box: Any,  # noqa: F811
    factory: async_sessionmaker[AsyncSession],  # noqa: F811
    advisor_products: dict[str, int],  # noqa: F811
) -> None:
    rt, _, _, _ = make_runtime(
        factory,
        json_replies=[
            route("advisor", []),
            extract(insured="spouse"),
            route("advisor", []),
            extract(budget=3000),
        ],
        answer_replies=["说明"],
    )
    box.runtime = rt
    h = await token_for(api, "prefill2")
    profile = {
        "annual_budget": "8000",
        "family": [
            {"id": "w1", "relation": "spouse", "birth_date": "1998-01-01", "gender": "female",
             "social_security": False},
        ],
    }  # fmt: skip
    await api.put("/api/v1/profile", json=profile, headers=h)
    first = await chat(api, h, "给我老婆配一份")
    thread = next(d["id"] for e, d in first if e == "thread")
    age = age_on(date(1998, 1, 1), date.today())
    assert text_of(first) == (
        f"已从您的画像带入：{age} 周岁，女，无社保；如有变化请直接告诉我。"
        "已了解：为配偶配置。"  # 画像带入的不再重复一遍
        "为了给您配置合适的方案，还需要了解：每年大概能接受的保费预算。"
    )
    card = card_of(await chat(api, h, "三千", thread))
    assert card["slots"]["insured"] == "spouse" and card["slots"]["budget"] == "3000.00"


# ---------- 理赔咨询 ----------


async def put_profile(sf: async_sessionmaker[AsyncSession], uid: int, data: Any) -> None:
    async with sf() as s:
        await ProfileService(s).replace(uid, ProfileData.model_validate(data))


async def test_claim_without_registered_coverage_asks_which_product(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811
    products: dict[str, int],  # noqa: F811
) -> None:
    uid = await make_user(session_factory, "claim1")
    rt, _, answer_llm, retr = make_runtime(
        session_factory,
        json_replies=[route("claim", []), json.dumps({"insured": "spouse", "query": "住院"})],
    )
    events = await ask_as(rt, "我老婆住院了能赔吗", uid)
    text = text_of(events)
    assert text.startswith("为了按您的保单条款判断，需要先知道您配偶投保的是哪款产品")
    assert "对话测试医疗险" in text and "我的画像" in text
    assert retr.calls == [] and answer_llm.prompts_seen == []


async def test_claim_searches_only_held_products_with_four_queries(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811
    products: dict[str, int],  # noqa: F811
) -> None:
    uid = await make_user(session_factory, "claim2")
    await put_profile(
        session_factory,
        uid,
        {
            "existing_coverage": [
                {"insured": "self", "product_id": products["ok"], "product_name": "x",
                 "sum_assured": "2000000", "start_date": "2026-10-01"},
            ]
        },
    )  # fmt: skip
    retr = FakeRetriever([hit(1, "第三条 等待期"), hit(2, "第九条 责任免除"), hit(3, "理赔材料")])
    rt, _, answer_llm, _ = make_runtime(
        session_factory,
        json_replies=[
            route("claim", []),
            json.dumps({"insured": "self", "query": "阑尾炎住院能赔吗"}),
            route("claim", []),  # 追问：沿用上一轮的产品
            json.dumps({"insured": "self", "query": "阑尾炎住院要什么材料"}),
        ],
        answer_replies=["**初步判断**：可能属于保障范围[1]，但要看等待期[1][9]。", "材料见[3]"],
        retriever=retr,
    )
    events = await ask_as(rt, "我阑尾炎住院了能赔吗", uid)
    doc = [products["doc"]]
    assert sorted(retr.calls) == sorted(
        [
            ("阑尾炎住院能赔吗", SearchMode.HYBRID_RERANK, doc),
            ("阑尾炎住院能赔吗" + prompts.CLAIM_EXCLUSION_SUFFIX, SearchMode.HYBRID_RERANK, doc),
            (prompts.CLAIM_WAITING_QUERY, SearchMode.HYBRID_RERANK, doc),
            (prompts.CLAIM_MATERIALS_QUERY, SearchMode.HYBRID_RERANK, doc),
        ]
    )
    text = text_of(events)
    # 模型没写免责声明：代码补上
    assert text.endswith("\n\n" + prompts.CLAIM_DISCLAIMER)
    cites = next(d["items"] for e, d in events if e == "citations")
    assert [c["index"] for c in cites] == [1]  # [9] 越界丢弃
    # 保单信息给了模型：生效日期用来判断等待期
    prompt = answer_llm.prompts_seen[0]
    days = (date.today() - date(2026, 10, 1)).days
    assert (
        "对话测试医疗险（被保险人：本人，保额 2,000,000.00 元，"
        f"生效日期 2026-10-01，到今天已生效 {days} 天）"
    ) in prompt
    assert f"今天是 {date.today().isoformat()}" in prompt
    # 四路结果按章节去重（假检索器每路返回同样的 3 个章节）
    assert prompt.count("《某条款》") == 3

    retr.calls.clear()
    await ask_as(rt, "那要什么材料", uid)
    assert {c[2][0] for c in retr.calls} == set(doc)


async def test_claim_follow_up_about_another_person_does_not_reuse_policy(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811
    products: dict[str, int],  # noqa: F811
) -> None:
    uid = await make_user(session_factory, "claim5")
    await put_profile(
        session_factory,
        uid,
        {
            "family": [{"id": "w1", "relation": "spouse"}],
            "existing_coverage": [
                {"insured": "self", "product_id": products["ok"], "product_name": "x"}
            ],
        },
    )
    rt, _, _, retr = make_runtime(
        session_factory,
        json_replies=[
            route("claim", []),
            json.dumps({"insured": "self", "query": "住院能赔吗"}),
            route("claim", []),  # 路由沿用上一轮的产品
            json.dumps({"insured": "spouse", "query": "老婆住院能赔吗"}),
            route("claim", []),
            json.dumps({"insured": "other", "query": "朋友住院能赔吗"}),
        ],
        answer_replies=["答[1]"],
    )
    await ask_as(rt, "我住院了能赔吗", uid)
    calls = len(retr.calls)
    # 换成配偶：不能拿本人的保单回答，配偶没登记就追问
    text = text_of(await ask_as(rt, "那我老婆住院呢", uid))
    assert text.startswith("为了按您的保单条款判断，需要先知道您配偶投保的是哪款产品")
    assert len(retr.calls) == calls
    text = text_of(await ask_as(rt, "我朋友住院了呢", uid))
    assert "需要先知道被保险人投保的是哪款产品" in text


async def test_claim_with_only_off_catalog_coverage_gets_general_guide(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811
    products: dict[str, int],  # noqa: F811
) -> None:
    uid = await make_user(session_factory, "claim3")
    await put_profile(
        session_factory,
        uid,
        {"existing_coverage": [{"insured": "self", "product_name": "公司团险"}]},
    )
    rt, _, answer_llm, retr = make_runtime(
        session_factory,
        json_replies=[route("claim", []), "坏掉的 JSON"],  # 抽取失败：按本人、原问题处理
    )
    text = text_of(await ask_as(rt, "我骨折了怎么理赔", uid))
    assert text.startswith("您名下登记的保障（公司团险）不在本平台的产品库里")
    assert text.endswith(prompts.CLAIM_DISCLAIMER)
    assert retr.calls == [] and answer_llm.prompts_seen == []


async def test_claim_mentioned_product_is_used_even_if_not_registered(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811
    products: dict[str, int],  # noqa: F811
) -> None:
    uid = await make_user(session_factory, "claim4")
    rt, _, answer_llm, retr = make_runtime(
        session_factory,
        json_replies=[
            route("claim", [products["ok"]]),
            json.dumps({"insured": "self", "query": "住院能赔吗"}),
            route("claim", [products["missing"]]),
            json.dumps({"insured": "self", "query": "骨折能赔吗"}),
        ],
        answer_replies=["可以[1]。最终能否赔付以保险公司审核为准。"],
    )
    events = await ask_as(rt, "我买了对话测试医疗险，住院能赔吗", uid)
    assert text_of(events).count(prompts.CLAIM_DISCLAIMER) == 1  # 模型写了就不重复补
    assert "画像里没有登记保额和生效日期" in answer_llm.prompts_seen[0]
    assert all(c[2] == [products["doc"]] for c in retr.calls)
    # 条款没入库的产品
    assert "还没有入库" in text_of(await ask_as(rt, "对话测试未入库险骨折能赔吗", uid))
