"""ORM 模型汇总。

每新增一个模型模块，都在这里导入一次，保证 Alembic autogenerate 能看到它。
"""

from app.models.chat import ChatThread
from app.models.document import Chunk, ChunkLevel, Document, DocumentStatus
from app.models.eval_run import EvalRun
from app.models.plan import Plan, PlanStatus
from app.models.pricing import Quote, RateBookRecord
from app.models.product import Product, ProductCategory, ProductStatus
from app.models.profile import UserProfile
from app.models.user import User, UserRole

__all__ = [
    "ChatThread",
    "Chunk",
    "ChunkLevel",
    "Document",
    "DocumentStatus",
    "EvalRun",
    "Plan",
    "PlanStatus",
    "Product",
    "ProductCategory",
    "ProductStatus",
    "Quote",
    "RateBookRecord",
    "User",
    "UserProfile",
    "UserRole",
]
