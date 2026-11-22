from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvalRun
from app.schemas.retrieval import EvalRunOut


class EvalService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_runs(self, suite: str | None, limit: int) -> list[EvalRunOut]:
        # 不取 details（逐题结果，可能有几百 KB），列表只需要配置和指标
        stmt = select(
            EvalRun.id,
            EvalRun.suite,
            EvalRun.dataset,
            EvalRun.dataset_hash,
            EvalRun.config,
            EvalRun.metrics,
            EvalRun.created_at,
        ).order_by(EvalRun.id.desc())
        if suite:
            stmt = stmt.where(EvalRun.suite == suite)
        rows = await self._session.execute(stmt.limit(limit))
        return [EvalRunOut.model_validate(r, from_attributes=True) for r in rows.all()]
