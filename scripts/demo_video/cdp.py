# ruff: noqa: ASYNC210  # 只在启动时等 Edge 暴露调试端口，阻塞的 urllib 调用可以接受
"""Minimal Chrome DevTools Protocol client for driving a headless Edge (no extra packages)."""

import asyncio
import itertools
import json
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from typing import Any

import websockets

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def launch(port: int, width: int, height: int) -> subprocess.Popen[bytes]:
    profile = tempfile.mkdtemp(prefix="demo-edge-")
    return subprocess.Popen(
        [
            EDGE,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            f"--window-size={width},{height}",
            "--hide-scrollbars",
            # 无界面窗口会被当成后台/被遮挡，没有输入时不出新帧，录屏会停在旧画面
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--force-device-scale-factor=1",
            "--no-first-run",
            "--disable-features=Translate",
            "--autoplay-policy=no-user-gesture-required",
            "--lang=zh-CN",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def page_ws_url(port: int) -> str:
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json") as r:
                pages = [t for t in json.load(r) if t["type"] == "page"]
            if pages:
                return str(pages[0]["webSocketDebuggerUrl"])
        except OSError:
            pass
        await asyncio.sleep(0.2)
    raise RuntimeError("Edge did not expose a page target")


class CDP:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.handlers: dict[str, list[Callable[[dict[str, Any]], Any]]] = {}
        self.reader = asyncio.create_task(self._read())

    @classmethod
    async def connect(cls, url: str) -> "CDP":
        ws = await websockets.connect(url, max_size=None)
        return cls(ws)

    async def _read(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg:
                fut = self.pending.pop(msg["id"], None)
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(RuntimeError(str(msg["error"])))
                    else:
                        fut.set_result(msg.get("result", {}))
            else:
                for h in self.handlers.get(msg["method"], []):
                    r = h(msg.get("params", {}))
                    if asyncio.iscoroutine(r):
                        asyncio.create_task(r)

    def on(self, method: str, fn: Callable[[dict[str, Any]], Any]) -> None:
        self.handlers.setdefault(method, []).append(fn)

    async def send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        i = next(self.ids)
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.pending[i] = fut
        await self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        return await asyncio.wait_for(fut, 120)

    async def js(self, expr: str, await_promise: bool = True) -> Any:
        r = await self.send(
            "Runtime.evaluate",
            {"expression": expr, "awaitPromise": await_promise, "returnByValue": True},
        )
        if "exceptionDetails" in r:
            raise RuntimeError(f"JS error: {r['exceptionDetails']}")
        return r.get("result", {}).get("value")

    async def close(self) -> None:
        self.reader.cancel()
        await self.ws.close()
