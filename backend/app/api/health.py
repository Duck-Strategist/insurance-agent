from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    database: str | None = None


@router.get("/health", summary="存活检查（不依赖外部服务）")
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", summary="就绪检查（检查数据库连接）")
async def readiness(request: Request, response: Response) -> HealthResponse:
    db_ok: bool = await request.app.state.db.ping()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database="ok" if db_ok else "unavailable",
    )
