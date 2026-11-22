"""我的方案（M6）：顾问生成并由用户确认的保障方案。"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, SessionDep
from app.models import Plan
from app.services.plan_service import PlanService

router = APIRouter(prefix="/plans", tags=["plans"])


class PlanOut(BaseModel):
    id: int
    thread_id: str | None
    status: str
    slots: dict[str, Any]
    items: list[dict[str, Any]]
    summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, row: Plan) -> "PlanOut":
        return cls.model_validate(row, from_attributes=True)


@router.get("", summary="我的方案（已确认和待确认，新的在前）")
async def list_plans(user: CurrentUser, session: SessionDep) -> list[PlanOut]:
    return [PlanOut.of(p) for p in await PlanService(session).list_for_user(user.id)]


@router.get("/{plan_id}", summary="方案详情")
async def get_plan(plan_id: int, user: CurrentUser, session: SessionDep) -> PlanOut:
    row = await PlanService(session).get(user.id, plan_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="方案不存在")
    return PlanOut.of(row)
