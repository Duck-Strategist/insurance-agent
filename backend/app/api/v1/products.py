from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import SessionDep
from app.models import ProductCategory
from app.schemas.product import Page, ProductOut
from app.services.product_service import ProductService

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", summary="产品列表（公开）")
async def list_products(
    session: SessionDep,
    category: ProductCategory | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 12,
) -> Page[ProductOut]:
    items, total = await ProductService(session).list_products(category, page, page_size)
    return Page[ProductOut](
        items=[ProductOut.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{product_id}", summary="产品详情（公开）")
async def get_product(product_id: int, session: SessionDep) -> ProductOut:
    product = await ProductService(session).get_product(product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="产品不存在")
    return ProductOut.model_validate(product)
