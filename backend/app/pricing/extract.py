"""从费率表 PDF 提取结构化数据（离线工具，依赖开发依赖 pdfplumber / pypdfium2）。

费率表 PDF 都有文字层，但不能按空格切文本：高龄行的空白单元格不一定在行尾
（如福有余 59 岁有 9 年交、没有 10 年交，后面还跟着女性的列），按文本切会让数字整体左移。
所以用 pdfplumber 按表格线取单元格，并且先核对表头文字，再按固定的列映射取值。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.pricing.tables import (
    FACTOR_AGES,
    UNIT_FACTOR,
    UNIT_PREMIUM,
    UNIT_PREMIUM_PER_1000_SA,
    UNIT_SUM_ASSURED_PER_1000,
    RateBook,
    RateItem,
    RateTable,
)

Row = list[str | None]
CENT = Decimal("0.01")


class ExtractError(Exception):
    pass


def _tables(pdf_path: Path) -> list[tuple[int, list[Row]]]:
    import pdfplumber  # 开发依赖：只有提取时才需要

    out: list[tuple[int, list[Row]]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables():
                out.append((page_no, [list(r) for r in table]))
    return out


def _clean(cell: str | None) -> str:
    return re.sub(r"\s+", "", cell or "")


def _value(cell: str | None) -> Decimal | None:
    """单元格 → 金额；空白和「-」表示不可投保。"""
    text = _clean(cell).removesuffix("元")
    if text in ("", "-", "—"):
        return None
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        raise ExtractError(f"无法识别的数值：{cell!r}")
    return Decimal(text).quantize(CENT)


def _age_range(cell: str | None) -> tuple[int, int]:
    """「18-50周岁（含）」「[0,4]」「0（须出生满30日）」「35」→ 闭区间。"""
    text = _clean(cell)
    if m := re.fullmatch(r"\[(\d+),(\d+)\]", text):
        return int(m[1]), int(m[2])
    if m := re.match(r"(\d+)-(\d+)周岁", text):
        return int(m[1]), int(m[2])
    if m := re.match(r"(\d+)(（.*）)?$", text):
        return int(m[1]), int(m[1])
    raise ExtractError(f"无法识别的年龄：{cell!r}")


def _expect(row: Sequence[str | None], expected: Sequence[str | None], where: str) -> None:
    """核对表头：None 表示该位置不检查。表头对不上时宁可失败，也不按猜测的列映射取值。"""
    actual = [_clean(c) for c in row]
    for i, exp in enumerate(expected):
        if exp is None:
            continue
        if i >= len(actual) or actual[i] != exp:
            raise ExtractError(f"{where} 表头第 {i} 列应为 {exp!r}，实际 {actual}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class _Builder:
    items: dict[str, list[RateItem]]

    def add(self, code: str, page: int, ages: tuple[int, int], key: dict[str, str], v: Any) -> None:
        if v is None:
            return
        self.items.setdefault(code, []).append(RateItem(ages[0], ages[1], key, v, page))


# ---------- 各格式的提取函数 ----------


def extract_fixed_plan(pdf: Path, *, skip_cols: int = 1) -> dict[str, list[RateItem]]:
    """意外险：表头「承保年龄 [保障期限] 计划1 计划2 …」，每行一个年龄段。"""
    tables = _tables(pdf)
    if len(tables) != 1:
        raise ExtractError(f"{pdf.name}: 应有 1 张表，实际 {len(tables)}")
    page, rows = tables[0]
    header = [_clean(c) for c in rows[0]]
    if header[0] != "承保年龄":
        raise ExtractError(f"{pdf.name}: 表头 {header}")
    plans = header[skip_cols:]
    b = _Builder({})
    for row in rows[1:]:
        ages = _age_range(row[0])
        for plan, cell in zip(plans, row[skip_cols:], strict=True):
            b.add("base", page, ages, {"plan": plan}, _value(cell))
    return b.items


def extract_zhongan(pdf: Path) -> dict[str, list[RateItem]]:
    tables = _tables(pdf)
    if [p for p, _ in tables] != [1, 2]:
        raise ExtractError(f"{pdf.name}: 应在第 1、2 页各有一张表")
    b = _Builder({})
    # 第 1 页：必选计划，有社保 / 无社保（合并单元格之间夹着 None）
    page, rows = tables[0]
    _expect(rows[2], [None, "有社保", None, "无社保"], f"{pdf.name} p1")
    for row in rows[3:]:
        ages = _age_range(row[0])
        b.add("base", page, ages, {"social_security": "yes"}, _value(row[1]))
        b.add("base", page, ages, {"social_security": "no"}, _value(row[3]))
    # 第 2 页：可选加油包。重疾关爱（按每 5 万保额、分性别）暂不支持：费率表没给保额范围
    page, rows = tables[1]
    _expect(
        rows[1],
        [
            None,
            "尊享家庭共享免赔额加油包",
            None,
            "尊享重疾关爱加油包",
            None,
            "尊享重大疾病扩展特需医疗加油包",
            "尊享住院医疗费用补偿加油包",
            "尊享门急诊医疗加油包",
            "尊享住院津贴加油包",
            "尊享药省保加油包",
        ],
        f"{pdf.name} p2",
    )
    _expect(
        rows[3],
        [None, "有社保", "无社保", "男", "女", *(["有/无社保"] * 5)],
        f"{pdf.name} p2",
    )
    yes, no = {"social_security": "yes"}, {"social_security": "no"}
    for row in rows[4:]:
        ages = _age_range(row[0])
        b.add("family_deductible", page, ages, yes, _value(row[1]))
        b.add("family_deductible", page, ages, no, _value(row[2]))
        b.add("ci_special_care", page, ages, {}, _value(row[5]))
        b.add("hospital_compensation", page, ages, {}, _value(row[6]))
        b.add("outpatient", page, ages, {}, _value(row[7]))
        b.add("hospital_allowance", page, ages, {}, _value(row[8]))
        b.add("drug", page, ages, {}, _value(row[9]))
    return b.items


def _extract_life_grid(
    pdf: Path,
    *,
    columns: Sequence[tuple[str, str]],
    header_rows: int,
    check_header: Callable[[list[Row]], None],
) -> dict[str, list[RateItem]]:
    """终身寿保额表：第 1 列是年龄，其后按 columns 给出的 (性别, 交费期) 顺序排列。"""
    b = _Builder({})
    for i, (page, rows) in enumerate(_tables(pdf)):
        if i == 0:
            check_header(rows[:header_rows])
            rows = rows[header_rows:]
        for row in rows:
            ages = _age_range(row[0])
            cells = row[1:]
            if len(cells) != len(columns):
                raise ExtractError(f"{pdf.name} p{page}: 第 {ages[0]} 岁有 {len(cells)} 列")
            for (gender, term), cell in zip(columns, cells, strict=True):
                b.add("base", page, ages, {"gender": gender, "pay_term": term}, _value(cell))
    return b.items


def extract_xinhua(pdf: Path) -> dict[str, list[RateItem]]:
    terms = ["single", "3y", "5y"]
    columns = [(g, t) for g in ("male", "female") for t in terms]

    def check(rows: list[Row]) -> None:
        _expect(rows[0], ["性别", "男", None, None, "女"], pdf.name)
        _expect(rows[1], [None, "趸交", "3年", "5年", "趸交", "3年", "5年"], pdf.name)

    return _extract_life_grid(pdf, columns=columns, header_rows=2, check_header=check)


def extract_zhongying(pdf: Path) -> dict[str, list[RateItem]]:
    terms = ["single", "3y", "5y", "6y", "10y"]
    columns = [(g, t) for t in terms for g in ("male", "female")]

    def check(rows: list[Row]) -> None:
        _expect(
            rows[1],
            ["交费期间", "趸交", "趸交", "3年", "3年", "5年", "5年", "6年", "6年", "10年", "10年"],
            pdf.name,
        )  # noqa: E501
        _expect(rows[2], ["性别年龄", *(["男", "女"] * 5)], pdf.name)

    return _extract_life_grid(pdf, columns=columns, header_rows=3, check_header=check)


def extract_fuyouyu(pdf: Path) -> dict[str, list[RateItem]]:
    terms = ["single", "3y", "5y", "9y", "10y"]
    columns = [(g, t) for g in ("male", "female") for t in terms]

    def check(rows: list[Row]) -> None:
        _expect(rows[1], ["投保年龄", "男性", None, None, None, None, "女性"], pdf.name)
        labels = ["趸交", "3年交", "5年交", "9年交", "10年交"]
        _expect(rows[2], [None, *labels, *labels], pdf.name)

    return _extract_life_grid(pdf, columns=columns, header_rows=3, check_header=check)


# ---------- 长表（一行一条记录）：医联有盟、达尔文12号 ----------
#
# 这两份表有 180 / 887 页，pdfplumber 处理 887 页超过 10 分钟；pypdfium2 自带的整页文本又会把
# 一行拆成几行。这里用 pypdfium2 取每个字符的坐标自己拼行：按字符底边（基线）分行——小数点比数字
# 矮，按中心分行会被拆到单独一行——再按横向间隔切成字段。每行字段数和格式都严格校验。

_NUM = re.compile(r"\d+(\.\d{1,2})?")


def _long_rows(pdf_path: Path) -> list[tuple[int, list[str]]]:
    import pypdfium2 as pdfium  # type: ignore[import-untyped]  # 开发依赖

    out: list[tuple[int, list[str]]] = []
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        for page_no in range(1, len(pdf) + 1):
            textpage = pdf[page_no - 1].get_textpage()
            chars = []
            for i in range(textpage.count_chars()):
                c = textpage.get_text_range(i, 1)
                if c.strip():
                    left, bottom, right, _top = textpage.get_charbox(i)
                    chars.append((bottom, left, right, c))
            chars.sort(key=lambda x: (-x[0], x[1]))
            lines: list[list[tuple[float, float, str]]] = []
            last_bottom: float | None = None
            for bottom, left, right, c in chars:
                if last_bottom is None or abs(bottom - last_bottom) > 2.5:
                    lines.append([])
                    last_bottom = bottom
                lines[-1].append((left, right, c))
            for line in lines:
                line.sort()
                tokens, token, last_right = [], "", None
                for left, right, c in line:
                    if last_right is not None and left - last_right > 2.0:
                        tokens.append(token)
                        token = ""
                    token += c
                    last_right = right
                tokens.append(token)
                out.append((page_no, tokens))
    finally:
        pdf.close()
    return out


def _num(token: str, where: str) -> Decimal:
    if not _NUM.fullmatch(token):
        raise ExtractError(f"{where}: 无法识别的数值 {token!r}")
    return Decimal(token).quantize(CENT)


_GENDERS = {"男": "male", "女": "female"}
_PAY_TERMS_CN = {
    "趸交": "single",
    "5年交": "5y",
    "10年交": "10y",
    "15年交": "15y",
    "20年交": "20y",
    "30年交": "30y",
    "35年交": "35y",
}
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}


def extract_yilian(pdf: Path) -> dict[str, list[RateItem]]:
    """医联有盟：第 1 页计划对照表 + 第 2 页起「保额 计划 性别 交费期 年龄 保费」长表。
    最后两页是附加长期医疗险的费率表（另一个产品），不提取。"""
    from app.pricing.engine import _yilian_plan

    b = _Builder({})
    verified: set[int] = set()
    header = ["基本保险金额", "计划", "性别", "交费期", "投保年龄", "保险费"]
    pages_with_header: set[int] = set()
    rider_from: int | None = None
    for page, tokens in _long_rows(pdf):
        if rider_from is not None and page >= rider_from:
            continue
        if tokens and tokens[0].startswith("复星联合附加"):
            rider_from = page
            continue
        if page == 1:
            # 「计划一」的「一」被排到了上一行，只剩「计划」
            name = "计划一" if tokens[0] == "计划" else tokens[0]
            if len(tokens) == 13 and name.startswith("计划") and name[2:] in _CN_NUM:
                n = _CN_NUM[name[2:]]
                flags = tokens[1:4] + tokens[7:13]
                if not set(flags) <= {"含", "不含"} or tokens[5] not in ("有医保", "无医保"):
                    raise ExtractError(f"{pdf.name} p1: 计划表格式不对 {tokens}")
                params = {
                    "social_security": tokens[5] == "有医保",
                    "options": [
                        o for o, i in (("mild_moderate", 8), ("death", 10)) if tokens[i] == "含"
                    ],
                }
                if tokens[8] != tokens[9] or tokens[8] != tokens[11] or tokens[9] != tokens[12]:
                    raise ExtractError(f"{pdf.name} p1: 轻度与中度责任不同时含 {tokens}")
                if _yilian_plan(params) != str(n):
                    raise ExtractError(f"{pdf.name} p1: {name} 与计划映射不符 {tokens}")
                verified.add(n)
            continue
        if tokens == header:
            pages_with_header.add(page)
            continue
        if not tokens or not tokens[0].isdigit():
            continue
        if len(tokens) != 6:
            # 以数字开头的行只能是数据行：字段数不对时宁可失败，也不能悄悄丢掉一格
            # （列首、列尾少一格时结构校验发现不了）
            raise ExtractError(f"{pdf.name} p{page}: 数据行应有 6 个字段 {tokens}")
        sa, plan, gender, term, age, premium = tokens
        where = f"{pdf.name} p{page}"
        if gender not in _GENDERS or term not in _PAY_TERMS_CN or not age.isdigit():
            raise ExtractError(f"{where}: 无法识别的行 {tokens}")
        key = {
            "sum_assured": sa,
            "plan": plan,
            "gender": _GENDERS[gender],
            "pay_term": _PAY_TERMS_CN[term],
        }
        b.add("base", page, (int(age), int(age)), key, _num(premium, where))
    if verified != set(range(1, 9)):
        raise ExtractError(f"{pdf.name}: 计划对照表只核对到 {sorted(verified)}")
    if 2 not in pages_with_header:
        raise ExtractError(f"{pdf.name}: 第 2 页没有找到表头")
    return b.items


DARWIN_COVERAGE = {"终身": "life", "保至70岁": "to70"}


def extract_darwin(pdf: Path) -> dict[str, list[RateItem]]:
    """达尔文12号：第 1 页终身计划对照表、第 817 页保至70岁计划对照表，其余为长表
    「保险期间 计划 性别 交费期 年龄 保费 EM25 EM50 EM75 EM100」。只取标准体保费，不取加费（EM）列。
    第 878 页起是附加投保人豁免险（按投保人年龄计费），不提取。"""
    from app.pricing.engine import DARWIN_MULTI_PAY, _darwin_plan

    b = _Builder({})
    life_verified: set[int] = set()
    to70_verified: set[int] = set()
    header = [
        "保险期",
        "间",
        "计划",
        "性别",
        "交费期",
        "投保年龄",
        "保险费",
        "EM25",
        "EM50",
        "EM75",
        "EM100",
    ]
    seen_coverages: dict[str, set[int]] = {}
    rider_from: int | None = None
    multi_by_flags = {
        ("不含", "不含"): None,
        ("含", "不含"): "plan1",
        ("不含", "含"): "plan2",
    }
    assert set(multi_by_flags.values()) == set(DARWIN_MULTI_PAY)
    for page, tokens in _long_rows(pdf):
        if rider_from is not None and page >= rider_from:
            continue
        joined = "".join(tokens)
        if "附加投保人豁免" in joined:
            rider_from = page
            continue
        flags_ok = len(tokens) >= 8 and tokens[0].isdigit()
        if flags_ok and set(tokens[1:8]) <= {"含", "不含"}:
            # 终身计划对照表：身故、疾病关爱、恶性肿瘤津贴、保费补偿、顶梁柱、多次给付方案一、方案二
            f = tokens[1:8]
            options = [
                code
                for code, flag in zip(
                    (
                        "death",
                        "disease_care",
                        "cancer_allowance",
                        "premium_compensation",
                        "pillar_care",
                    ),
                    f[:5],
                    strict=True,
                )
                if flag == "含"
            ]
            if (f[5], f[6]) not in multi_by_flags:
                raise ExtractError(f"{pdf.name} p{page}: 多次给付方案一、二同时含 {tokens}")
            params = {
                "coverage": "life",
                "options": options,
                "multi_pay": multi_by_flags[(f[5], f[6])],
            }
            if _darwin_plan(params) != tokens[0]:
                raise ExtractError(f"{pdf.name} p{page}: 计划 {tokens[0]} 与映射不符 {tokens}")
            life_verified.add(int(tokens[0]))
            continue
        if len(tokens) == 4 and tokens[0].isdigit() and set(tokens[1:]) <= {"含", "不含"}:
            # 保至70岁计划对照表：身故、保费补偿、顶梁柱
            options = [
                code
                for code, flag in zip(
                    ("death", "premium_compensation", "pillar_care"), tokens[1:], strict=True
                )
                if flag == "含"
            ]
            if _darwin_plan({"coverage": "to70", "options": options}) != tokens[0]:
                raise ExtractError(f"{pdf.name} p{page}: 计划 {tokens[0]} 与映射不符 {tokens}")
            to70_verified.add(int(tokens[0]))
            continue
        if tokens == header or not tokens or tokens[0] not in DARWIN_COVERAGE:
            continue
        if len(tokens) != 10:
            # 字段被拆开（如字距让「11.60」变成「1」「1.60」）：失败，不能悄悄丢掉一格
            raise ExtractError(f"{pdf.name} p{page}: 数据行应有 10 个字段 {tokens}")
        coverage, plan, gender, term, age, premium = tokens[:6]
        where = f"{pdf.name} p{page}"
        if gender not in _GENDERS or term not in _PAY_TERMS_CN or not age.isdigit():
            raise ExtractError(f"{where}: 无法识别的行 {tokens}")
        for em in tokens[6:]:
            _num(em, where)
        seen_coverages.setdefault(DARWIN_COVERAGE[coverage], set()).add(int(plan))
        key = {"plan": plan, "gender": _GENDERS[gender], "pay_term": _PAY_TERMS_CN[term]}
        b.add("base", page, (int(age), int(age)), key, _num(premium, where))
    # 计划对照表第 1 页有几行的说明文字折行，字段拼不完整，这些行靠混合进制规律推出；
    # 能完整读出的行必须全部和映射一致，且至少要核对到一半以上
    if len(life_verified) < 48:
        raise ExtractError(f"{pdf.name}: 终身计划对照表只核对到 {len(life_verified)} 行")
    if to70_verified != set(range(97, 105)):
        raise ExtractError(f"{pdf.name}: 保至70岁计划对照表只核对到 {sorted(to70_verified)}")
    if seen_coverages != {"life": set(range(1, 97)), "to70": set(range(97, 105))}:
        got = {k: (min(v), max(v), len(v)) for k, v in seen_coverages.items()}
        raise ExtractError(f"{pdf.name}: 保险期间与计划编号不对应 {got}")
    print(f"  达尔文12号：终身计划对照表核对 {len(life_verified)}/96 行，保至70岁 8/8 行")
    return b.items


# ---------- 星相守2号：基准费率 × 免赔额因子 + 可选责任 ----------

_XIANGSHOU_SECTIONS = (
    ("必选责任", "base"),
    ("重大疾病康复医疗", "ci_rehab"),
    ("住院津贴", "ci_allowance"),
    ("特定药品", "cancer_drug"),
    ("补充关爱", None),  # 按每千元保额、分性别，暂不支持
    ("拓展特需", "ci_special_care"),
    ("基础医疗", "basic_medical"),
    ("门急诊", "outpatient"),
)
_PLAN_SS = [("计划一", "yes"), ("计划一", "no"), ("计划二", "yes"), ("计划二", "no")]


def extract_xiangshou(pdf: Path) -> dict[str, list[RateItem]]:
    """只取「有等待期」的列：条款 2.5 写明无等待期只适用于意外和续保，首次投保都有等待期。"""
    b = _Builder({})
    section: str | None = None
    layout: list[tuple[str, str]] | None = None
    seen: list[str] = []
    for page, rows in _tables(pdf):
        first = _clean(rows[0][0])
        if page == 1:
            if first == "免赔额":
                _expect(rows[1], [None, "计划一", None, "计划二"], f"{pdf.name} p1")
                _expect(rows[2], [None, "有医保", "无医保", "有医保", "无医保"], f"{pdf.name} p1")
                for row in rows[3:]:
                    deductible = _clean(row[0])
                    if not deductible.isdigit():
                        raise ExtractError(f"{pdf.name} p1: 无法识别的免赔额 {row[0]!r}")
                    for (plan, ss), cell in zip(_PLAN_SS, row[1:5], strict=True):
                        pct = _clean(cell).removesuffix("%")
                        if not pct.isdigit():
                            raise ExtractError(f"{pdf.name} p1: 无法识别的因子 {cell!r}")
                        key = {"deductible": deductible, "plan": plan, "social_security": ss}
                        b.add("deductible", 1, FACTOR_AGES, key, Decimal(pct) / 100)
            continue
        if "费率表" in first:
            matches = [code for kw, code in _XIANGSHOU_SECTIONS if kw in first]
            if len(matches) != 1:
                raise ExtractError(f"{pdf.name} p{page}: 无法识别的责任 {first}")
            section = matches[0]
            seen.append(first)
            if section is None:
                continue  # 不提取的责任：跳过表头检查，续页由下面的 section is None 跳过
            header = [[_clean(c) for c in r] for r in rows[:5]]
            if any("计划一" in r for r in header):
                layout = [(p, ss) for p, ss in _PLAN_SS]
                cols = 4
            elif any("有医保" in r for r in header):
                layout = [("", "yes"), ("", "no")]
                cols = 2
            else:
                layout = [("", "")]
                cols = 1
            wait_row = next(r for r in header if "有等待期" in r)
            if wait_row.index("有等待期") != 1:
                raise ExtractError(f"{pdf.name} p{page}: 「有等待期」不在第 2 列 {wait_row}")
            if "无等待期" in wait_row and wait_row.index("无等待期") != 1 + cols:
                raise ExtractError(f"{pdf.name} p{page}: 「无等待期」列位置不对 {wait_row}")
            where = f"{pdf.name} p{page}"
            if cols == 4:
                plan_row = next(r for r in header if "计划一" in r)
                _expect(plan_row, [None, "计划一", None, "计划二"], where)
            if cols >= 2:
                ss_row = next(r for r in header if "有医保" in r)
                _expect(ss_row, [None, *(["有医保", "无医保"] * (cols // 2))], where)
        if section is None:
            continue
        if layout is None:
            raise ExtractError(f"{pdf.name} p{page}: 没有表头")
        for row in rows:
            if not _clean(row[0]).isdigit():
                continue
            ages = _age_range(row[0])
            for (plan, ss), cell in zip(layout, row[1 : 1 + len(layout)], strict=True):
                key = {}
                if plan:
                    key["plan"] = plan
                if ss:
                    key["social_security"] = ss
                b.add(section, page, ages, key, _value(cell))
    if len(seen) != len(_XIANGSHOU_SECTIONS):
        raise ExtractError(
            f"{pdf.name}: 识别到 {len(seen)} 个责任，应为 {len(_XIANGSHOU_SECTIONS)}"
        )
    return b.items


# ---------- 产品清单 ----------


@dataclass(frozen=True)
class Source:
    product: str
    slug: str  # 输出文件名
    pdf: str  # data/raw/rate_tables/ 下的文件名
    extract: Callable[[Path], dict[str, list[RateItem]]]
    tables: Mapping[str, tuple[str, str, tuple[str, ...]]]  # code → (标题, 单位, 维度)


def _fixed(skip_cols: int = 1) -> Callable[[Path], dict[str, list[RateItem]]]:
    return lambda p: extract_fixed_plan(p, skip_cols=skip_cols)


_LIFE = {"base": ("基本保险金额表", UNIT_SUM_ASSURED_PER_1000, ("gender", "pay_term"))}
_SS = ("social_security",)

SOURCES: tuple[Source, ...] = (
    Source(
        "太平洋小蜜蜂6号综合意外险",
        "xiaomifeng6",
        "太平洋小蜜蜂6号综合意外险费率表.pdf",
        _fixed(),
        {"base": ("综合意外险", UNIT_PREMIUM, ("plan",))},
    ),
    Source(
        "太平洋小蜜蜂6号玫瑰版综合意外险",
        "xiaomifeng6-rose",
        "太平洋小蜜蜂6号玫瑰版综合意外险费率表.pdf",
        _fixed(),
        {"base": ("综合意外险（玫瑰版）", UNIT_PREMIUM, ("plan",))},
    ),
    Source(
        "人保小学童2号Pro学平险",
        "xiaoxuetong2-pro",
        "人保小学童2号Pro学平险费率表.pdf",
        _fixed(skip_cols=2),
        {"base": ("学平险（保障期限 1 年）", UNIT_PREMIUM, ("plan",))},
    ),
    Source(
        "众安尊享e生百万医疗险2026版",
        "zunxiang-e-sheng-2026",
        "众安尊享e生百万医疗险2026版费率表.pdf",
        extract_zhongan,
        {
            "base": ("必选计划", UNIT_PREMIUM, _SS),
            "family_deductible": ("家庭共享免赔额加油包", UNIT_PREMIUM, _SS),
            "ci_special_care": ("重大疾病扩展特需医疗加油包", UNIT_PREMIUM, ()),
            "hospital_compensation": ("住院医疗费用补偿加油包", UNIT_PREMIUM, ()),
            "outpatient": ("门急诊医疗加油包", UNIT_PREMIUM, ()),
            "hospital_allowance": ("住院津贴加油包", UNIT_PREMIUM, ()),
            "drug": ("药省保加油包", UNIT_PREMIUM, ()),
        },
    ),
    Source(
        "中英人寿福满佳C款（悦享版）",
        "fumanjia-c",
        "中英人寿福满佳C款(悦享版)终身寿险分红型费率表.pdf",
        extract_zhongying,
        _LIFE,
    ),
    Source(
        "太保福有余2025",
        "fuyouyu-2025",
        "太平洋福有余2025终身寿险分红型费率表.pdf",
        extract_fuyouyu,
        _LIFE,
    ),
    Source(
        "新华人寿E增福优享版",
        "e-zengfu",
        "新华人寿E增福优享版终身寿险互联网费率表.pdf",
        extract_xinhua,
        _LIFE,
    ),
    Source(
        "复星联合医联有盟重疾险",
        "yilian-youmeng",
        "复星联合医联有盟重疾险费率表.pdf",
        extract_yilian,
        {"base": ("重大疾病保险", UNIT_PREMIUM, ("sum_assured", "plan", "gender", "pay_term"))},
    ),
    Source(
        "达尔文12号重大疾病保险",
        "darwin-12",
        "复星联合优选K重疾险(达尔文12号)费率表.pdf",
        extract_darwin,
        {
            "base": (
                "重大疾病保险",
                UNIT_PREMIUM_PER_1000_SA,
                ("plan", "gender", "pay_term"),
            )
        },
    ),
    Source(
        "复星联合星相守2号长期医疗险（个人版）",
        "xiangshou-2-personal",
        "复星联合优选二号长期住院医疗险(星相守2号)费率表.pdf",
        extract_xiangshou,
        {
            "base": ("必选责任", UNIT_PREMIUM, ("plan", "social_security")),
            "deductible": (
                "免赔额调整因子",
                UNIT_FACTOR,
                ("deductible", "plan", "social_security"),
            ),
            "basic_medical": ("基础医疗保险金", UNIT_PREMIUM, ("plan", "social_security")),
            "ci_rehab": ("重大疾病康复医疗保险金", UNIT_PREMIUM, _SS),
            "ci_allowance": ("重大疾病住院或重症监护病房住院津贴保险金", UNIT_PREMIUM, ()),
            "cancer_drug": ("恶性肿瘤-重度特定药品费用医疗保险金", UNIT_PREMIUM, _SS),
            "ci_special_care": ("重大疾病住院拓展特需医疗保险金", UNIT_PREMIUM, _SS),
            "outpatient": ("附加长期门急诊医疗保险", UNIT_PREMIUM, ("plan", "social_security")),
        },
    ),
)


def extract_book(source: Source, rate_dir: Path) -> RateBook:
    pdf = rate_dir / source.pdf
    raw = source.extract(pdf)
    if set(raw) != set(source.tables):
        raise ExtractError(f"{source.product}: 提取到 {sorted(raw)}，应为 {sorted(source.tables)}")
    tables = {}
    for code, (title, unit, dims) in source.tables.items():
        # 保持 PDF 的行列顺序：计划、交费期的展示顺序也来自这里
        items = tuple(raw[code])
        tables[code] = RateTable(code=code, title=title, unit=unit, dims=dims, items=items)
    return RateBook(
        product=source.product,
        source_file=source.pdf,
        source_sha256=_sha256(pdf),
        tables=tables,
    )
