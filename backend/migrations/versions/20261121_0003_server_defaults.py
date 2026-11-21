"""为 users / products 的默认值列加数据库层默认值

原先只有 ORM 的 Python 默认值，直接用 SQL 插入时会违反 NOT NULL。

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (表, 列, 默认值表达式)
DEFAULTS = [
    ("users", "role", "'user'"),
    ("products", "status", "'active'"),
    ("products", "description", "''"),
    ("products", "target_group", "''"),
    ("products", "highlights", "'{}'::text[]"),
]


def upgrade() -> None:
    for table, column, default in DEFAULTS:
        op.alter_column(table, column, server_default=sa.text(default))


def downgrade() -> None:
    for table, column, _ in DEFAULTS:
        op.alter_column(table, column, server_default=None)
