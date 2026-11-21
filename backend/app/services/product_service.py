from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, ProductCategory, ProductStatus


class ProductService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_products(
        self, category: ProductCategory | None, page: int, page_size: int
    ) -> tuple[Sequence[Product], int]:
        conditions = [Product.status == ProductStatus.ACTIVE]
        if category is not None:
            conditions.append(Product.category == category)

        total = await self.session.scalar(
            select(func.count()).select_from(Product).where(*conditions)
        )
        rows = await self.session.scalars(
            select(Product)
            .where(*conditions)
            # 按 id 排序保证分页稳定，不会翻页时重复或漏掉
            .order_by(Product.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return rows.all(), total or 0

    async def get_product(self, product_id: int) -> Product | None:
        product = await self.session.get(Product, product_id)
        if product is None or product.status != ProductStatus.ACTIVE:
            return None
        return product
