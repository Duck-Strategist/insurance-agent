# ruff: noqa: E501, ASYNC240  # 长行在嵌入的 JavaScript 里；检查脚本只写几张截帧
"""Load the MP4 in headless Edge, report duration/size, and save frames at given seconds as PNG."""

import asyncio
import base64
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import CDP, launch, page_ws_url  # noqa: E402

VIDEO = Path(sys.argv[1])
OUT = Path(sys.argv[2])
TIMES = [float(x) for x in sys.argv[3].split(",")]


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a: object) -> None:
        pass


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(VIDEO.parent)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    proc = launch(9335, 1280, 720)
    try:
        cdp = await CDP.connect(await page_ws_url(9335))
        await cdp.send("Page.navigate", {"url": base + "/"})
        await asyncio.sleep(1)
        info = await cdp.js(
            f"""fetch('{base}/{VIDEO.name}').then(r => r.blob()).then(b => new Promise((ok, bad) => {{
              // 整个文件读进内存再播放，等同于本地播放器（测试服务器不支持 Range，边下边播会估错时长）
              const v = document.createElement('video');
              v.muted = true; v.src = URL.createObjectURL(b);
              v.onloadedmetadata = () => {{ window.__v = v; ok(JSON.stringify({{duration: v.duration, w: v.videoWidth, h: v.videoHeight}})); }};
              v.onerror = () => bad('load error ' + (v.error && v.error.code));
              document.body.appendChild(v);
            }}))"""
        )
        print("metadata", info, flush=True)
        for t in TIMES:
            data = await cdp.js(
                f"""new Promise(ok => {{
                  const v = window.__v;
                  v.onseeked = () => {{
                    const c = document.createElement('canvas'); c.width = v.videoWidth; c.height = v.videoHeight;
                    c.getContext('2d').drawImage(v, 0, 0);
                    ok(c.toDataURL('image/jpeg', 0.8).split(',')[1]);
                  }};
                  v.currentTime = {t};
                }})"""
            )
            (OUT / f"t{int(t):03d}.jpg").write_bytes(base64.b64decode(data))
            print("saved", t, flush=True)
        await cdp.close()
    finally:
        proc.terminate()
        server.shutdown()


asyncio.run(main())
