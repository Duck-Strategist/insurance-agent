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
YL = "复星联合医联有盟重疾险"
DW = "达尔文12号重大疾病保险"
XS = "复星联合星相守2号长期医疗险（个人版）"


def _yl(sa: str, opts: list[str], ss: bool, gender: str, term: str, age: int) -> dict[str, Any]:
    return {
        "sum_assured": sa,
        "options": opts,
        "social_security": ss,
        "gender": gender,
        "pay_term": term,
        "age": age,
    }


def _dw(coverage: str, opts: list[str], multi: str | None, gender: str, term: str, age: int,
        sa: str = "1000") -> dict[str, Any]:  # fmt: skip
    return {
        "coverage": coverage,
        "options": opts,
        "multi_pay": multi,
        "gender": gender,
        "pay_term": term,
        "age": age,
        "sum_assured": sa,
    }


def _xs(plan: str, ss: bool, deductible: str, age: int, addons: list[str] | None = None
        ) -> dict[str, Any]:  # fmt: skip
    return {
        "plan": plan,
        "social_security": ss,
        "deductible": deductible,
        "age": age,
        "addons": addons or [],
    }


M, F = "male", "female"

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
    # 医联有盟（第 2、90、178 页）：计划 = 1 + 轻中症 + 2×身故 + 4×无医保；保额 10/15/20 万
    (YL, _yl("100000", [], True, M, "10y", 0), "3143.00"),  # 计划1
    (YL, _yl("100000", ["mild_moderate", "death"], True, M, "10y", 1), "4683.00"),  # 计划4
    (YL, _yl("100000", [], False, M, "10y", 2), "3223.00"),  # 计划5
    (YL, _yl("100000", ["mild_moderate", "death"], False, M, "10y", 5), "4969.00"),  # 计划8
    (YL, _yl("100000", ["mild_moderate"], True, M, "10y", 6), "3870.00"),  # 计划2
    (YL, _yl("150000", ["mild_moderate"], False, M, "30y", 40), "4343.50"),  # 计划6
    (YL, _yl("150000", ["death"], True, F, "10y", 0), "5447.00"),  # 计划3
    (YL, _yl("150000", ["death"], False, F, "10y", 4), "5792.50"),  # 计划7
    (YL, _yl("150000", [], True, F, "10y", 6), "4180.50"),  # 计划1
    (YL, _yl("200000", ["death"], False, F, "30y", 40), "5901.00"),  # 计划7
    (YL, _yl("200000", ["mild_moderate", "death"], False, F, "30y", 40), "6818.00"),  # 计划8
    # 达尔文12号（第 2、231、600、877 页）：保额 1000 元时保费 = 每千元费率
    (DW, _dw("life", [], None, M, "single", 0), "173.42"),  # 计划1
    (DW, _dw("life", [], None, M, "single", 50), "425.63"),
    (DW, _dw("life", [], None, M, "20y", 30), "18.24"),
    (DW, _dw("life", [], None, M, "20y", 30, sa="300000"), "5472.00"),  # 18.24 × 300
    (DW, _dw("life", [], "plan1", M, "20y", 0), "11.31"),  # 计划2
    (DW, _dw("life", [], "plan1", M, "20y", 30), "20.22"),
    (DW, _dw("life", ["death"], None, F, "10y", 21), "42.95"),  # 计划49
    (DW, _dw("life", ["death"], None, F, "10y", 55), "80.86"),
    (DW, _dw("life", ["death"], "plan1", F, "10y", 0), "30.84"),  # 计划50
    (DW, _dw("life", ["death"], "plan1", F, "10y", 22), "46.89"),
    # 第 264 页：计划 48 在第 1 页计划表里折行读不出，用它独立验证混合进制映射
    (
        DW,
        _dw(
            "life",
            ["disease_care", "cancer_allowance", "premium_compensation", "pillar_care"],
            "plan2",
            M,
            "20y",
            35,
        ),
        "35.23",
    ),  # fmt: skip
    (DW, _dw("life", ["death"], None, M, "20y", 30), "30.12"),  # 计划49
    (
        DW,
        _dw("to70", ["death", "premium_compensation", "pillar_care"], None, F, "20y", 44),
        "14.87",
    ),
    (DW, _dw("to70", [], None, F, "20y", 45), "11.50"),  # 计划97
    (DW, _dw("to70", ["premium_compensation"], None, F, "20y", 45), "12.69"),  # 计划99
    (DW, _dw("to70", ["death", "pillar_care"], None, F, "20y", 45), "13.60"),  # 计划102
    # 星相守2号个人版（第 1–3、6、14、16 页及 4/8/12 页首行）：必选责任 = 基准费率 × 免赔额因子
    (XS, _xs("计划一", True, "10000", 30), "184.00"),  # 因子 100%
    (XS, _xs("计划一", True, "0", 30), "414.00"),  # 184 × 225%
    (XS, _xs("计划一", False, "0", 0), "1310.95"),  # 785 × 167%
    (XS, _xs("计划二", True, "0", 59), "6209.06"),  # 3718 × 167%
    (XS, _xs("计划二", False, "10000", 7), "1613.00"),
    (XS, _xs("计划一", True, "10000", 70), "2982.00"),
    (XS, _xs("计划二", False, "10000", 70), "8445.00"),
    (XS, _xs("计划一", True, "10000", 30, ["basic_medical"]), str(184 + 139)),
    (XS, _xs("计划二", False, "10000", 46, ["basic_medical"]), str(2532 + 1276)),
    (XS, _xs("计划一", True, "10000", 35, ["ci_allowance"]), str(269 + 77)),
    (XS, _xs("计划二", False, "10000", 49, ["outpatient"]), str(2546 + 5917)),
    (XS, _xs("计划一", True, "0", 0, ["outpatient"]), "1784.25"),  # 341 × 225% + 1017
    (XS, _xs("计划一", False, "10000", 13, ["ci_rehab", "cancer_drug"]), str(330 + 5 + 28)),
    (XS, _xs("计划一", True, "10000", 9, ["ci_special_care"]), str(142 + 41)),
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
    (YL, _yl("200000", [], True, F, "30y", 41)),  # 30 年交到 40 岁
    (DW, _dw("life", ["death"], None, F, "10y", 56)),  # 10 年交到 55 岁
    (DW, _dw("to70", [], None, F, "20y", 46)),  # 保至70岁 20 年交到 45 岁
    (DW, _dw("to70", ["disease_care"], None, F, "20y", 30)),  # 保至70岁不含疾病关爱
    (DW, _dw("to70", [], "plan1", F, "20y", 30)),  # 保至70岁不含多次给付
    (XS, _xs("计划一", True, "10000", 71)),  # 「有等待期」列到 70 岁
]


@pytest.mark.parametrize(("product", "params", "expected"), PREMIUM_POINTS)
def test_premium_points(product: str, params: dict[str, Any], expected: str) -> None:
    result = quote(BOOKS[product], params)
    assert result.status == "ok", result.messages
    assert result.annual_premium == Decimal(expected)
    assert result.total_premium == Decimal(expected) * (result.pay_years or 1)


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
