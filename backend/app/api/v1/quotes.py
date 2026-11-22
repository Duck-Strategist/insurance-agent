"""报价（M5）：试算公开，保存与查看报价记录需要登录。"""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.pricing import (
    ParamSpecOut,
    PricingInfoOut,
    QuoteLineOut,
    QuoteOut,
    QuoteRequest,
    SavedQuoteOut,
    SaveQuoteRequest,
)
from app.services.pricing_service import PricingService

router = APIRouter(tags=["pricing"])

_NOT_FOUND = "产品不存在"
_UNSUPPORTED = "该产品暂不支持在线报价"


@router.get("/products/{product_id}/pricing", summary="报价所需参数（公开）")
async def pricing_info(product_id: int, session: SessionDep) -> PricingInfoOut:
    found = await PricingService(session).param_specs(product_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    _, specs = found
    return PricingInfoOut(
        product_id=product_id,
        supported=specs is not None,
        params=[ParamSpecOut.of(s) for s in specs or []],
    )


@router.post("/products/{product_id}/quote", summary="试算报价（公开，不保存）")
async def calculate(product_id: int, body: QuoteRequest, session: SessionDep) -> QuoteOut:
    """参数缺失、格式错误、不可投保都返回 200，用 status 区分，便于前端和 Agent 追问。"""
    found = await PricingService(session).quote(product_id, body.params)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    _, _, result = found
    if result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=_UNSUPPORTED)
    return QuoteOut.of(product_id, result)


def _lines(breakdown: list[Any]) -> list[QuoteLineOut]:
    return [QuoteLineOut.model_validate(line) for line in breakdown]


@router.post("/quotes", status_code=status.HTTP_201_CREATED, summary="保存报价")
async def save_quote(
    body: SaveQuoteRequest, user: CurrentUser, session: SessionDep
) -> SavedQuoteOut:
    service = PricingService(session)
    found = await service.quote(body.product_id, body.params)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    product, record, result = found
    if record is None or result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=_UNSUPPORTED)
    if result.status != "ok":
        # 服务端重新计算，不信任前端传来的金额；算不出来就不保存
        # messages 已是给用户看的中文（缺参时为「缺少参数：被保险人年龄、…」）
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="；".join(result.messages)
        )
    row = await service.save(user, product, record, result)
    return SavedQuoteOut(
        id=row.id,
        product_id=product.id,
        product_name=product.name,
        rate_version=record.version,
        params=row.params,
        lines=_lines(row.breakdown),
        annual_premium=row.annual_premium,
        pay_years=row.pay_years,
        total_premium=row.total_premium,
        sum_assured=row.sum_assured,
        created_at=row.created_at,
    )


@router.get("/quotes", summary="我的报价记录（新的在前）")
async def list_quotes(user: CurrentUser, session: SessionDep) -> list[SavedQuoteOut]:
    rows = await PricingService(session).list_quotes(user)
    return [
        SavedQuoteOut(
            id=q.id,
            product_id=q.product_id,
            product_name=name,
            rate_version=version,
            params=q.params,
            lines=_lines(q.breakdown),
            annual_premium=q.annual_premium,
            pay_years=q.pay_years,
            total_premium=q.total_premium,
            sum_assured=q.sum_assured,
            created_at=q.created_at,
        )
        for q, name, version in rows
    ]
