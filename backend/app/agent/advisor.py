"""顾问业务（M6）：需求槽位与方案生成。纯函数，不依赖 LLM 和数据库，可单测。

分工（spec §6.2、D47）：
- LLM 只把用户的话抽取成槽位（JSON），由这里逐项校验；
- 选哪些险种、哪款产品、什么保额，由这里的规则决定；保费全部来自报价引擎；
- LLM 再根据生成好的方案写几句说明，不写金额（金额以方案卡片为准）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, TypedDict

from app.pricing.engine import (
    PAY_TERMS,
    CriticalIllnessRule,
    ProductRule,
    QuoteResult,
    jsonable_params,
)
from app.pricing.tables import RateBook

CATEGORY_LABELS = {
    "medical": "医疗险",
    "critical_illness": "重疾险",
    "accident": "意外险",
    "life": "寿险",
}
# 预算不够时按这个顺序保留：先医疗（报销大额医疗费），再重疾（收入损失），再意外；
# 寿险（增额终身寿）偏储蓄，只在用户明确要时才配
PRIORITY = ("medical", "critical_illness", "accident", "life")
DEFAULT_CATEGORIES = ("medical", "critical_illness", "accident")
# 重疾默认从高到低尝试的保额档位：取预算内能买到的最高档
CI_SUM_ASSURED_STEPS = (500_000, 300_000, 200_000, 150_000, 100_000)

REQUIRED_SLOTS = {
    "age": "被保险人的年龄",
    "gender": "性别",
    "social_security": "是否有社保（医保）",
    "budget": "每年大概能接受的保费预算",
}
MAX_BUDGET = Decimal("10000000")


class Slots(TypedDict, total=False):
    age: int
    gender: str  # male / female
    social_security: bool
    budget: str  # 年预算（元），两位小数字符串；checkpoint 里不存 Decimal
    categories: list[str]
    ci_sum_assured: int  # 期望的重疾保额（元）
    life_annual_premium: str  # 寿险年交保费（元）
    excluded_product_ids: list[int]
    # 为谁配置（M7）：self / spouse / child / parent，没说时按本人
    insured: str
    # 从画像带入的槽位名：回复里告诉用户，用户改口时从这里去掉
    from_profile: list[str]


# ---------- 槽位 ----------


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _money(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > MAX_BUDGET:
        return None
    return amount.quantize(Decimal("0.01"))


# other：家人以外的人（朋友、同事），画像里没有，不预填
INSURED_LABELS = {
    "self": "本人",
    "spouse": "配偶",
    "child": "子女",
    "parent": "父母",
    "other": "他人",
}
# 描述被保险人的槽位：换了被保险人时都作废（不管是用户说的还是画像带入的）
PERSON_SLOTS = ("age", "gender", "social_security")


def merge_slots(current: Slots, raw: Mapping[str, Any]) -> Slots:
    """把 LLM 抽取出的槽位合并进当前槽位。每个字段单独校验，不合法的丢弃（保留原值），
    这样模型编出一个 200 岁或负数预算时，最多是多问一句，不会算出错误的方案。"""
    slots: Slots = Slots(**current)
    insured = raw.get("insured")
    if insured in INSURED_LABELS and insured != slots.get("insured", "self"):
        # 换了被保险人：上一个人的年龄、性别、社保不再适用；从画像带入的预算也作废
        # （画像里是本人的预算）。用户自己说的预算保留：多半是这次配置的预算
        for name in {*PERSON_SLOTS, *slots.get("from_profile", [])}:
            slots.pop(name, None)  # type: ignore[misc]
        slots["from_profile"] = []
        slots["insured"] = insured
    age = _int(raw.get("age"))
    if age is not None and 0 <= age <= 105:
        slots["age"] = age
    gender = raw.get("gender")
    if gender in ("male", "female"):
        slots["gender"] = gender
    ss = raw.get("social_security")
    if isinstance(ss, bool):
        slots["social_security"] = ss
    budget = _money(raw.get("budget"))
    if budget is not None:
        slots["budget"] = str(budget)
    cats = raw.get("categories")
    if isinstance(cats, list):
        valid = [c for c in PRIORITY if c in cats]
        if valid:
            slots["categories"] = valid
    sa = _int(raw.get("ci_sum_assured"))
    if sa is not None and 10_000 <= sa <= 5_000_000:
        slots["ci_sum_assured"] = sa
    life = _money(raw.get("life_annual_premium"))
    if life is not None:
        slots["life_annual_premium"] = str(life)
    # 用户自己说了的值不再算「来自画像」
    if slots.get("from_profile"):
        slots["from_profile"] = [
            n for n in slots["from_profile"] if n not in raw or slots.get(n) == current.get(n)
        ]
    return slots


def prefill(slots: Slots, known: Mapping[str, Any]) -> Slots:
    """缺的必填槽位用画像补上（known 来自 profile.profile_slots），用户说过的不覆盖。"""
    out = Slots(**slots)
    filled = list(out.get("from_profile", []))
    for name in REQUIRED_SLOTS:
        if name not in out and name in known:
            out[name] = known[name]  # type: ignore[literal-required]
            filled.append(name)
    if filled:
        out["from_profile"] = filled
    return out


def profile_notice(slots: Slots) -> str:
    """告诉用户哪些信息是从画像带入的：用户可能已经换了城市、有了社保。"""
    names = slots.get("from_profile") or []
    if not names:
        return ""
    known = describe_slots(Slots(**{n: slots[n] for n in names if n in slots}))  # type: ignore[literal-required, typeddict-item]
    return f"已从您的画像带入：{known}；如有变化请直接告诉我。"


def missing_slots(slots: Slots) -> list[str]:
    return [name for name in REQUIRED_SLOTS if name not in slots]


def ask_for(missing: Sequence[str]) -> str:
    items = "、".join(REQUIRED_SLOTS[m] for m in missing)
    return f"为了给您配置合适的方案，还需要了解：{items}。"


def describe_slots(slots: Slots) -> str:
    parts = []
    if slots.get("insured", "self") != "self":
        parts.append("为" + INSURED_LABELS[slots["insured"]] + "配置")
    if "age" in slots:
        parts.append(f"{slots['age']} 周岁")
    if "gender" in slots:
        parts.append("男" if slots["gender"] == "male" else "女")
    if "social_security" in slots:
        parts.append("有社保" if slots["social_security"] else "无社保")
    if "budget" in slots:
        parts.append(f"年预算 {Decimal(slots['budget']):,.0f} 元")
    cats = slots.get("categories")
    if cats:
        parts.append("关注" + "、".join(CATEGORY_LABELS[c] for c in cats))
    return "，".join(parts)


# ---------- 方案 ----------


@dataclass(frozen=True)
class Candidate:
    product_id: int
    name: str
    category: str
    rule: ProductRule
    book: RateBook
    min_premium: Decimal  # 产品页的起投金额，寿险默认按它报


def _pay_terms(rule: ProductRule, book: RateBook) -> list[str]:
    specs = {s.name: s for s in rule.param_specs(book)}
    terms = [v for v, _ in specs["pay_term"].choices] if "pay_term" in specs else []
    # 交费期越长，每年交得越少：先试最长的
    return sorted(terms, key=lambda t: PAY_TERMS[t][1], reverse=True)


def _base_params(c: Candidate, slots: Slots) -> dict[str, Any]:
    """按规则类型给出默认参数：最低档计划、最高免赔额、不加可选责任。"""
    specs = {s.name: s for s in c.rule.param_specs(c.book)}
    params: dict[str, Any] = {"age": slots["age"]}
    if "gender" in specs:
        params["gender"] = slots["gender"]
    if "social_security" in specs:
        params["social_security"] = slots["social_security"]
    if "plan" in specs:
        params["plan"] = specs["plan"].choices[0][0]  # 费率表里从低档到高档排列
    if "deductible" in specs:
        params["deductible"] = max((v for v, _ in specs["deductible"].choices), key=int)
    if "coverage" in specs:
        params["coverage"] = specs["coverage"].choices[0][0]  # 达尔文：终身
    if "annual_premium" in specs:
        params["annual_premium"] = slots.get("life_annual_premium") or str(c.min_premium)
    return params


def _sum_assured_options(c: Candidate, slots: Slots) -> list[str | None]:
    if not isinstance(c.rule, CriticalIllnessRule):
        return [None]
    spec = next(s for s in c.rule.param_specs(c.book) if s.name == "sum_assured")
    wanted = slots.get("ci_sum_assured")
    if spec.kind == "choice":
        tiers = sorted((int(v) for v, _ in spec.choices), reverse=True)
    else:
        tiers = list(CI_SUM_ASSURED_STEPS)
        if wanted is not None and wanted % 1000 == 0 and wanted not in tiers:
            tiers = sorted({*tiers, wanted}, reverse=True)
    if wanted is not None:
        # 用户说了保额：不超过它；都超过时退到最低一档
        capped = [t for t in tiers if t <= wanted]
        tiers = capped or [min(tiers)]
    return [str(t) for t in tiers]


@dataclass
class Configured:
    params: dict[str, Any]
    result: QuoteResult


def configure(c: Candidate, slots: Slots, remaining: Decimal) -> tuple[Configured | None, str]:
    """给一款产品找预算内的配置。返回 (配置, 原因)；配置为 None 时原因说明为什么配不了。"""
    base = _base_params(c, slots)
    cheapest_ok: Decimal | None = None
    last_reason = "不符合投保条件"
    terms: list[str | None] = list(_pay_terms(c.rule, c.book)) or [None]
    for sa in _sum_assured_options(c, slots):
        for term in terms:
            params = dict(base)
            if term is not None:
                params["pay_term"] = term
            if sa is not None:
                params["sum_assured"] = sa
            result = c.rule.quote(c.book, params)
            if result.status != "ok":
                last_reason = "；".join(result.messages) or last_reason
                continue
            assert result.annual_premium is not None
            if result.annual_premium <= remaining:
                return Configured(params, result), ""
            if cheapest_ok is None or result.annual_premium < cheapest_ok:
                cheapest_ok = result.annual_premium
    if cheapest_ok is not None:
        return None, f"超出剩余预算（最低需 {cheapest_ok:,.2f} 元/年）"
    return None, last_reason


def _item(c: Candidate, conf: Configured) -> dict[str, Any]:
    r = conf.result
    assert r.annual_premium is not None and r.total_premium is not None
    return {
        "product_id": c.product_id,
        "product_name": c.name,
        "category": c.category,
        "category_label": CATEGORY_LABELS[c.category],
        "params": jsonable_params(r.params),
        "annual_premium": str(r.annual_premium),
        "pay_years": r.pay_years,
        "total_premium": str(r.total_premium),
        "sum_assured": str(r.sum_assured) if r.sum_assured is not None else None,
        "rate_version": r.rate_version,
        "lines": [
            {"title": ln.title, "premium": str(ln.premium), "basis": ln.basis, "page": ln.page}
            for ln in r.lines
        ],
    }


def _better(c: Candidate, a: Configured, b: Configured) -> bool:
    """a 是否比 b 好：重疾先比保额（高的好），其余比保费（低的好）。"""
    ra, rb = a.result, b.result
    assert ra.annual_premium is not None and rb.annual_premium is not None
    if c.category == "critical_illness" and ra.sum_assured != rb.sum_assured:
        return (ra.sum_assured or 0) > (rb.sum_assured or 0)
    return ra.annual_premium < rb.annual_premium


def build_plan(candidates: Sequence[Candidate], slots: Slots) -> dict[str, Any]:
    """按险种优先级逐个配置，每个险种选一款产品，累计不超过年预算。"""
    budget = Decimal(slots["budget"])
    remaining = budget
    wanted = slots.get("categories") or list(DEFAULT_CATEGORIES)
    excluded = set(slots.get("excluded_product_ids", []))
    items: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    for category in PRIORITY:
        if category not in wanted:
            continue
        pool = [c for c in candidates if c.category == category and c.product_id not in excluded]
        best: tuple[Candidate, Configured] | None = None
        reasons: list[str] = []
        for c in pool:
            conf, reason = configure(c, slots, remaining)
            if conf is None:
                reasons.append(f"{c.name}：{reason}")
                continue
            if best is None or _better(c, conf, best[1]):
                best = (c, conf)
        label = CATEGORY_LABELS[category]
        if best is None:
            reason = "；".join(reasons) if reasons else "没有可报价的产品"
            dropped.append({"category": category, "category_label": label, "reason": reason})
            continue
        c, conf = best
        items.append(_item(c, conf))
        assert conf.result.annual_premium is not None
        remaining -= conf.result.annual_premium
    total = sum((Decimal(i["annual_premium"]) for i in items), Decimal(0))
    return {
        "slots": dict(slots),
        "items": items,
        "dropped": dropped,
        "budget": str(budget),
        "total_annual_premium": str(total.quantize(Decimal("0.01"))),
    }
