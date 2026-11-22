"""报价引擎：纯函数，输入费率表和参数，输出可追溯的报价明细（spec §6.4）。

- 数字只来自费率表和这里的计算，LLM 不参与（D11）。
- 参数缺失时返回 `missing_params`，由调用方（Agent / 前端）去追问，不抛异常。
- 每种费率表格式对应一种规则（ProductRule），产品名 → 规则的映射在 RULES 里。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

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
    "15y": ("15年交", 15),
    "20y": ("20年交", 20),
    "30y": ("30年交", 30),
    "35y": ("35年交", 35),
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
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)  # 保额档位等取值是数字字符串，调用方（如 Agent）可能直接传数字
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
    # 首次投保的最高年龄。众安的表里更高年龄的行只适用于续保，必须在规则里限制；
    # None 表示以费率表为准（星相守只提取了「有等待期」即首次投保的列）
    max_new_age: int | None = None


DEDUCTIBLE_TABLE = "deductible"


def _deductible_label(value: str) -> str:
    amount = int(value)
    return f"{amount // 10000} 万元" if amount and amount % 10000 == 0 else f"{amount:,} 元"


@dataclass(frozen=True)
class MedicalRule(ProductRule):
    """长期/百万医疗：必选责任按「年龄 × [计划] × 社保」查表，可选责任逐项相加。

    - 有 deductible 表时（星相守），必选责任 = 基准费率 × 免赔额调整因子，因子只用于必选责任。
    - 只报首次投保、个人单、标准体的价格。
    """

    max_new_age: int | None
    addons: tuple[Addon, ...]
    base: str = "base"
    newborn_note: str = ""

    def param_specs(self, book: RateBook) -> list[ParamSpec]:
        t = book.table(self.base)
        specs = [_age_spec(t, self.max_new_age, self.newborn_note)]
        if "plan" in t.dims:
            plans = tuple((p, p) for p in t.choices("plan"))
            specs.append(ParamSpec("plan", "保障计划", "choice", choices=plans))
        specs.append(ParamSpec("social_security", "是否有社保", "bool"))
        if DEDUCTIBLE_TABLE in book.tables:
            values = book.table(DEDUCTIBLE_TABLE).choices("deductible")
            specs.append(
                ParamSpec(
                    "deductible",
                    "年免赔额",
                    "choice",
                    choices=tuple((v, _deductible_label(v)) for v in values),
                )
            )
        specs.append(
            ParamSpec(
                "addons",
                "可选责任",
                "multi",
                required=False,
                choices=tuple((a.code, a.title) for a in self.addons),
            )
        )
        return specs

    @staticmethod
    def _key(table: RateTable, params: dict[str, Any]) -> dict[str, str]:
        key: dict[str, str] = {}
        for dim in table.dims:
            if dim == "social_security":
                key[dim] = "yes" if params["social_security"] else "no"
            else:  # plan、deductible
                key[dim] = params[dim]
        return key

    @staticmethod
    def _dims_text(key: dict[str, str]) -> str:
        parts = []
        for dim, value in key.items():
            if dim == "social_security":
                parts.append(SOCIAL_SECURITY_LABELS[value])
            elif dim == "deductible":
                parts.append(f"免赔额 {_deductible_label(value)}")
            else:
                parts.append(value)
        return " ".join(parts)

    def _line(
        self,
        table: RateTable,
        item: RateItem,
        key: dict[str, str],
        factor: tuple[RateItem, dict[str, str]] | None = None,
    ) -> QuoteLine:
        where = " ".join(p for p in (_age_text(item), self._dims_text(key)) if p)
        if factor is None:
            premium = item.value
            basis = f"费率表第 {item.page} 页：{where} {_fmt(item.value)} 元"
        else:
            f_item, f_key = factor
            premium = (item.value * f_item.value).quantize(CENT, rounding=ROUND_HALF_UP)
            basis = (
                f"费率表第 {item.page} 页：{where} 基准费率 {_fmt(item.value)} 元 × "
                f"{self._dims_text({'deductible': f_key['deductible']})} 调整因子 "
                f"{f_item.value * 100:.0f}%（第 {f_item.page} 页）= {_fmt(premium)} 元"
            )
        return QuoteLine(
            code=table.code, title=table.title, premium=premium, basis=basis, page=item.page
        )

    def _compute(self, book: RateBook, params: dict[str, Any], result: QuoteResult) -> None:
        age = params["age"]
        if self.max_new_age is not None and age > self.max_new_age:
            _ineligible(result, f"首次投保年龄最高 {self.max_new_age} 周岁，更高年龄仅限续保")
            return
        base = book.table(self.base)
        key = self._key(base, params)
        item = base.lookup(age, **key)
        if item is None:
            lo, hi = base.age_range()
            _ineligible(result, f"{age} 周岁不在投保年龄 {lo}–{hi} 周岁内")
            return
        factor = None
        if DEDUCTIBLE_TABLE in book.tables:
            f_table = book.table(DEDUCTIBLE_TABLE)
            f_key = self._key(f_table, params)
            f_item = f_table.lookup(FACTOR_AGES[0], **f_key)
            if f_item is None:
                _ineligible(result, f"{self._dims_text(f_key)} 没有对应的免赔额调整因子")
                return
            factor = (f_item, f_key)
        result.lines.append(self._line(base, item, key, factor))
        by_code = {a.code: a for a in self.addons}
        for code in params["addons"]:
            addon = by_code[code]
            table = book.table(code)
            key = self._key(table, params)
            item = table.lookup(age, **key)
            too_old = addon.max_new_age is not None and age > addon.max_new_age
            if too_old or item is None:
                if addon.max_new_age is not None:
                    limit = f"首次投保最高 {addon.max_new_age} 周岁"
                else:
                    lo, hi = table.age_range()
                    limit = f"投保年龄 {lo}–{hi} 周岁"
                _ineligible(result, f"{addon.title}{limit}，{age} 周岁不能选")
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
            if t.unit != UNIT_PREMIUM or not set(t.dims) <= {"social_security", "plan"}:
                problems.append(f"{book.product}/{code}: 不支持的单位或维度 {t.unit} {t.dims}")
                continue
            if "social_security" not in t.dims:
                continue
            # 同一年龄、同一计划，无社保的价格不应低于有社保
            for item in t.items:
                if item.key["social_security"] != "yes":
                    continue
                other = t.lookup(item.age_min, **{**item.key, "social_security": "no"})
                if other is not None and other.value < item.value:
                    problems.append(
                        f"{book.product}/{code}: {item.age_min} 周岁 {dict(item.key)} "
                        f"无社保 {other.value} 低于有社保 {item.value}"
                    )
        if DEDUCTIBLE_TABLE in book.tables:
            f = book.table(DEDUCTIBLE_TABLE)
            if f.unit != UNIT_FACTOR or set(f.dims) != {"deductible", *book.table(self.base).dims}:
                problems.append(f"{book.product}: 免赔额因子表的单位或维度不对 {f.unit} {f.dims}")
            # 免赔额越低，因子越高（同一计划、同一社保情况）
            groups: dict[tuple[str, ...], list[RateItem]] = {}
            for item in f.items:
                rest = tuple(v for k, v in sorted(item.key.items()) if k != "deductible")
                groups.setdefault(rest, []).append(item)
            for rest, items in groups.items():
                ordered = sorted(items, key=lambda i: int(i.key["deductible"]))
                values = [i.value for i in ordered]
                if values != sorted(values, reverse=True):
                    problems.append(f"{book.product}: 免赔额因子未随免赔额递减 {rest} {values}")
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


class NotOffered(Exception):
    """所选责任组合没有对应的计划（如「保至70岁」不含疾病关爱）。消息直接给用户看。"""


@dataclass(frozen=True)
class CriticalIllnessRule(ProductRule):
    """重疾险：费率表按「计划 × 性别 × 交费期 × 年龄 [× 保额]」排成长表。

    计划编号由可选责任的组合决定（plan_of），映射写在代码里，提取时与 PDF 的计划对照表逐行核对。
    - 表里有 sum_assured 维度（医联有盟）：保额只能选表里的档位，单元格就是保费；
    - 否则（达尔文12号）单元格是每 1000 元保额的费率，保费 = 费率 × 保额 ÷ 1000，四舍五入到分。
    """

    options: tuple[tuple[str, str], ...]  # 可选责任（多选）：(取值, 展示名)
    plan_of: Callable[[dict[str, Any]], str]  # 参数 → 计划编号；组合不存在时抛 NotOffered
    extra_params: tuple[ParamSpec, ...] = ()  # 影响计划的其他参数（社保、保障期间、多次给付）
    table: str = "base"
    sum_assured_step: int = 1000  # 按每千元保额计费时，保额须是它的整数倍

    def _per_1000(self, book: RateBook) -> bool:
        return "sum_assured" not in book.table(self.table).dims

    def param_specs(self, book: RateBook) -> list[ParamSpec]:
        t = book.table(self.table)
        specs = [
            _age_spec(t),
            ParamSpec("gender", "性别", "choice", choices=tuple(GENDER_LABELS.items())),
            ParamSpec(
                "pay_term",
                "交费期",
                "choice",
                choices=tuple((c, PAY_TERMS[c][0]) for c in t.choices("pay_term")),
            ),
        ]
        if self._per_1000(book):
            specs.append(
                ParamSpec(
                    "sum_assured",
                    "基本保额（元）",
                    "money",
                    hint=f"须为 {self.sum_assured_step:,} 元的整数倍；保额上限以投保页面为准",
                )
            )
        else:
            values = sorted(t.choices("sum_assured"), key=int)
            specs.append(
                ParamSpec(
                    "sum_assured",
                    "基本保额",
                    "choice",
                    choices=tuple((v, f"{int(v) // 10000} 万元") for v in values),
                )
            )
        specs.extend(self.extra_params)
        specs.append(
            ParamSpec("options", "可选责任", "multi", required=False, choices=self.options)
        )
        return specs

    def _compute(self, book: RateBook, params: dict[str, Any], result: QuoteResult) -> None:
        t = book.table(self.table)
        per_1000 = self._per_1000(book)
        if per_1000:
            sa: Decimal = params["sum_assured"]
            if sa % self.sum_assured_step != 0:
                result.status = "invalid"
                result.messages.append(f"基本保额须为 {self.sum_assured_step:,} 元的整数倍")
                return
        try:
            plan = self.plan_of(params)
        except NotOffered as e:
            _ineligible(result, str(e))
            return
        age, gender, pay_term = params["age"], params["gender"], params["pay_term"]
        key = {"plan": plan, "gender": gender, "pay_term": pay_term}
        if not per_1000:
            key["sum_assured"] = params["sum_assured"]
        item = t.lookup(age, **key)
        if item is None:
            rest = {k: v for k, v in key.items() if k != "pay_term"}
            options = t.available("pay_term", age, **rest)
            if options:
                labels = "、".join(PAY_TERMS[o][0] for o in options)
                _ineligible(result, f"{age} 周岁不能选{PAY_TERMS[pay_term][0]}，可选：{labels}")
            else:
                lo, hi = t.age_range()
                _ineligible(result, f"{age} 周岁不在投保年龄 {lo}–{hi} 周岁内")
            return
        label, years = PAY_TERMS[pay_term]
        where = f"计划 {plan} {_age_text(item)} {GENDER_LABELS[gender]} {label}"
        if per_1000:
            premium = (item.value * sa / 1000).quantize(CENT, rounding=ROUND_HALF_UP)
            basis = (
                f"费率表第 {item.page} 页：{where} 每 1,000 元保额 {_fmt(item.value)} 元；"
                f"{_fmt(sa)} ÷ 1,000 × {_fmt(item.value)} = {_fmt(premium)} 元"
            )
            result.sum_assured = sa
        else:
            premium = item.value
            sa_text = _fmt(Decimal(params["sum_assured"]))
            basis = f"费率表第 {item.page} 页：{where} 保额 {sa_text} 元 {_fmt(premium)} 元"
            result.sum_assured = Decimal(params["sum_assured"]).quantize(CENT)
        result.pay_years = years
        result.lines.append(
            QuoteLine(
                code=self.table,
                title=f"{t.title}（{self._coverage_text(params)}）",
                premium=premium,
                basis=basis,
                page=item.page,
            )
        )

    def _coverage_text(self, params: dict[str, Any]) -> str:
        """所选责任的文字说明，写进明细标题：保存的报价不用查计划编号也知道保了什么。"""
        parts = []
        for spec in self.extra_params:
            value = params.get(spec.name)
            if spec.kind == "bool":
                parts.append(f"{spec.label}：{'是' if value else '否'}")
            elif value is not None:
                parts.append(f"{spec.label}：{dict(spec.choices)[value]}")
        names = dict(self.options)
        chosen = [names[o] for o in params.get("options", [])]
        parts.append("含" + "、".join(chosen) if chosen else "仅必选责任")
        return "；".join(parts)

    def _combos(self) -> list[dict[str, Any]]:
        """可选责任与额外参数的全部组合（用于校验）。"""
        combos: list[dict[str, Any]] = [{"options": []}]
        for spec in self.extra_params:
            if spec.kind == "bool":
                values: list[Any] = [True, False]
            else:
                values = [v for v, _ in spec.choices] + ([] if spec.required else [None])
            combos = [{**c, spec.name: v} for c in combos for v in values]
        codes = [c for c, _ in self.options]
        out = []
        for c in combos:
            for mask in range(1 << len(codes)):
                out.append(
                    {**c, "options": [code for i, code in enumerate(codes) if mask >> i & 1]}
                )
        return out

    def check(self, book: RateBook) -> list[str]:
        t = book.table(self.table)
        problems: list[str] = []
        expected_dims = {"plan", "gender", "pay_term"} | (
            set() if self._per_1000(book) else {"sum_assured"}
        )
        expected_unit = UNIT_PREMIUM_PER_1000_SA if self._per_1000(book) else UNIT_PREMIUM
        if set(t.dims) != expected_dims or t.unit != expected_unit:
            return [f"{book.product}: 重疾表应为 {expected_unit} × {sorted(expected_dims)}"]
        if unknown := set(t.choices("pay_term")) - set(PAY_TERMS):
            return [f"{book.product}: 未知交费期 {sorted(unknown)}"]
        values = {(tuple(sorted(i.key.items())), i.age_min): i.value for i in t.items}

        # 1. 计划映射：每个组合都映射到表里存在的计划，且每个计划都被用到
        plans_used: set[str] = set()
        pairs: set[tuple[str, str]] = set()
        for combo in self._combos():
            try:
                plan = self.plan_of(combo)
            except NotOffered:
                continue
            plans_used.add(plan)
            # 多选一个可选责任，计划应该更贵（发现计划编号映射错位）
            for code, _ in self.options:
                if code in combo["options"]:
                    continue
                try:
                    richer = self.plan_of({**combo, "options": [*combo["options"], code]})
                except NotOffered:
                    continue
                pairs.add((plan, richer))
        if missing := set(t.choices("plan")) - plans_used:
            problems.append(f"{book.product}: 计划 {sorted(missing, key=int)} 没有对应的责任组合")
        if extra := plans_used - set(t.choices("plan")):
            problems.append(f"{book.product}: 责任组合映射到不存在的计划 {sorted(extra, key=int)}")
        for (key, age), value in values.items():
            k = dict(key)
            for lower, higher in pairs:
                if k["plan"] != lower:
                    continue
                other = values.get((tuple(sorted({**k, "plan": higher}.items())), age))
                if other is not None and other <= value:
                    problems.append(
                        f"{book.product}: {age} 周岁 {k} 加选责任后（计划 {higher}）"
                        f"保费 {other} 不高于 {value}"
                    )
        # 2. 同一年龄、同一计划：交费期越长，每年保费越低
        terms = sorted(t.choices("pay_term"), key=lambda c: PAY_TERMS[c][1])
        for (key, age), value in values.items():
            k = dict(key)
            i = terms.index(k["pay_term"])
            for longer in terms[i + 1 :]:
                other = values.get((tuple(sorted({**k, "pay_term": longer}.items())), age))
                if other is not None and other >= value:
                    problems.append(
                        f"{book.product}: {age} 周岁 {k} 交费期 {longer} "
                        f"的保费 {other} 不低于 {value}"
                    )
                    break
        return problems[:50]


def _yilian_plan(params: dict[str, Any]) -> str:
    """医联有盟：计划一～八 = 1 + 轻中症×1 + 身故×2 + 无医保×4（第 1 页计划表，提取时逐行核对）。"""
    opts = params["options"]
    return str(
        1 + ("mild_moderate" in opts) + 2 * ("death" in opts) + 4 * (not params["social_security"])
    )


DARWIN_LIFE_WEIGHTS = {
    "death": 48,
    "disease_care": 24,
    "cancer_allowance": 12,
    "premium_compensation": 6,
    "pillar_care": 3,
}
DARWIN_MULTI_PAY = {None: 0, "plan1": 1, "plan2": 2}
# 保至70岁计划对照表（第 817 页）：(身故, 保费补偿, 顶梁柱) → 计划
DARWIN_TO70 = {
    (False, False, False): "97",
    (True, False, False): "98",
    (False, True, False): "99",
    (False, False, True): "100",
    (True, True, False): "101",
    (True, False, True): "102",
    (False, True, True): "103",
    (True, True, True): "104",
}
DARWIN_TO70_OPTIONS = {"death", "premium_compensation", "pillar_care"}


def _darwin_plan(params: dict[str, Any]) -> str:
    """达尔文12号：终身计划 1～96 按可选责任的混合进制编号（第 1 页计划对照表，提取时逐行核对）。"""
    opts = set(params["options"])
    multi = params.get("multi_pay")
    if params["coverage"] == "life":
        return str(1 + sum(DARWIN_LIFE_WEIGHTS[o] for o in opts) + DARWIN_MULTI_PAY[multi])
    names = dict(DARWIN_OPTIONS)
    labels = [names[o] for o in sorted(opts - DARWIN_TO70_OPTIONS)]
    if multi:
        labels.append("重大疾病多次给付保险金")
    if labels:
        raise NotOffered(f"保至70岁计划不含：{'、'.join(labels)}")
    return DARWIN_TO70[("death" in opts, "premium_compensation" in opts, "pillar_care" in opts)]


DARWIN_OPTIONS = (
    ("death", "身故或全残保险金"),
    ("disease_care", "疾病关爱保险金"),
    ("cancer_allowance", "「恶性肿瘤——重度」治疗津贴保险金"),
    ("premium_compensation", "重大疾病保费补偿金"),
    ("pillar_care", "顶梁柱关爱保险金"),
)
DARWIN_EXTRA = (
    ParamSpec(
        "coverage",
        "保障期间",
        "choice",
        choices=(("life", "终身"), ("to70", "保至70岁")),
    ),
    ParamSpec(
        "multi_pay",
        "重大疾病多次给付",
        "choice",
        required=False,
        choices=(("plan1", "方案一"), ("plan2", "方案二")),
        hint="不选表示不含；仅终身计划可选",
    ),
)
YILIAN_OPTIONS = (
    ("mild_moderate", "轻度、中度疾病保险金（含对应豁免）"),
    ("death", "身故或全残保险金"),
)
YILIAN_EXTRA = (
    ParamSpec(
        "social_security", "是否有医保", "bool", hint="决定所含一般医疗保险金的版本（有/无医保）"
    ),
)


ZHONGAN_ADDONS = (
    Addon("family_deductible", "家庭共享免赔额加油包", 70),
    Addon("ci_special_care", "重大疾病扩展特需医疗加油包", 70),
    Addon("hospital_compensation", "住院医疗费用补偿加油包", 70),
    Addon("outpatient", "门急诊医疗加油包", 55),
    Addon("hospital_allowance", "住院津贴加油包", 70),
    Addon("drug", "药省保加油包", 70),
)

# 星相守2号：只提取「有等待期」（首次投保）的列；重大疾病补充关爱（按每千元保额、分性别）
# 费率表没给保额范围，暂不开放
XIANGSHOU_ADDONS = (
    Addon("basic_medical", "基础医疗保险金"),
    Addon("ci_rehab", "重大疾病康复医疗保险金"),
    Addon("ci_allowance", "重大疾病住院或重症监护病房住院津贴保险金"),
    Addon("cancer_drug", "恶性肿瘤-重度特定药品费用医疗保险金"),
    Addon("ci_special_care", "重大疾病住院拓展特需医疗保险金"),
    Addon("outpatient", "附加长期门急诊医疗保险"),
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
    "复星联合医联有盟重疾险": CriticalIllnessRule(
        options=YILIAN_OPTIONS, plan_of=_yilian_plan, extra_params=YILIAN_EXTRA
    ),
    "达尔文12号重大疾病保险": CriticalIllnessRule(
        options=DARWIN_OPTIONS, plan_of=_darwin_plan, extra_params=DARWIN_EXTRA
    ),
    "复星联合星相守2号长期医疗险（个人版）": MedicalRule(max_new_age=None, addons=XIANGSHOU_ADDONS),
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
