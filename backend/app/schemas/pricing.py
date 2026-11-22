from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.pricing.engine import ParamSpec, QuoteResult, jsonable_params
from app.schemas.product import Money


class ParamChoice(BaseModel):
    value: str
    label: str


class ParamSpecOut(BaseModel):
    name: str
    label: str
    kind: Literal["age", "choice", "bool", "money", "multi"]
    required: bool
    choices: list[ParamChoice]
    min: int | None
    max: int | None
    hint: str

    @classmethod
    def of(cls, spec: ParamSpec) -> "ParamSpecOut":
        return cls(
            name=spec.name,
            label=spec.label,
            kind=spec.kind,
            required=spec.required,
            choices=[ParamChoice(value=v, label=label) for v, label in spec.choices],
            min=spec.min,
            max=spec.max,
            hint=spec.hint,
        )


class PricingInfoOut(BaseModel):
    product_id: int
    supported: bool  # False：暂不支持在线报价（没有报价规则或费率表未入库）
    params: list[ParamSpecOut]


class QuoteRequest(BaseModel):
    # 参数按产品不同而不同，由报价引擎逐项校验；这里只限制大小，防止超大请求体
    params: dict[str, Any] = Field(max_length=20)


class SaveQuoteRequest(QuoteRequest):
    product_id: int


class QuoteLineOut(BaseModel):
    code: str
    title: str
    premium: Money
    basis: str
    page: int


class QuoteOut(BaseModel):
    product_id: int
    product_name: str
    status: Literal["ok", "missing_params", "invalid", "ineligible"]
    rate_version: str
    params: dict[str, Any]
    missing: list[str]
    messages: list[str]
    lines: list[QuoteLineOut]
    annual_premium: Money | None
    pay_years: int | None
    total_premium: Money | None
    sum_assured: Money | None

    @classmethod
    def of(cls, product_id: int, result: QuoteResult) -> "QuoteOut":
        return cls(
            product_id=product_id,
            product_name=result.product,
            status=result.status,
            rate_version=result.rate_version,
            params=jsonable_params(result.params),
            missing=result.missing,
            messages=result.messages,
            lines=[
                QuoteLineOut.model_validate(line, from_attributes=True) for line in result.lines
            ],
            annual_premium=result.annual_premium,
            pay_years=result.pay_years,
            total_premium=result.total_premium,
            sum_assured=result.sum_assured,
        )


class SavedQuoteOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    rate_version: str
    params: dict[str, Any]
    lines: list[QuoteLineOut]
    annual_premium: Money
    pay_years: int
    total_premium: Money
    sum_assured: Money | None
    created_at: datetime
