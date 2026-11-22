"""报价引擎：纯函数，输入费率表和参数，输出可追溯的报价明细（spec §6.4）。

- 数字只来自费率表和这里的计算，LLM 不参与（D11）。
- 参数缺失时返回 `missing_params`，由调用方（Agent / 前端）去追问，不抛异常。
- 每种费率表格式对应一种规则（ProductRule），产品名 → 规则的映射在 RULES 里。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from app.pricing.tables import (
    UNIT_PREMIUM,
    UNIT_SUM_ASSURED_PER_1000,
    RateBook,
    RateItem,
    RateTable,
)

CENT = Decimal("0.01")

GENDER_LABELS = {"male": "男", "female": "女"}
SOCIAL_SECURITY_LABELS = {"yes": "有社保", "no": "无社保"}
# 交费期：编码 → (展示名, 交费年数)
PAY_TERMS: dict[str, tuple[str, int]] = {
    "single": ("趸交", 1),
    "3y": ("3年交", 3),
    "5y": ("5年交", 5),
    "6y": ("6年交", 6),
    "9y": ("9年交", 9),
    "10y": ("10年交", 10),
    "20y": ("20年交", 20),
}
MAX_AGE = 120
# 年交保费上限只用来挡住明显的输入错误（如多输了几个 0），不是产品规则
MAX_ANNUAL_PREMIUM = Decimal("100000000")

Status = Literal["ok", "missing_params", "invalid", "ineligible"]
ParamKind = Literal["age", "choice", "bool", "money", "multi"]


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    kind: ParamKind
    required: bool = True
    choices: tuple[tuple[str, str], ...] = ()  # (取值, 展示名)
    min: int | None = None
    max: int | None = None
    hint: str = ""


@dataclass(frozen=True)
class QuoteLine:
    code: str
    title: str
    premium: Decimal  # 年交保费
    basis: str  # 计算依据，如「费率表第 1 页：26–30 周岁 有社保 318.00」
    page: int


@dataclass
class QuoteResult:
    product: str
    status: Status
    rate_version: str
    params: dict[str, Any] = field(default_factory=dict)  # 规范化后的参数
    missing: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    lines: list[QuoteLine] = field(default_factory=list)
    annual_premium: Decimal | None = None
    pay_years: int | None = None
    total_premium: Decimal | None = None  # 交费期内总保费 = 年交保费 × 交费年数
    sum_assured: Decimal | None = None  # 终身寿险：由保费算出的基本保额


class _Invalid(Exception):
    pass


# ---------- 参数解析 ----------


def _parse_age(value: Any) -> int:
    # bool 是 int 的子类，单独排除；"30" 这类字符串允许（前端表单、LLM 都可能给字符串）
    if isinstance(value, bool):
        raise _Invalid("年龄应为整数")
    try:
        age = int(str(value).strip())
    except ValueError:
        raise _Invalid("年龄应为整数") from None
    if not 0 <= age <= MAX_AGE:
        raise _Invalid(f"年龄应在 0–{MAX_AGE} 周岁之间")
    return age


def _parse_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    raise _Invalid(f"{label}应为是/否")


def _parse_choice(value: Any, spec: ParamSpec) -> str:
    allowed = {v for v, _ in spec.choices}
    if not isinstance(value, str) or value not in allowed:
        labels = "、".join(label for _, label in spec.choices)
        raise _Invalid(f"{spec.label}只能选：{labels}")
    return value


def _parse_money(value: Any, label: str) -> Decimal:
    if isinstance(value, bool | float):
        # float 会带来二进制误差，要求调用方传字符串或整数
        raise _Invalid(f"{label}请用字符串或整数表示")
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        raise _Invalid(f"{label}应为金额") from None
    if not amount.is_finite() or amount <= 0:
        raise _Invalid(f"{label}应大于 0")
    # 先挡住过大的值：quantize 在结果超过 28 位有效数字时会抛 InvalidOperation
    if amount > MAX_ANNUAL_PREMIUM:
        raise _Invalid(f"{label}超出范围")
    if amount != amount.quantize(CENT):
        raise _Invalid(f"{label}最多两位小数")
    return amount.quantize(CENT)


def _parse_multi(value: Any, spec: ParamSpec) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
        raise _Invalid(f"{spec.label}应为列表")
    allowed = [v for v, _ in spec.choices]
    unknown = [v for v in value if v not in allowed]
    if unknown:
        raise _Invalid(f"{spec.label}没有：{'、'.join(unknown)}")
    # 去重并按费率表顺序排列，保证同样的选择得到同样的报价记录
    return [v for v in allowed if v in value]


def parse_params(
    specs: list[ParamSpec], raw: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """校验并规范化参数，返回 (参数, 缺失的必填参数名)。格式错误抛 _Invalid。"""
    params: dict[str, Any] = {}
    missing: list[str] = []
    for spec in specs:
        value = raw.get(spec.name)
        if value is None or value == "":
            if spec.required:
                missing.append(spec.name)
            elif spec.kind == "multi":
                params[spec.name] = []
            continue
        if spec.kind == "age":
            params[spec.name] = _parse_age(value)
        elif spec.kind == "bool":
            params[spec.name] = _parse_bool(value, spec.label)
        elif spec.kind == "choice":
            params[spec.name] = _parse_choice(value, spec)
        elif spec.kind == "money":
            params[spec.name] = _parse_money(value, spec.label)
        else:
            params[spec.name] = _parse_multi(value, spec)
    return params, missing


def _fmt(value: Decimal) -> str:
    return f"{value:,.2f}"


def _age_text(item: RateItem) -> str:
    if item.age_min == item.age_max:
        return f"{item.age_min} 周岁"
    return f"{item.age_min}–{item.age_max} 周岁"


# ---------- 规则 ----------


class ProductRule(ABC):
    """一种费率表格式的报价规则。"""

    @abstractmethod
    def param_specs(self, book: RateBook) -> list[ParamSpec]: ...

    @abstractmethod
    def _compute(self, book: RateBook, params: dict[str, Any], result: QuoteResult) -> None:
        """在 result 上填写明细；不可投保时把 status 设为 ineligible 并写原因。"""

    @abstractmethod
    def check(self, book: RateBook) -> list[str]:
        """业务层面的数据校验（如交费期越长、每千元保费对应的保额越高），发现提取错位。"""

    def quote(self, book: RateBook, raw: Mapping[str, Any]) -> QuoteResult:
        result = QuoteResult(product=book.product, status="ok", rate_version=book.version)
        specs = self.param_specs(book)
        try:
            params, missing = parse_params(specs, raw)
        except _Invalid as e:
            result.status = "invalid"
            result.messages.append(str(e))
            return result
        result.params = params
        if missing:
            result.status = "missing_params"
            result.missing = missing
            labels = {s.name: s.label for s in specs}
            result.messages.append(f"缺少参数：{'、'.join(labels[m] for m in missing)}")
            return result
        self._compute(book, params, result)
        if result.status == "ok":
            total = sum((line.premium for line in result.lines), Decimal(0))
            result.annual_premium = total.quantize(CENT)
            result.pay_years = result.pay_years or 1
            result.total_premium = (total * result.pay_years).quantize(CENT)
        else:
            result.lines = []
        return result


def _ineligible(result: QuoteResult, message: str) -> None:
    result.status = "ineligible"
    result.messages.append(message)


def _age_spec(table: RateTable, max_age: int | None = None, newborn_note: str = "") -> ParamSpec:
    lo, hi = table.age_range()
    if max_age is not None:
        hi = min(hi, max_age)
    hint = f"投保年龄 {lo}–{hi} 周岁"
    if newborn_note:
        # 引擎只按整岁计算，出生天数要求只能提示，由投保时核实（spec §6.4）
        hint += f"，0 周岁须{newborn_note}"
    return ParamSpec("age", "被保险人年龄", "age", min=lo, max=hi, hint=hint)


@dataclass(frozen=True)
class FixedPlanRule(ProductRule):
    """按计划固定保费（意外险）：费率表只有「承保年龄 × 计划」。"""

    table: str = "base"

    def param_specs(self, book: RateBook) -> list[ParamSpec]:
        t = book.table(self.table)
        plans = tuple((p, p) for p in t.choices("plan"))
        return [_age_spec(t), ParamSpec("plan", "保障计划", "choice", choices=plans)]

    def _compute(self, book: RateBook, params: dict[str, Any], result: QuoteResult) -> None:
        t = book.table(self.table)
        age, plan = params["age"], params["plan"]
        item = t.lookup(age, plan=plan)
        if item is None:
            lo, hi = t.age_range()
            _ineligible(result, f"{age} 周岁不在承保年龄 {lo}–{hi} 周岁内")
            return
        result.lines.append(
            QuoteLine(
                code=self.table,
                title=f"{t.title}（{plan}）",
                premium=item.value,
                basis=f"费率表第 {item.page} 页：{_age_text(item)} {plan} {_fmt(item.value)} 元",
                page=item.page,
            )
        )

    def check(self, book: RateBook) -> list[str]:
        t = book.table(self.table)
        problems = []
        if t.unit != UNIT_PREMIUM or t.dims != ("plan",):
            problems.append(f"{book.product}: 固定保费表应为 premium × (plan)")
        # 费率表从左到右是从低档到高档，保费应递增；不递增说明列对错了
        lo, _ = t.age_range()
        prices = [t.lookup(lo, plan=p) for p in t.choices("plan")]
        values = [p.value for p in prices if p]
        if values != sorted(values) or len(set(values)) != len(values):
            problems.append(f"{book.product}: 各计划保费未按档次递增 {values}")
        return problems


@dataclass(frozen=True)
class Addon:
    code: str  # 费率表 code，同时是可选责任的取值
    title: str
    max_new_age: int  # 首次投保的最高年龄（费率表里更高年龄的行只适用于续保）


@dataclass(frozen=True)
class MedicalRule(ProductRule):
    """百万医疗：必选计划按「年龄段 × 社保」，可选加油包逐项相加。只报首次投保的个人单价格。"""

    max_new_age: int
    addons: tuple[Addon, ...]
    base: str = "base"
    newborn_note: str = ""

    def param_specs(self, book: RateBook) -> list[ParamSpec]:
        t = book.table(self.base)
        return [
            _age_spec(t, self.max_new_age, self.newborn_note),
            ParamSpec("social_security", "是否有社保", "bool"),
            ParamSpec(
                "addons",
                "可选责任",
                "multi",
                required=False,
                choices=tuple((a.code, a.title) for a in self.addons),
            ),
        ]

    def _key(self, table: RateTable, params: dict[str, Any]) -> dict[str, str]:
        key: dict[str, str] = {}
        if "social_security" in table.dims:
            key["social_security"] = "yes" if params["social_security"] else "no"
        return key

    def _line(self, table: RateTable, item: RateItem, key: dict[str, str]) -> QuoteLine:
        dims = " ".join(SOCIAL_SECURITY_LABELS[v] for v in key.values())
        basis = f"费率表第 {item.page} 页：{_age_text(item)} {dims} {_fmt(item.value)} 元"
        return QuoteLine(
            code=table.code,
            title=table.title,
            premium=item.value,
            basis=basis.replace("  ", " "),
            page=item.page,
        )

    def _compute(self, book: RateBook, params: dict[str, Any], result: QuoteResult) -> None:
        age = params["age"]
        if age > self.max_new_age:
            _ineligible(result, f"首次投保年龄最高 {self.max_new_age} 周岁，更高年龄仅限续保")
            return
        base = book.table(self.base)
        key = self._key(base, params)
        item = base.lookup(age, **key)
        if item is None:
            lo, hi = base.age_range()
            _ineligible(result, f"{age} 周岁不在投保年龄 {lo}–{hi} 周岁内")
            return
        result.lines.append(self._line(base, item, key))
        by_code = {a.code: a for a in self.addons}
        for code in params["addons"]:
            addon = by_code[code]
            table = book.table(code)
            key = self._key(table, params)
            item = table.lookup(age, **key)
            if age > addon.max_new_age or item is None:
                _ineligible(
                    result, f"{addon.title}首次投保最高 {addon.max_new_age} 周岁，{age} 周岁不能选"
                )
                continue
            result.lines.append(self._line(table, item, key))

    def check(self, book: RateBook) -> list[str]:
        problems = []
        codes = {self.base, *(a.code for a in self.addons)}
        if missing := codes - set(book.tables):
            problems.append(f"{book.product}: 缺少费率表 {sorted(missing)}")
            return problems
        for code in sorted(codes):
            t = book.table(code)
            if not set(t.dims) <= {"social_security"}:
                problems.append(f"{book.product}/{code}: 不支持的维度 {t.dims}")
                continue
            if "social_security" not in t.dims:
                continue
            # 同一年龄段，无社保的价格不应低于有社保
            for item in t.items:
                if item.key["social_security"] != "yes":
                    continue
                other = t.lookup(item.age_min, social_security="no")
                if other is not None and other.value < item.value:
                    problems.append(
                        f"{book.product}/{code}: {item.age_min} 周岁无社保 {other.value} "
                        f"低于有社保 {item.value}"
                    )
        return problems


@dataclass(frozen=True)
class LifeRule(ProductRule):
    """增额终身寿：费率表给出「每 1000 元年交保费对应的基本保额」，按保费反算保额。"""

    table: str = "base"
    newborn_note: str = ""

    def param_specs(self, book: RateBook) -> list[ParamSpec]:
        t = book.table(self.table)
        return [
            _age_spec(t, newborn_note=self.newborn_note),
            ParamSpec("gender", "性别", "choice", choices=tuple(GENDER_LABELS.items())),
            ParamSpec(
                "pay_term",
                "交费期",
                "choice",
                choices=tuple((c, PAY_TERMS[c][0]) for c in t.choices("pay_term")),
            ),
            ParamSpec("annual_premium", "年交保费（元）", "money"),
        ]

    def _compute(self, book: RateBook, params: dict[str, Any], result: QuoteResult) -> None:
        t = book.table(self.table)
        age, gender, pay_term = params["age"], params["gender"], params["pay_term"]
        premium: Decimal = params["annual_premium"]
        item = t.lookup(age, gender=gender, pay_term=pay_term)
        if item is None:
            options = t.available("pay_term", age, gender=gender)
            if options:
                labels = "、".join(PAY_TERMS[o][0] for o in options)
                _ineligible(result, f"{age} 周岁不能选{PAY_TERMS[pay_term][0]}，可选：{labels}")
            else:
                lo, hi = t.age_range()
                _ineligible(result, f"{age} 周岁不在投保年龄 {lo}–{hi} 周岁内")
            return
        label, years = PAY_TERMS[pay_term]
        sum_assured = (premium * item.value / 1000).quantize(CENT, rounding=ROUND_HALF_UP)
        result.pay_years = years
        result.sum_assured = sum_assured
        result.lines.append(
            QuoteLine(
                code=self.table,
                title=t.title,
                premium=premium,
                basis=(
                    f"费率表第 {item.page} 页：{_age_text(item)} {GENDER_LABELS[gender]} {label}"
                    f" 每 1,000 元年交保费对应基本保额 {_fmt(item.value)} 元；"
                    f"{_fmt(premium)} ÷ 1,000 × {_fmt(item.value)} = {_fmt(sum_assured)} 元"
                ),
                page=item.page,
            )
        )

    def check(self, book: RateBook) -> list[str]:
        t = book.table(self.table)
        problems = []
        if t.unit != UNIT_SUM_ASSURED_PER_1000 or set(t.dims) != {"gender", "pay_term"}:
            problems.append(f"{book.product}: 保额表应为 sum_assured_per_1000 × (gender, pay_term)")
            return problems
        unknown = set(t.choices("pay_term")) - set(PAY_TERMS)
        if unknown:
            problems.append(f"{book.product}: 未知交费期 {sorted(unknown)}")
            return problems
        terms = sorted(t.choices("pay_term"), key=lambda c: PAY_TERMS[c][1])
        lo, hi = t.age_range()
        for gender in GENDER_LABELS:
            prev_col: dict[str, Decimal] = {}
            for age in range(lo, hi + 1):
                row = [t.lookup(age, gender=gender, pay_term=p) for p in terms]
                values = [i.value for i in row if i is not None]
                # 同一年龄：交费期越长，每千元年交保费买到的保额越多
                if values != sorted(values) or len(set(values)) != len(values):
                    problems.append(f"{book.product}: {gender} {age} 周岁保额未随交费期递增")
                # 同一列：年龄越大，同样保费买到的保额不会更多
                for p, i in zip(terms, row, strict=True):
                    if i is None:
                        continue
                    if p in prev_col and i.value > prev_col[p]:
                        problems.append(f"{book.product}: {gender} {p} {age} 周岁保额比上一岁高")
                    prev_col[p] = i.value
        return problems


ZHONGAN_ADDONS = (
    Addon("family_deductible", "家庭共享免赔额加油包", 70),
    Addon("ci_special_care", "重大疾病扩展特需医疗加油包", 70),
    Addon("hospital_compensation", "住院医疗费用补偿加油包", 70),
    Addon("outpatient", "门急诊医疗加油包", 55),
    Addon("hospital_allowance", "住院津贴加油包", 70),
    Addon("drug", "药省保加油包", 70),
)

# 产品名 → 报价规则。产品名与 app/seed/products.json、费率 JSON 的 product 字段一致。
RULES: dict[str, ProductRule] = {
    "太平洋小蜜蜂6号综合意外险": FixedPlanRule(),
    "太平洋小蜜蜂6号玫瑰版综合意外险": FixedPlanRule(),
    "人保小学童2号Pro学平险": FixedPlanRule(),
    # 出生天数要求取自费率表本身（众安的注释、新华的表头），其余产品的表里没有写
    "众安尊享e生百万医疗险2026版": MedicalRule(
        max_new_age=70, addons=ZHONGAN_ADDONS, newborn_note="出生满 30 天"
    ),
    "中英人寿福满佳C款（悦享版）": LifeRule(),
    "太保福有余2025": LifeRule(),
    "新华人寿E增福优享版": LifeRule(newborn_note="出生满 30 日"),
}


def jsonable_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """规范化后的参数转成可存 JSON 的形式：金额转字符串，其余（年龄、布尔、列表）原样。"""
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in params.items()}


def rule_for(product: str) -> ProductRule | None:
    return RULES.get(product)


def quote(book: RateBook, params: Mapping[str, Any]) -> QuoteResult:
    rule = rule_for(book.product)
    if rule is None:
        raise KeyError(f"产品 {book.product} 没有报价规则")
    return rule.quote(book, params)
