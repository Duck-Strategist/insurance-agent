"""从费率表 PDF 提取结构化数据（离线工具，依赖开发依赖 pdfplumber）。

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
    UNIT_PREMIUM,
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


def _expect(row: Row, expected: Sequence[str | None], where: str) -> None:
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
