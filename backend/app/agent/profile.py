"""画像（M7）：把模型抽取的个人信息变成「建议保存的增量」，以及从画像给顾问预填槽位。
纯函数，不依赖 LLM 和数据库，可单测。

分工（spec D20、D47 的同一思路）：
- LLM 只把这句话里用户说的事实抽成 JSON；
- 这里逐项校验，和已保存的画像比较，只留下真正新增或变化的内容；
- 结果只是「建议」，推给前端，用户点保存后才写库。
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.schemas.profile import (
    MAX_AGE,
    RELATION_LABELS,
    Coverage,
    FamilyMember,
    ProfileData,
    ProfilePatch,
    ProfileSuggestion,
    SuggestionItem,
    age_on,
)

GENDER_LABELS = {"male": "男", "female": "女"}
CATEGORY_LABELS = {
    "medical": "医疗险",
    "critical_illness": "重疾险",
    "accident": "意外险",
    "life": "寿险",
}
MAX_BUDGET = Decimal("10000000")


# ---------- 校验单个值（不合法的返回 None，等于没抽到） ----------


def _age(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    return value if isinstance(value, int) and 0 <= value <= MAX_AGE else None


def _date(value: Any, today: date) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        d = date.fromisoformat(value.strip())
    except ValueError:
        return None
    return d if d <= today and age_on(d, today) <= MAX_AGE else None


def _money(value: Any, upper: Decimal = MAX_BUDGET) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > upper:
        return None
    return amount.quantize(Decimal("0.01"))


def estimate_birth(age: int, today: date) -> date:
    """只知道周岁时估算出生日期：取「今天往前 age 年再往前半年」，生日落在区间中间，
    以后按它算年龄，平均误差最小。页面上标为估算，提示用户改成准确日期。"""
    try:
        anchor = today.replace(year=today.year - age)
    except ValueError:  # 2 月 29 日
        anchor = today.replace(year=today.year - age, day=28)
    return anchor - timedelta(days=182)


def _birth(raw: Mapping[str, Any], today: date) -> tuple[date | None, int | None, bool]:
    """返回 (出生日期, 周岁, 是否估算)。明确说了出生日期的优先。"""
    exact = _date(raw.get("birth_date"), today)
    if exact is not None:
        return exact, age_on(exact, today), False
    age = _age(raw.get("age"))
    if age is None:
        return None, None, False
    return estimate_birth(age, today), age, True


def _same_age(known: date | None, age: int | None, today: date) -> bool:
    return known is not None and age is not None and age_on(known, today) == age


def _fmt_money(v: Decimal) -> str:
    if v >= 10000 and v % 10000 == 0:
        return f"{v / 10000:,.0f} 万元"
    return f"{v:,.0f} 元"


# ---------- 抽取结果 → 画像增量 ----------


def _match_member(
    family: Sequence[FamilyMember], relation: str, gender: str | None, age: int | None, today: date
) -> FamilyMember | None:
    """找画像里是同一个人的成员：关系相同，性别、年龄（±1 岁）不冲突；有多个都对得上时不猜。"""
    found = []
    for m in family:
        if m.relation != relation:
            continue
        if gender and m.gender and m.gender != gender:
            continue
        if age is not None and m.birth_date and abs(age_on(m.birth_date, today) - age) > 1:
            continue
        found.append(m)
    return found[0] if len(found) == 1 else None


def _member_text(m: FamilyMember, today: date) -> str:
    parts = [m.name or RELATION_LABELS[m.relation]]
    if m.birth_date:
        parts.append(
            f"{age_on(m.birth_date, today)} 周岁" + ("（估算）" if m.birth_date_estimated else "")
        )
    if m.gender:
        parts.append(GENDER_LABELS[m.gender])
    if m.social_security is not None:
        parts.append("有社保" if m.social_security else "无社保")
    return "，".join(parts)


def build_suggestion(
    current: ProfileData,
    raw: Mapping[str, Any],
    catalog: Mapping[int, tuple[str, str]],
    today: date,
) -> ProfileSuggestion | None:
    """raw 是模型的 JSON：{self: {...}, family: [...], coverage: [...]}；
    catalog 是 {产品 id: (产品名, 险种)}。返回需要用户确认的增量，没有新信息时返回 None。"""
    patch: dict[str, Any] = {}
    items: list[SuggestionItem] = []

    me = raw.get("self")
    me = me if isinstance(me, Mapping) else {}
    birth, age, estimated = _birth(me, today)
    if birth is not None and not (
        current.birth_date == birth or (estimated and _same_age(current.birth_date, age, today))
    ):
        patch["birth_date"] = birth.isoformat()
        patch["birth_date_estimated"] = estimated
        text = f"{age} 周岁（按年龄估算出生日期）" if estimated else birth.isoformat()
        items.append(SuggestionItem(label="年龄", value=text))
    gender = me.get("gender")
    if gender in GENDER_LABELS and gender != current.gender:
        patch["gender"] = gender
        items.append(SuggestionItem(label="性别", value=GENDER_LABELS[gender]))
    ss = me.get("social_security")
    if isinstance(ss, bool) and ss != current.social_security:
        patch["social_security"] = ss
        items.append(SuggestionItem(label="社保", value="有" if ss else "无"))
    city = me.get("city")
    if isinstance(city, str) and 0 < len(city.strip()) <= 32 and city.strip() != current.city:
        patch["city"] = city.strip()
        items.append(SuggestionItem(label="城市", value=city.strip()))
    budget = _money(me.get("annual_budget"))
    raw_family = raw.get("family")
    if isinstance(raw_family, list) and raw_family:
        # 同一句里在说家人（「给我老婆配一份，预算三千」）：预算多半是给家人配置的。
        # 提示词里写了不要抽这种预算，实测 qwen-plus 仍会抽，只能在代码里兜底
        budget = None
    if budget is not None and budget != current.annual_budget:
        patch["annual_budget"] = str(budget)
        items.append(SuggestionItem(label="年保费预算", value=_fmt_money(budget)))

    # 家庭成员：对得上的按 id 更新变化的字段，对不上的新建
    family_patch: dict[str, FamilyMember] = {}
    by_relation: dict[str, str] = {}  # 关系 → 本次涉及的成员 id，给已有保障指向被保险人
    for entry in raw_family if isinstance(raw_family, list) else []:
        if not isinstance(entry, Mapping) or entry.get("relation") not in RELATION_LABELS:
            continue
        relation = str(entry["relation"])
        m_birth, m_age, m_est = _birth(entry, today)
        m_gender = entry.get("gender") if entry.get("gender") in GENDER_LABELS else None
        m_ss = entry.get("social_security")
        m_ss = m_ss if isinstance(m_ss, bool) else None
        name = entry.get("name")
        name = name.strip()[:20] if isinstance(name, str) else ""
        existing = _match_member(current.family, relation, m_gender, m_age, today)
        if existing is None:
            new = FamilyMember(
                id=secrets.token_hex(4),
                relation=relation,
                name=name,
                birth_date=m_birth,
                birth_date_estimated=m_est,
                gender=m_gender,
                social_security=m_ss,
            )
            family_patch[new.id] = new
            by_relation.setdefault(relation, new.id)
            items.append(SuggestionItem(label="新增家庭成员", value=_member_text(new, today)))
            continue
        by_relation.setdefault(relation, existing.id)
        changes: dict[str, Any] = {}
        same_birth = existing.birth_date == m_birth or (
            m_est and _same_age(existing.birth_date, m_age, today)
        )
        if m_birth is not None and not same_birth:
            changes.update(birth_date=m_birth, birth_date_estimated=m_est)
        if m_gender and m_gender != existing.gender:
            changes["gender"] = m_gender
        if m_ss is not None and m_ss != existing.social_security:
            changes["social_security"] = m_ss
        if changes:
            updated = existing.model_copy(update=changes)
            family_patch[existing.id] = FamilyMember(
                id=existing.id, relation=existing.relation, **changes
            )
            items.append(SuggestionItem(label="更新家庭成员", value=_member_text(updated, today)))

    # 已有保障
    coverage_patch: list[Coverage] = []
    raw_cov = raw.get("coverage")
    for entry in raw_cov if isinstance(raw_cov, list) else []:
        if not isinstance(entry, Mapping):
            continue
        insured_rel = entry.get("insured", "self")
        if insured_rel == "self":
            insured = "self"
        elif insured_rel in RELATION_LABELS:
            found = by_relation.get(str(insured_rel))
            if found is None:
                member = _match_member(current.family, str(insured_rel), None, None, today)
                if member is None and any(m.relation == insured_rel for m in current.family):
                    continue  # 有多个子女（或父母），不知道是谁的保单：不猜
                if member is None:
                    member = FamilyMember(id=secrets.token_hex(4), relation=insured_rel)
                    family_patch[member.id] = member
                    items.append(
                        SuggestionItem(label="新增家庭成员", value=_member_text(member, today))
                    )
                found = by_relation[str(insured_rel)] = member.id
            insured = found
        else:
            continue
        pid = entry.get("product_id")
        pid = pid if isinstance(pid, int) and not isinstance(pid, bool) else None
        if pid is not None and pid in catalog:
            name, category = catalog[pid]
        else:
            pid = None
            raw_name = entry.get("product_name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            name, category = raw_name.strip()[:50], ""
            raw_cat = entry.get("category")
            category = raw_cat if raw_cat in CATEGORY_LABELS else ""
        sum_assured = _money(entry.get("sum_assured"), upper=Decimal("100000000"))
        start = _date(entry.get("start_date"), today + timedelta(days=366))
        if start is not None and start.year < 1990:
            start = None
        cov = Coverage(
            insured=insured,
            product_id=pid,
            product_name=name,
            category=category or None,
            sum_assured=sum_assured,
            start_date=start,
        )
        same = next(
            (
                c
                for c in current.existing_coverage
                if c.insured == insured
                and (c.product_id == pid if pid is not None else c.product_name == name)
            ),
            None,
        )
        if same is not None and all(
            getattr(cov, f) in (None, getattr(same, f)) for f in ("sum_assured", "start_date")
        ):
            continue  # 已经登记过，没有新信息
        coverage_patch.append(cov)
        who = "本人" if insured == "self" else _who(insured, current, family_patch)
        detail = []
        if sum_assured is not None:
            detail.append(f"保额 {_fmt_money(sum_assured)}")
        if start is not None:
            detail.append(f"{start.isoformat()} 生效")
        if pid is None:
            detail.append("不在本平台产品库中")
        items.append(
            SuggestionItem(
                label="已有保障",
                value=f"{who} · {name}" + (f"（{'，'.join(detail)}）" if detail else ""),
            )
        )

    if not items:
        return None
    if family_patch:
        patch["family"] = [m.model_dump(mode="json") for m in family_patch.values()]
    if coverage_patch:
        patch["existing_coverage"] = [c.model_dump(mode="json") for c in coverage_patch]
    # 用同一个模型校验一遍：前端原样提交时一定能通过
    ProfilePatch.model_validate(patch)
    return ProfileSuggestion(items=items, patch=patch)


def _who(member_id: str, current: ProfileData, extra: Mapping[str, FamilyMember]) -> str:
    m = extra.get(member_id) or next((m for m in current.family if m.id == member_id), None)
    return (m.name or RELATION_LABELS[m.relation]) if m else "家人"


# ---------- 画像 → 顾问槽位 ----------


def subject_of(profile: ProfileData, insured: str) -> FamilyMember | None | str:
    """为谁配置：self 返回 "self"；家人返回唯一对得上的成员，没有或有多个时返回 None。"""
    if insured == "self":
        return "self"
    found = [m for m in profile.family if m.relation == insured]
    return found[0] if len(found) == 1 else None


def profile_slots(profile: ProfileData, insured: str, today: date) -> dict[str, Any]:
    """顾问可从画像预填的槽位。预算只在给本人配置时带入：画像里的预算是本人的年预算，
    给家人配时套用它，可能把全家的预算都花在一个人身上。"""
    who = subject_of(profile, insured)
    out: dict[str, Any] = {}
    if who == "self":
        birth, gender, ss = profile.birth_date, profile.gender, profile.social_security
        if profile.annual_budget is not None:
            out["budget"] = str(profile.annual_budget)
    elif isinstance(who, FamilyMember):
        birth, gender, ss = who.birth_date, who.gender, who.social_security
    else:
        return out
    if birth is not None:
        out["age"] = age_on(birth, today)
    if gender is not None:
        out["gender"] = gender
    if ss is not None:
        out["social_security"] = ss
    return out


def held_coverage(profile: ProfileData, insured: str) -> list[Coverage]:
    """被保险人名下登记的保障（理赔咨询用，spec D18）。家人有多个对得上时都算上。"""
    ids = {m.id for m in profile.family if m.relation == insured}
    if insured == "self":
        ids = {"self"}
    return [c for c in profile.existing_coverage if c.insured in ids]
