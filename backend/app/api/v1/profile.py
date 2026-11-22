"""我的画像（M7，spec D8、D20）：页面查看与整份保存；对话里的建议经用户确认后合并写入。"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.profile import ProfileData, ProfileOut, ProfilePatch
from app.services.profile_service import ProfileError, ProfileService, to_out

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", summary="我的画像（还没有时返回空画像，updated_at 为空）")
async def get_profile(user: CurrentUser, session: SessionDep) -> ProfileOut:
    return to_out(await ProfileService(session).get(user.id))


@router.put("", summary="保存整份画像（页面编辑）")
async def put_profile(user: CurrentUser, session: SessionDep, body: ProfileData) -> ProfileOut:
    try:
        return await ProfileService(session).replace(user.id, body)
    except ProfileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


@router.post("/suggestions", summary="保存对话里的画像建议（增量合并，不删除已有内容）")
async def apply_suggestion(
    user: CurrentUser, session: SessionDep, body: ProfilePatch
) -> ProfileOut:
    try:
        return await ProfileService(session).apply_patch(user.id, body)
    except ProfileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
