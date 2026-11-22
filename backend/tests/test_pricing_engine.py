"""报价引擎的逻辑：参数校验、缺参追问、取整、业务校验能否发现提取错位。"""

import dataclasses
import json
from decimal import Decimal

import pytest

from app.pricing import extract
from app.pricing.engine import RULES, LifeRule, quote, rule_for
from app.pricing.tables import (
    RateBook,
    RateItem,
    RateTable,
    book_from_json,
    check_structure,
    content_version,
    dumps_book,
    load_all_books,
)

BOOKS = load_all_books()
XH = BOOKS["新华人寿E增福优享版"]
ZA = BOOKS["众安尊享e生百万医疗险2026版"]
XMF = BOOKS["太平洋小蜜蜂6号综合意外险"]


def _replace_items(book: RateBook, code: str, items: list[RateItem]) -> RateBook:
    tables = dict(book.tables)
    tables[code] = dataclasses.replace(tables[code], items=tuple(items))
    return dataclasses.replace(book, tables=tables)


# ---------- 数据文件 ----------


def test_every_rate_file_passes_checks() -> None:
    assert set(BOOKS) == set(RULES)
    for book in BOOKS.values():
        rule = rule_for(book.product)
        assert rule is not None
        assert check_structure(book) == []
        assert rule.check(book) == []


def test_version_ignores_line_endings() -> None:
    text = dumps_book(XH)
    assert content_version(text) == content_version(text.replace("\n", "\r\n"))
    assert content_version(text) != content_version(text.replace('"964.00"', '"965.00"', 1))


def test_json_roundtrip_is_stable() -> None:
    text = dumps_book(XH)
    assert dumps_book(book_from_json(json.loads(text))) == text


# ---------- 参数 ----------


def test_missing_params_are_reported_not_raised() -> None:
    result = quote(XH, {"age": 30})
    assert result.status == "missing_params"
    assert result.missing == ["gender", "pay_term", "annual_premium"]
    # 给用户看的提示用中文标签，不暴露参数名
    assert result.messages == ["缺少参数：性别、交费期、年交保费（元）"]
    assert result.annual_premium is None


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"age": True, "plan": "经典版"}, "年龄应为整数"),
        ({"age": "三十", "plan": "经典版"}, "年龄应为整数"),
        ({"age": 200, "plan": "经典版"}, "年龄应在"),
        ({"age": 30, "plan": "黄金版"}, "保障计划只能选"),
    ],
)
def test_invalid_params(params: dict[str, object], message: str) -> None:
    result = quote(XMF, params)
    assert result.status == "invalid"
    assert message in result.messages[0]


@pytest.mark.parametrize(
    ("premium", "message"),
    [
        (10000.5, "字符串或整数"),
        ("-1", "大于 0"),
        ("100.001", "两位小数"),
        ("abc", "金额"),
        # 超过 28 位有效数字时 quantize 会抛 InvalidOperation，曾导致接口 500
        ("1e30", "超出范围"),
        ("1" * 40, "超出范围"),
    ],
)
def test_invalid_premium(premium: object, message: str) -> None:
    params = {"age": 30, "gender": "male", "pay_term": "3y", "annual_premium": premium}
    result = quote(XH, params)
    assert result.status == "invalid"
    assert message in result.messages[0]


def test_social_security_must_be_bool() -> None:
    result = quote(ZA, {"age": 30, "social_security": "yes"})
    assert result.status == "invalid"


def test_addons_are_deduplicated_and_ordered_by_table() -> None:
    result = quote(
        ZA, {"age": 30, "social_security": True, "addons": ["drug", "outpatient", "drug"]}
    )
    assert result.status == "ok"
    assert result.params["addons"] == ["outpatient", "drug"]
    assert [line.code for line in result.lines] == ["base", "outpatient", "drug"]


def test_unknown_addon_is_invalid() -> None:
    result = quote(ZA, {"age": 30, "social_security": True, "addons": ["ci_care"]})
    assert result.status == "invalid"


# ---------- 计算 ----------


def test_life_quote_rounds_sum_assured_and_multiplies_pay_years() -> None:
    # 30 岁男 3 年交：每千元年交保费对应 2818 元保额
    params = {"age": 30, "gender": "male", "pay_term": "3y", "annual_premium": "12345.67"}
    result = quote(XH, params)
    assert result.status == "ok"
    assert result.annual_premium == Decimal("12345.67")
    assert result.pay_years == 3
    assert result.total_premium == Decimal("37037.01")
    # 12345.67 × 2818 / 1000 = 34790.098… → 34790.10
    assert result.sum_assured == Decimal("34790.10")
    assert "第 1 页" in result.lines[0].basis


def test_single_premium_pays_once() -> None:
    params = {"age": 30, "gender": "female", "pay_term": "single", "annual_premium": "50000"}
    result = quote(XH, params)
    assert result.pay_years == 1 and result.total_premium == Decimal("50000.00")


def test_ineligible_pay_term_lists_alternatives() -> None:
    params = {"age": 65, "gender": "male", "pay_term": "5y", "annual_premium": "10000"}
    result = quote(XH, params)
    assert result.status == "ineligible"
    assert result.messages == ["65 周岁不能选5年交，可选：趸交"]


def test_medical_addon_age_limit_comes_from_rule_not_table() -> None:
    # 费率表里门急诊有 56–65 岁的价格（续保用），首次投保仍然不能选
    assert ZA.table("outpatient").lookup(60) is not None
    result = quote(ZA, {"age": 60, "social_security": True, "addons": ["outpatient"]})
    assert result.status == "ineligible"
    assert "门急诊" in result.messages[0]


def test_param_specs_expose_choices_from_table() -> None:
    rule = rule_for(XH.product)
    assert isinstance(rule, LifeRule)
    specs = {s.name: s for s in rule.param_specs(XH)}
    assert specs["pay_term"].choices == (("single", "趸交"), ("3y", "3年交"), ("5y", "5年交"))
    assert (specs["age"].min, specs["age"].max) == (0, 70)
    # 引擎按整岁计算，出生天数要求只能在提示里说明
    assert "0 周岁须出生满 30 日" in specs["age"].hint
    za_specs = {s.name: s for s in rule_for(ZA.product).param_specs(ZA)}  # type: ignore[union-attr]
    assert za_specs["age"].max == 70  # 表到 105 岁，但首次投保只到 70 岁


# ---------- 校验能发现提取错位 ----------


def test_check_catches_swapped_pay_term_columns() -> None:
    """把男性 3 年交和 5 年交两列对调（模拟列映射写错），业务校验必须报错。"""
    swap = {"3y": "5y", "5y": "3y"}
    items = [
        dataclasses.replace(i, key={**i.key, "pay_term": swap[i.key["pay_term"]]})
        if i.key["gender"] == "male" and i.key["pay_term"] in swap
        else i
        for i in XH.table("base").items
    ]
    broken = _replace_items(XH, "base", items)
    assert check_structure(broken) == []  # 结构上看不出来
    assert any("未随交费期递增" in p for p in LifeRule().check(broken))


def test_check_catches_shifted_blank_cells() -> None:
    """高龄行按文本切分时，空白格会让后面的数字左移：61 岁女性趸交的值跑到男性 3 年交。"""
    items = [
        i for i in XH.table("base").items if not (i.age_min == 61 and i.key["gender"] == "female")
    ]
    items.append(RateItem(61, 61, {"gender": "male", "pay_term": "3y"}, Decimal("952"), 2))
    broken = _replace_items(XH, "base", items)
    problems = check_structure(broken) + LifeRule().check(broken)
    assert problems


def test_check_structure_finds_gaps_and_overlaps() -> None:
    items = [
        RateItem(0, 4, {"social_security": "yes"}, Decimal("1"), 1),
        RateItem(6, 10, {"social_security": "yes"}, Decimal("1"), 1),
        RateItem(9, 12, {"social_security": "yes"}, Decimal("1"), 1),
    ]
    table = RateTable("base", "t", "premium", ("social_security",), tuple(items))
    book = RateBook("p", "f.pdf", "x", {"base": table})
    problems = check_structure(book)
    assert any("缺失" in p for p in problems)
    assert any("重叠" in p for p in problems)


def test_fixed_plan_check_requires_increasing_plans() -> None:
    items = list(XMF.table("base").items)
    items[0], items[1] = (
        dataclasses.replace(items[0], value=items[1].value),
        dataclasses.replace(items[1], value=items[0].value),
    )
    broken = _replace_items(XMF, "base", items)
    assert rule_for(XMF.product).check(broken)  # type: ignore[union-attr]


# ---------- 提取辅助函数 ----------


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("18-50 周岁（含）", (18, 50)),
        ("3-24周岁", (3, 24)),
        ("[101,105]", (101, 105)),
        ("0（须出生满30日）", (0, 0)),
        ("35", (35, 35)),
    ],
)
def test_age_range(cell: str, expected: tuple[int, int]) -> None:
    assert extract._age_range(cell) == expected


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("806.0", Decimal("806.00")),
        ("180元", Decimal("180.00")),
        ("-", None),
        ("", None),
        (None, None),
    ],
)
def test_value(cell: str | None, expected: Decimal | None) -> None:
    assert extract._value(cell) == expected


def test_value_rejects_garbage() -> None:
    with pytest.raises(extract.ExtractError):
        extract._value("约 800")


def test_expect_header_mismatch_fails() -> None:
    with pytest.raises(extract.ExtractError):
        extract._expect(["性别", "女", None], ["性别", "男"], "x")
    extract._expect(["性别", "男 ", None], ["性别", "男", None], "x")
