"""长期画像：读取、页面整份保存、对话建议增量合并（spec D20、D21）。"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, ProductCategory, UserProfile
from app.schemas.profile import (
    RELATION_LABELS,
    Coverage,
    FamilyMember,
    ProfileData,
    ProfileOut,
    ProfilePatch,
    age_on,
)

SCALARS = ("birth_date", "gender", "social_security", "occupation_class", "city", "annual_budget")
LISTS = ("family", "existing_coverage")


class ProfileError(ValueError):
    """画像内容不合法（如引用了不存在的产品），接口返回 422。"""


def to_data(row: UserProfile | None) -> ProfileData:
    if row is None:
        return ProfileData()
    return ProfileData.model_validate({f: getattr(row, f) for f in (*SCALARS, *LISTS)})


def to_out(row: UserProfile | None, today: date | None = None) -> ProfileOut:
    data = to_data(row)
    return ProfileOut(
        **data.model_dump(),
        age=age_on(data.birth_date, today or date.today()) if data.birth_date else None,
        field_sources=dict(row.field_sources) if row else {},
        updated_at=row.updated_at if row else None,
    )


def _dump(data: ProfileData) -> dict[str, Any]:
    out: dict[str, Any] = jsonable_encoder(data.model_dump(include={*SCALARS, *LISTS}))
    return out


def _coverage_key(c: Coverage) -> tuple[str, str]:
    return (c.insured, f"id:{c.product_id}" if c.product_id is not None else c.product_name)


def merge_patch(current: ProfileData, patch: ProfilePatch) -> ProfileData:
    """把对话建议合并进画像：标量覆盖；成员按 id 增改（只写建议里有值的字段），
    已有保障按（被保险人, 产品）增改。不删除任何已有内容。"""
    values = current.model_dump()
    for field in patch.model_fields_set & set(SCALARS):
        values[field] = getattr(patch, field)

    family = [m.model_copy() for m in current.family]
    remap: dict[str, str] = {}
    for new in patch.family:
        target = next((m for m in family if m.id == new.id), None)
        if target is None and new.relation == "spouse":
            # 建议生成后用户又在页面上加了配偶：合并到已有配偶，不再加第二位
            target = next((m for m in family if m.relation == "spouse"), None)
        if target is None:
            family.append(new)
            continue
        remap[new.id] = target.id
        updates = new.model_dump(exclude={"id", "relation"}, exclude_defaults=True)
        if "birth_date" in updates:
            updates["birth_date_estimated"] = new.birth_date_estimated
        family[family.index(target)] = target.model_copy(update=updates)

    coverage = [c.model_copy() for c in current.existing_coverage]
    for suggested in patch.existing_coverage:
        item = suggested.model_copy(
            update={"insured": remap.get(suggested.insured, suggested.insured)}
        )
        idx = next(
            (i for i, c in enumerate(coverage) if _coverage_key(c) == _coverage_key(item)), None
        )
        if idx is None:
            coverage.append(item)
        else:
            # 只用建议里有值的字段更新（不用 model_dump：金额会被序列化成字符串）
            updates = {
                f: getattr(item, f)
                for f in Coverage.model_fields
                if f != "insured" and getattr(item, f) is not None
            }
            coverage[idx] = coverage[idx].model_copy(update=updates)
    values["family"] = [m.model_dump() for m in family]
    values["existing_coverage"] = [c.model_dump() for c in coverage]
    return ProfileData.model_validate(values)


class ProfileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: int) -> UserProfile | None:
        return await self.session.get(UserProfile, user_id)

    async def _lock(self, user_id: int) -> UserProfile:
        """取画像行并加锁（没有就先建一行）：页面保存和对话建议同时提交时不会互相覆盖。"""
        await self.session.execute(
            insert(UserProfile).values(user_id=user_id).on_conflict_do_nothing()
        )
        row = await self.session.scalar(
            select(UserProfile)
            .where(UserProfile.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert row is not None
        return row

    async def _check_products(self, items: Sequence[Coverage]) -> list[Coverage]:
        """库内产品以数据库为准填写产品名和险种；引用不存在的产品直接拒绝。"""
        ids = {c.product_id for c in items if c.product_id is not None}
        products = {}
        if ids:
            rows = await self.session.scalars(select(Product).where(Product.id.in_(ids)))
            products = {p.id: p for p in rows}
        out = []
        for c in items:
            if c.product_id is None:
                out.append(c)
                continue
            p = products.get(c.product_id)
            if p is None:
                raise ProfileError(f"产品不存在：{c.product_id}")
            out.append(
                c.model_copy(
                    update={"product_name": p.name, "category": ProductCategory(p.category)}
                )
            )
        return out

    async def _save(self, row: UserProfile, data: ProfileData, source: dict[str, str]) -> None:
        """写入并更新字段来源：值变了的字段记为本次来源，清空的字段去掉来源。"""
        data = data.model_copy(
            update={"existing_coverage": await self._check_products(data.existing_coverage)}
        )
        before, after = _dump(to_data(row)), _dump(data)
        sources = dict(row.field_sources)
        for field, value in after.items():
            if value in (None, []):
                sources.pop(field, None)
            elif value != before[field]:
                sources[field] = source.get(field, source["*"])
            setattr(row, field, getattr(data, field) if field in SCALARS else value)
        row.field_sources = sources
        await self.session.commit()
        await self.session.refresh(row)

    async def replace(self, user_id: int, data: ProfileData) -> ProfileOut:
        """页面保存整份画像。"""
        row = await self._lock(user_id)
        old = {m.id: m for m in to_data(row).family}
        family = []
        for m in data.family:
            prev = old.get(m.id)
            # 用户在页面上改了出生日期，就不再是估算值
            if m.birth_date_estimated and (prev is None or prev.birth_date != m.birth_date):
                m = m.model_copy(update={"birth_date_estimated": False})
            family.append(m)
        data = data.model_copy(update={"family": family})
        try:
            await self._save(row, data, {"*": "manual"})
        except ProfileError:
            await self.session.rollback()  # 不留下 _lock 插入的空行
            raise
        return to_out(row)

    async def apply_patch(self, user_id: int, patch: ProfilePatch) -> ProfileOut:
        """对话建议：用户点「保存到画像」后合并写入。"""
        row = await self._lock(user_id)
        try:
            merged = merge_patch(to_data(row), patch)
            source = {"*": "chat"}
            if patch.birth_date_estimated:
                source["birth_date"] = "chat_age"
            await self._save(row, merged, source)
        except ValidationError as exc:
            # 如建议生成后用户在页面上删了这位成员，保障指向的人不存在了
            await self.session.rollback()
            raise ProfileError(exc.errors()[0]["msg"].removeprefix("Value error, ")) from exc
        except ProfileError:
            await self.session.rollback()
            raise
        return to_out(row)


def member_label(member: FamilyMember) -> str:
    return member.name or RELATION_LABELS[member.relation]
