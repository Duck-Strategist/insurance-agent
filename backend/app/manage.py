"""运维命令。

uv run python -m app.manage promote-admin <用户名>   把已注册用户设为管理员
uv run python -m app.manage demote-admin <用户名>    取消管理员
"""

import argparse
import asyncio
import sys

from sqlalchemy import update

from app.core.config import get_settings
from app.infra.db import Database
from app.models import User, UserRole


async def set_role(username: str, role: UserRole) -> int:
    db = Database(get_settings().db)
    try:
        async with db.session_factory() as session, session.begin():
            result = await session.execute(
                update(User).where(User.username == username.lower()).values(role=role)
            )
            if result.rowcount == 0:  # type: ignore[attr-defined]
                print(f"用户不存在：{username}", file=sys.stderr)
                return 1
    finally:
        await db.dispose()
    print(f"{username} → {role}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("promote-admin", "demote-admin"):
        sub.add_parser(name).add_argument("username")
    args = parser.parse_args()
    role = UserRole.ADMIN if args.command == "promote-admin" else UserRole.USER
    sys.exit(asyncio.run(set_role(args.username, role)))


if __name__ == "__main__":
    main()
