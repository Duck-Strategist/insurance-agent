"""保障方案：顾问生成的方案卡片落库、状态流转、我的方案列表。"""

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plan, PlanStatus


class PlanService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_pending(self, user_id: int, thread_id: str, plan: dict[str, Any]) -> Plan:
        row = Plan(
            user_id=user_id,
            thread_id=thread_id,
            status=PlanStatus.PENDING,
            slots=plan["slots"],
            items=plan["items"],
            summary={
                "budget": plan["budget"],
                "total_annual_premium": plan["total_annual_premium"],
                "dropped": plan["dropped"],
            },
        )
        self.session.add(row)
        await self.session.commit()
        return row

    async def transition(self, plan_id: int, user_id: int, to: PlanStatus) -> bool:
        """只有 pending 的方案能改状态；返回是否改成功（重复点击确认时第二次返回 False）。"""
        result = await self.session.execute(
            update(Plan)
            .where(
                Plan.id == plan_id,
                Plan.user_id == user_id,
                Plan.status == PlanStatus.PENDING,
            )
            .values(status=to)
        )
        await self.session.commit()
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def expire_pending_for_thread(self, user_id: int, thread_id: str) -> int:
        result = await self.session.execute(
            update(Plan)
            .where(
                Plan.user_id == user_id,
                Plan.thread_id == thread_id,
                Plan.status == PlanStatus.PENDING,
            )
            .values(status=PlanStatus.EXPIRED)
        )
        await self.session.commit()
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def statuses(self, user_id: int, plan_ids: Iterable[int]) -> dict[int, str]:
        ids = list(set(plan_ids))
        if not ids:
            return {}
        rows = await self.session.execute(
            select(Plan.id, Plan.status).where(Plan.id.in_(ids), Plan.user_id == user_id)
        )
        return {pid: status for pid, status in rows.all()}

    async def list_for_user(self, user_id: int, limit: int = 50) -> Sequence[Plan]:
        """我的方案：已确认和待确认的（被取代、取消、过期的不展示）。"""
        rows = await self.session.scalars(
            select(Plan)
            .where(
                Plan.user_id == user_id,
                Plan.status.in_([PlanStatus.CONFIRMED, PlanStatus.PENDING]),
            )
            .order_by(Plan.created_at.desc(), Plan.id.desc())
            .limit(limit)
        )
        return rows.all()

    async def get(self, user_id: int, plan_id: int) -> Plan | None:
        return await self.session.scalar(
            select(Plan).where(Plan.id == plan_id, Plan.user_id == user_id)
        )
