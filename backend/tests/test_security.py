import asyncio

from app.core.security import (
    hash_password,
    hash_password_async,
    verify_password,
    verify_password_async,
)


async def _ticks_while(coro_factory) -> int:  # type: ignore[no-untyped-def]
    """在 coro 执行期间，事件循环还能调度另一个协程多少次。同步阻塞时为 0。"""
    ticks = 0
    done = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)  # 让 ticker 先启动
    ticks = 0
    await coro_factory()
    done.set()
    await task
    return ticks


async def test_hashing_does_not_block_event_loop() -> None:
    hashed = hash_password("password123")

    # 对照组：在协程里直接调用同步哈希，期间事件循环一次都调度不到
    async def blocking() -> None:
        hash_password("password123")

    assert await _ticks_while(blocking) == 0

    # 线程池版本：哈希期间事件循环仍在调度其他协程
    assert await _ticks_while(lambda: hash_password_async("password123")) > 0
    assert await _ticks_while(lambda: verify_password_async("password123", hashed)) > 0
    assert await _ticks_while(lambda: verify_password_async("x", None)) > 0


async def test_async_versions_match_sync() -> None:
    hashed = await hash_password_async("password123")
    assert verify_password("password123", hashed)
    assert await verify_password_async("password123", hashed)
    assert not await verify_password_async("wrong", hashed)
    assert not await verify_password_async("password123", None)
