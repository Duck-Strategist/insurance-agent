"""费率表的内存表示与 JSON 格式。

每个产品一个 JSON 文件（`app/pricing/rates/<slug>.json`），由 `python -m app.pricing extract`
从 PDF 提取、校验、人工抽查后提交进仓库，再由 `python -m app.pricing load` 写入数据库（spec D11）。
这里只放纯数据结构和查表逻辑，不依赖数据库和 LLM。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

RATES_DIR = Path(__file__).with_name("rates")

# 费率表单元格的含义
UNIT_PREMIUM = "premium"  # 年交保费（元）
UNIT_SUM_ASSURED_PER_1000 = "sum_assured_per_1000_premium"  # 每 1000 元年交保费对应的基本保额
UNITS = (UNIT_PREMIUM, UNIT_SUM_ASSURED_PER_1000)


@dataclass(frozen=True)
class RateItem:
    """一个单元格：年龄区间（闭区间）+ 其他维度取值 → 数值。"""

    age_min: int
    age_max: int
    key: Mapping[str, str]
    value: Decimal
    page: int  # PDF 页码（从 1 开始），报价明细里用于追溯

    def matches(self, age: int, key: Mapping[str, str]) -> bool:
        return self.age_min <= age <= self.age_max and all(
            self.key.get(k) == v for k, v in key.items()
        )


@dataclass(frozen=True)
class RateTable:
    code: str
    title: str
    unit: str
    dims: tuple[str, ...]  # 除年龄外的维度，如 ("gender", "pay_term")
    items: tuple[RateItem, ...]

    def lookup(self, age: int, **key: str) -> RateItem | None:
        """按年龄和全部维度精确查表；查不到返回 None（该组合不可投保）。"""
        if set(key) != set(self.dims):
            raise ValueError(f"{self.code}: 维度应为 {self.dims}，实际 {tuple(key)}")
        for item in self.items:
            if item.matches(age, key):
                return item
        return None

    def choices(self, dim: str) -> list[str]:
        """某个维度出现过的取值，保持费率表中的顺序。"""
        seen: dict[str, None] = {}
        for item in self.items:
            seen.setdefault(item.key[dim], None)
        return list(seen)

    def available(self, dim: str, age: int, **key: str) -> list[str]:
        """在给定年龄和其他维度下，某个维度还能选哪些值（用于提示「可选：趸交、3年交」）。"""
        return [
            v
            for v in self.choices(dim)
            if any(item.matches(age, {**key, dim: v}) for item in self.items)
        ]

    def age_range(self) -> tuple[int, int]:
        return min(i.age_min for i in self.items), max(i.age_max for i in self.items)


@dataclass(frozen=True)
class RateBook:
    """一个产品的全部费率表。version 是 JSON 内容的哈希，报价记录它以便复现。"""

    product: str
    source_file: str
    source_sha256: str
    tables: Mapping[str, RateTable]
    version: str = field(default="")

    def table(self, code: str) -> RateTable:
        return self.tables[code]


# ---------- JSON 读写 ----------


def book_to_json(book: RateBook) -> dict[str, Any]:
    return {
        "product": book.product,
        "source": {"file": book.source_file, "sha256": book.source_sha256},
        "tables": [
            {
                "code": t.code,
                "title": t.title,
                "unit": t.unit,
                "dims": list(t.dims),
                "items": [
                    {
                        "age": [i.age_min, i.age_max],
                        **({"key": dict(i.key)} if i.key else {}),
                        "value": str(i.value),
                        "page": i.page,
                    }
                    for i in t.items
                ],
            }
            for t in book.tables.values()
        ],
    }


def dumps_book(book: RateBook) -> str:
    """稳定的文本格式：每个单元格一行，方便在 diff 里人工审查。"""
    data = book_to_json(book)
    head = {k: v for k, v in data.items() if k != "tables"}
    lines = ["{"]
    for k, v in head.items():
        lines.append(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},")
    lines.append('  "tables": [')
    for ti, t in enumerate(data["tables"]):
        meta = {k: v for k, v in t.items() if k != "items"}
        lines.append("    {")
        for k, v in meta.items():
            lines.append(f"      {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},")
        lines.append('      "items": [')
        items = t["items"]
        for ii, item in enumerate(items):
            sep = "," if ii < len(items) - 1 else ""
            lines.append(f"        {json.dumps(item, ensure_ascii=False)}{sep}")
        lines.append("      ]")
        lines.append("    }" + ("," if ti < len(data["tables"]) - 1 else ""))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def content_version(text: str) -> str:
    """按解析后的内容算版本，不按原始字节：同一份数据在 Windows（CRLF）和 git 检出（LF）
    下得到同一个版本，否则每换一台机器加载都会多出一个内容相同的「新版本」。"""
    canonical = json.dumps(
        json.loads(text), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def book_from_json(data: Mapping[str, Any], *, version: str = "") -> RateBook:
    tables: dict[str, RateTable] = {}
    for t in data["tables"]:
        dims = tuple(t["dims"])
        items = tuple(
            RateItem(
                age_min=int(i["age"][0]),
                age_max=int(i["age"][1]),
                key=dict(i.get("key", {})),
                value=Decimal(i["value"]),
                page=int(i["page"]),
            )
            for i in t["items"]
        )
        tables[t["code"]] = RateTable(
            code=t["code"], title=t["title"], unit=t["unit"], dims=dims, items=items
        )
    return RateBook(
        product=data["product"],
        source_file=data["source"]["file"],
        source_sha256=data["source"]["sha256"],
        tables=tables,
        version=version,
    )


def load_book(path: Path) -> RateBook:
    text = path.read_text(encoding="utf-8")
    return book_from_json(json.loads(text), version=content_version(text))


def load_all_books(rates_dir: Path = RATES_DIR) -> dict[str, RateBook]:
    """读取目录下全部费率 JSON，按产品名索引。"""
    books: dict[str, RateBook] = {}
    for path in sorted(rates_dir.glob("*.json")):
        book = load_book(path)
        if book.product in books:
            raise ValueError(f"产品 {book.product} 有两份费率文件")
        books[book.product] = book
    return books


# ---------- 结构校验（与具体产品无关） ----------


def check_structure(book: RateBook) -> list[str]:
    """返回问题列表，空列表表示通过。业务规则相关的校验在 engine.ProductRule.check 里。"""
    problems: list[str] = []
    if not book.tables:
        problems.append("没有费率表")
    for t in book.tables.values():
        where = f"{book.product}/{t.code}"
        if t.unit not in UNITS:
            problems.append(f"{where}: 未知单位 {t.unit}")
        if not t.items:
            problems.append(f"{where}: 空表")
            continue
        cells: dict[tuple[tuple[str, str], ...], list[RateItem]] = {}
        for item in t.items:
            if set(item.key) != set(t.dims):
                problems.append(f"{where}: 单元格维度 {sorted(item.key)} 与表头 {t.dims} 不一致")
            if item.age_min > item.age_max or item.age_min < 0:
                problems.append(f"{where}: 年龄区间无效 [{item.age_min},{item.age_max}]")
            if item.value <= 0:
                problems.append(f"{where}: 非正数 {item.value}（年龄 {item.age_min}）")
            cells.setdefault(tuple(sorted(item.key.items())), []).append(item)
        # 同一列内年龄区间不能重叠，也不能有缺口（缺口意味着漏提取了行）
        for key, items in cells.items():
            items = sorted(items, key=lambda i: i.age_min)
            for a, b in zip(items, items[1:], strict=False):
                if b.age_min <= a.age_max:
                    problems.append(f"{where} {dict(key)}: 年龄区间重叠 {a.age_max}/{b.age_min}")
                elif b.age_min != a.age_max + 1:
                    problems.append(
                        f"{where} {dict(key)}: 年龄 {a.age_max + 1}–{b.age_min - 1} 缺失"
                    )
    return problems
