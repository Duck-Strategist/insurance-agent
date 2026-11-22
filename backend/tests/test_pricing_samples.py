"""报价抽查点：期望值是人工对照费率表 PDF 页面图像逐个读出来的，不来自提取脚本（spec §8 报价指标）。

覆盖每个产品的表头/表尾、每张加油包表、高龄行的空白单元格（应不可投保）。
重新提取费率表后这里必须全部通过；新增产品时按同样方式补点。
"""

from decimal import Decimal
from typing import Any

import pytest

from app.pricing.engine import quote
from app.pricing.tables import load_all_books

BOOKS = load_all_books()

XMF = "太平洋小蜜蜂6号综合意外险"
ROSE = "太平洋小蜜蜂6号玫瑰版综合意外险"
XXT = "人保小学童2号Pro学平险"
ZA = "众安尊享e生百万医疗险2026版"
ZY = "中英人寿福满佳C款（悦享版）"
FYY = "太保福有余2025"
XH = "新华人寿E增福优享版"

# (产品, 参数, 期望年交保费)
PREMIUM_POINTS: list[tuple[str, dict[str, Any], str]] = [
    (XMF, {"age": 18, "plan": "经典版"}, "136"),
    (XMF, {"age": 30, "plan": "典藏版"}, "166"),
    (XMF, {"age": 45, "plan": "优享版"}, "236"),
    (XMF, {"age": 50, "plan": "尊享版"}, "298"),
    (ROSE, {"age": 18, "plan": "典藏版"}, "169"),
    (ROSE, {"age": 35, "plan": "尊享版"}, "319"),
    (ROSE, {"age": 50, "plan": "至尊版"}, "439"),
    (XXT, {"age": 3, "plan": "基础版"}, "180"),
    (XXT, {"age": 12, "plan": "经典版"}, "240"),
    (XXT, {"age": 24, "plan": "尊贵版"}, "335"),
    # 众安：必选计划
    (ZA, {"age": 0, "social_security": True}, "806"),
    (ZA, {"age": 4, "social_security": False}, "1679"),
    (ZA, {"age": 28, "social_security": True}, "318"),
    (ZA, {"age": 30, "social_security": False}, "679"),
    (ZA, {"age": 70, "social_security": True}, "3316"),
    (ZA, {"age": 66, "social_security": False}, "11926"),
    # 众安：每个加油包至少一个点（总价 = 必选 + 加油包）
    (ZA, {"age": 33, "social_security": True, "addons": ["family_deductible"]}, str(423 + 22)),
    (ZA, {"age": 33, "social_security": False, "addons": ["family_deductible"]}, str(971 + 52)),
    (ZA, {"age": 43, "social_security": True, "addons": ["ci_special_care"]}, str(636 + 260)),
    (ZA, {"age": 2, "social_security": True, "addons": ["hospital_compensation"]}, str(806 + 698)),
    (ZA, {"age": 27, "social_security": True, "addons": ["outpatient"]}, str(318 + 598)),
    (ZA, {"age": 53, "social_security": False, "addons": ["outpatient"]}, str(4042 + 1019)),
    (ZA, {"age": 38, "social_security": True, "addons": ["hospital_allowance"]}, str(517 + 200)),
    (ZA, {"age": 70, "social_security": True, "addons": ["drug"]}, str(3316 + 99)),
    (
        ZA,
        {
            "age": 30,
            "social_security": True,
            "addons": ["drug", "outpatient", "hospital_allowance"],
        },
        str(318 + 598 + 141 + 99),
    ),
]

# (产品, 年龄, 性别, 交费期, 每千元年交保费对应的基本保额)
SUM_ASSURED_POINTS: list[tuple[str, int, str, str, str]] = [
    (XH, 0, "male", "single", "964"),
    (XH, 0, "male", "3y", "2840"),
    (XH, 0, "female", "5y", "4658"),
    (XH, 33, "male", "5y", "4613"),
    (XH, 33, "female", "single", "960"),
    (XH, 60, "male", "5y", "4527"),
    (XH, 60, "female", "3y", "2806"),
    (XH, 70, "male", "single", "824"),
    (XH, 70, "female", "single", "913"),
    (ZY, 42, "male", "single", "887"),
    (ZY, 52, "male", "10y", "7562"),
    (ZY, 52, "female", "10y", "8090"),
    (ZY, 52, "female", "6y", "4980"),
    (ZY, 57, "male", "3y", "2236"),
    (ZY, 57, "female", "5y", "4235"),
    (ZY, 60, "male", "single", "792"),
    (ZY, 60, "female", "single", "872"),
    (FYY, 40, "male", "single", "919"),
    (FYY, 40, "female", "10y", "8439"),
    (FYY, 58, "female", "10y", "8297"),
    (FYY, 59, "male", "9y", "7323"),
    (FYY, 63, "male", "5y", "3998"),
    (FYY, 65, "female", "3y", "2571"),
    (FYY, 68, "male", "single", "817"),
    (FYY, 68, "female", "single", "886"),
]

# 费率表里是空白或「-」的格子：应不可投保，而不是报出别的列的数字
INELIGIBLE_POINTS: list[tuple[str, dict[str, Any]]] = [
    (XH, {"age": 61, "gender": "male", "pay_term": "3y", "annual_premium": "10000"}),
    (XH, {"age": 71, "gender": "female", "pay_term": "single", "annual_premium": "10000"}),
    (ZY, {"age": 53, "gender": "male", "pay_term": "6y", "annual_premium": "10000"}),
    (ZY, {"age": 58, "gender": "female", "pay_term": "3y", "annual_premium": "10000"}),
    (FYY, {"age": 59, "gender": "male", "pay_term": "10y", "annual_premium": "10000"}),
    (FYY, {"age": 66, "gender": "female", "pay_term": "3y", "annual_premium": "10000"}),
    (ZA, {"age": 56, "social_security": True, "addons": ["outpatient"]}),  # 门急诊首投到 55 岁
    (ZA, {"age": 71, "social_security": True}),  # 71 岁以上仅限续保
    (XMF, {"age": 17, "plan": "经典版"}),
    (XXT, {"age": 25, "plan": "基础版"}),
]


@pytest.mark.parametrize(("product", "params", "expected"), PREMIUM_POINTS)
def test_premium_points(product: str, params: dict[str, Any], expected: str) -> None:
    result = quote(BOOKS[product], params)
    assert result.status == "ok", result.messages
    assert result.annual_premium == Decimal(expected)
    assert result.total_premium == Decimal(expected)  # 一年期，交一次


@pytest.mark.parametrize(("product", "age", "gender", "pay_term", "per_1000"), SUM_ASSURED_POINTS)
def test_sum_assured_points(
    product: str, age: int, gender: str, pay_term: str, per_1000: str
) -> None:
    result = quote(
        BOOKS[product],
        {"age": age, "gender": gender, "pay_term": pay_term, "annual_premium": "1000"},
    )
    assert result.status == "ok", result.messages
    assert result.sum_assured == Decimal(per_1000)


@pytest.mark.parametrize(("product", "params"), INELIGIBLE_POINTS)
def test_blank_cells_are_ineligible(product: str, params: dict[str, Any]) -> None:
    result = quote(BOOKS[product], params)
    assert result.status == "ineligible"
    assert result.annual_premium is None and not result.lines


def test_every_rate_file_is_sampled() -> None:
    sampled = {p for p, *_ in PREMIUM_POINTS} | {p for p, *_ in SUM_ASSURED_POINTS}
    assert sampled == set(BOOKS)
