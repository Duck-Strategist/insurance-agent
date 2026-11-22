"""评测里每道题的网络调用失败时重试：几十上百道题跑十分钟，偶发的断连不该让整次运行作废。
认证错误不重试（CLAUDE.md：401/403 要停止批处理）；重试完仍失败就抛出，整次运行不写记录，
不留下少了几道题的指标。"""

import asyncio
from collections.abc import Awaitable, Callable

import openai

from app.core.logging import get_logger

logger = get_logger(__name__)

NO_RETRY = (openai.AuthenticationError, openai.PermissionDeniedError)


async def with_retries[T](
    fn: Callable[[], Awaitable[T]], *, label: str, attempts: int = 3, base_delay: float = 2.0
) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except NO_RETRY:
            raise
        except Exception as exc:
            if attempt == attempts:
                raise
            delay = base_delay * 2 ** (attempt - 1)
            logger.warning(
                "eval.retry", item=label, attempt=attempt, delay_s=delay, error=repr(exc)
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
