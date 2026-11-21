"""ORM 模型汇总。

每新增一个模型模块，都在这里导入一次，保证 Alembic autogenerate 能看到它。
"""

from app.models.document import Chunk, ChunkLevel, Document, DocumentStatus
from app.models.product import Product, ProductCategory, ProductStatus
from app.models.user import User, UserRole

__all__ = [
    "Chunk",
    "ChunkLevel",
    "Document",
    "DocumentStatus",
    "Product",
    "ProductCategory",
    "ProductStatus",
    "User",
    "UserRole",
]
