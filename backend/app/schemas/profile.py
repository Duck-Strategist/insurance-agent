"""长期画像（M7，spec D8、D20、D21）。页面保存和对话里确认保存走同一套校验。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from app.models import ProductCategory

Relation = Literal["spouse", "child", "parent"]
Gender = Literal["male", "female"]
RELATION_LABELS = {"spouse": "配偶", "child": "子女", "parent": "父母"}
MEMBER_ID = r"^[a-z0-9]{1,12}$"
MAX_AGE = 105
MAX_MONEY = Decimal("1000000000")


def _money_in(value: Any) -> Any:
    # 与报价接口一致：只接受字符串或整数，float 会带来二进制误差（CLAUDE.md 报价约定）
    if isinstance(value, bool | float):
        raise ValueError("金额请用字符串或整数")
    return value


def _quantize(value: Decimal) -> Decimal:
    if not value.is_finite() or value < 0 or value > MAX_MONEY:
        raise ValueError("金额超出范围")
    return value.quantize(Decimal("0.01"))


MoneyField = Annotated[
    Decimal,
    BeforeValidator(_money_in),
    PlainSerializer(lambda v: f"{v:.2f}", return_type=str),
]


def age_on(birth: date, today: date) -> int:
    """周岁。"""
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def check_birth_date(value: date | None, today: date | None = None) -> date | None:
    if value is None:
        return None
    today = today or date.today()
    if value > today:
        raise ValueError("出生日期不能晚于今天")
    if age_on(value, today) > MAX_AGE:
        raise ValueError(f"年龄不能超过 {MAX_AGE} 岁")
    return value


class FamilyMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 成员 id 由前端或服务端生成，已有保障用它指向被保险人；删除成员不会让下标错位
    id: str = Field(pattern=MEMBER_ID)
    relation: Relation
    name: str = Field(default="", max_length=20)  # 称呼，如「大女儿」，区分多个子女
    birth_date: date | None = None
    # 出生日期由对话里说的年龄估算（不知道生日），页面上提示用户改成准确日期
    birth_date_estimated: bool = False
    gender: Gender | None = None
    social_security: bool | None = None

    _birth = field_validator("birth_date")(check_birth_date)


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insured: str = Field(pattern=r"^(self|[a-z0-9]{1,12})$")  # self 或家庭成员 id
    # 库内产品：服务端按 id 重新填产品名和险种；为空表示库外产品，只能做通用理赔咨询（spec §4）
    product_id: int | None = None
    product_name: str = Field(min_length=1, max_length=50)
    category: ProductCategory | None = None
    sum_assured: MoneyField | None = None
    start_date: date | None = None  # 生效日期：理赔咨询时用来判断等待期

    _money = field_validator("sum_assured")(lambda v: None if v is None else _quantize(v))

    @field_validator("start_date")
    @classmethod
    def _sane_start(cls, v: date | None) -> date | None:
        if v is not None and v.year < 1990:
            raise ValueError("生效日期不正确")
        return v


class ProfileBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    birth_date: date | None = None
    gender: Gender | None = None
    social_security: bool | None = None
    occupation_class: int | None = Field(default=None, ge=1, le=6)
    city: str | None = Field(default=None, max_length=32)
    annual_budget: MoneyField | None = None

    _birth = field_validator("birth_date")(check_birth_date)
    _money = field_validator("annual_budget")(lambda v: None if v is None else _quantize(v))

    @field_validator("city")
    @classmethod
    def _strip_city(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None


class ProfileData(ProfileBase):
    """整份画像（页面保存时提交）。"""

    family: list[FamilyMember] = Field(default_factory=list, max_length=10)
    existing_coverage: list[Coverage] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [m.id for m in self.family]
        if len(ids) != len(set(ids)):
            raise ValueError("家庭成员 id 重复")
        if sum(m.relation == "spouse" for m in self.family) > 1:
            raise ValueError("只能登记一位配偶")
        valid = {"self", *ids}
        for c in self.existing_coverage:
            if c.insured not in valid:
                raise ValueError(f"「{c.product_name}」的被保险人不在家庭成员里")
        return self


class ProfileOut(ProfileData):
    model_config = ConfigDict(extra="ignore")

    age: int | None = None  # 按出生日期算出的周岁
    # 字段 → manual / chat / chat_age（由对话中的年龄估算出生日期）
    field_sources: dict[str, str] = Field(default_factory=dict)
    updated_at: datetime | None = None  # 为空表示还没有画像


class ProfilePatch(ProfileBase):
    """对话里抽取出的画像增量（spec D20）：用户点「保存到画像」后才写库。

    只有出现的字段会写入（exclude_unset）；家庭成员按 id 增改，已有保障按
    （被保险人, 产品 id 或产品名）增改，不会删除任何已有内容。"""

    birth_date_estimated: bool = False
    family: list[FamilyMember] = Field(default_factory=list, max_length=10)
    existing_coverage: list[Coverage] = Field(default_factory=list, max_length=20)


class SuggestionItem(BaseModel):
    label: str
    value: str


class ProfileSuggestion(BaseModel):
    """SSE profile_suggestion 事件：items 给用户看，patch 在用户确认后原样提交。"""

    items: list[SuggestionItem]
    patch: dict[str, Any]
