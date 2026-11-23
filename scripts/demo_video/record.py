# ruff: noqa: E501, ASYNC109  # 长行在嵌入的 JavaScript 里；录屏脚本单线程，wait_js 的 timeout 参数是有意的
"""Record the demo (docs/DEMO.md) against the running compose stack with a headless Edge.

Output: <out>/frames/*.jpg + <out>/timeline.json (frame arrival times, scene starts, speed-up spans).
Usage: python record.py <out_dir> <username> <password>   (user must already exist and be admin)
"""

import asyncio
import base64
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from cdp import CDP, launch, page_ws_url  # noqa: E402

BASE = "http://localhost:8080"
W, H = 1280, 720
OUT = Path(sys.argv[1])
USER, PASSWORD = sys.argv[2], sys.argv[3]

# 页面里的辅助函数：按文字找元素、点击时画一个红色波纹（无界面浏览器没有鼠标指针）
HELPERS = r"""
window.__norm = s => (s || '').replace(/\s+/g, '');
// 一个几乎看不见的 1 像素动画，让合成器持续出帧（否则数据加载完成后录屏可能停在旧画面）
if (!document.getElementById('__tick')) {
  const st = document.createElement('style');
  st.textContent = '@keyframes __tick{from{opacity:.01}to{opacity:.02}}';
  document.head.appendChild(st);
  const t = document.createElement('div');
  t.id = '__tick';
  t.style.cssText = 'position:fixed;right:0;bottom:0;width:1px;height:1px;background:#000;z-index:2147483647;pointer-events:none;animation:__tick .5s infinite alternate';
  document.body.appendChild(t);
}
window.__find = (text, sel) => {
  const want = __norm(text);
  const els = [...document.querySelectorAll(sel || 'button, a, label, [role=tab], .ant-segmented-item, .ant-collapse-header, .ant-menu-item, h4, h5, span, div')];
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  return els.find(e => vis(e) && __norm(e.innerText) === want) || els.find(e => vis(e) && __norm(e.innerText).startsWith(want));
};
window.__ripple = (x, y) => {
  const d = document.createElement('div');
  d.style.cssText = `position:fixed;left:${x-18}px;top:${y-18}px;width:36px;height:36px;border-radius:50%;` +
    'border:3px solid #ff4d4f;background:rgba(255,77,79,.25);z-index:2147483647;pointer-events:none;' +
    'transition:transform .5s ease-out, opacity .5s ease-out;';
  document.body.appendChild(d);
  requestAnimationFrame(() => { d.style.transform = 'scale(1.8)'; d.style.opacity = '0'; });
  setTimeout(() => d.remove(), 700);
};
window.__click = (el) => {
  el.scrollIntoView({block: 'center', behavior: 'instant'});
  const r = el.getBoundingClientRect();
  __ripple(r.left + r.width / 2, r.top + r.height / 2);
  el.click();
  return true;
};
"""


class Recorder:
    def __init__(self, cdp: CDP) -> None:
        self.cdp = cdp
        self.frames: list[tuple[float, str]] = []
        self.events: list[dict[str, Any]] = []
        (OUT / "frames").mkdir(parents=True, exist_ok=True)
        cdp.on("Page.screencastFrame", self._frame)

    async def _frame(self, p: dict[str, Any]) -> None:
        t = time.time()
        name = f"{len(self.frames):06d}.jpg"
        (OUT / "frames" / name).write_bytes(base64.b64decode(p["data"]))
        self.frames.append((t, name))
        await self.cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})

    def mark(self, kind: str, **kw: Any) -> None:
        self.events.append({"t": time.time(), "kind": kind, **kw})
        print(time.strftime("%H:%M:%S"), kind, kw.get("title", ""), flush=True)

    def save(self) -> None:
        (OUT / "timeline.json").write_text(
            json.dumps({"frames": self.frames, "events": self.events}, ensure_ascii=False), "utf-8"
        )


async def helpers(cdp: CDP) -> None:
    await cdp.js(HELPERS)


async def goto(cdp: CDP, path: str, settle: float = 2.0) -> None:
    await cdp.send("Page.navigate", {"url": BASE + path})
    await asyncio.sleep(settle)
    await helpers(cdp)


async def wait_js(cdp: CDP, expr: str, timeout: float = 90, every: float = 0.25) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if await cdp.js(expr):
                return
        except RuntimeError:
            pass
        await asyncio.sleep(every)
    raise TimeoutError(expr)


async def click(cdp: CDP, text: str, sel: str | None = None, pause: float = 0.8) -> None:
    sel_js = json.dumps(sel) if sel else "undefined"
    await wait_js(cdp, f"Boolean(__find({json.dumps(text)}, {sel_js}))", timeout=30)
    await cdp.js(f"__click(__find({json.dumps(text)}, {sel_js}))")
    await asyncio.sleep(pause)


async def type_text(cdp: CDP, focus_expr: str, text: str, delay: float = 0.07) -> None:
    await cdp.js(
        f"(() => {{ const el = {focus_expr}; el.scrollIntoView({{block:'center'}}); el.focus(); return true }})()"
    )
    for ch in text:
        await cdp.send("Input.insertText", {"text": ch})
        await asyncio.sleep(delay)


async def press_enter(cdp: CDP) -> None:
    for t in ("keyDown", "keyUp"):
        await cdp.send(
            "Input.dispatchKeyEvent",
            {
                "type": t,
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "text": "\r" if t == "keyDown" else "",
            },
        )


CHAT_BOX = "document.querySelector('textarea')"
# 检索调试页：结果卡片出现、骨架屏消失
RESULTS = "(document.querySelectorAll('main .ant-card').length > 0 && !document.querySelector('main .ant-skeleton'))"
CHAT_TEXT = "(document.querySelector('main')?.innerText.length || 0)"


async def ask(cdp: CDP, rec: Recorder, question: str) -> None:
    """Type a chat question, speed up the wait before the first token, wait for the answer to finish."""
    await type_text(cdp, CHAT_BOX, question)
    await asyncio.sleep(0.5)
    base = await cdp.js(CHAT_TEXT)
    await press_enter(cdp)
    rec.mark("speed_start")
    await wait_js(cdp, f"{CHAT_BOX}.disabled", timeout=10)
    # 首个 token 之前（路由、检索、重排）加速；状态提示只有十几个字，正文出来后长度会明显增加
    await wait_js(cdp, f"{CHAT_TEXT} > {base} + {len(question)} + 40", timeout=60)
    rec.mark("speed_end")
    await wait_js(cdp, f"!{CHAT_BOX}.disabled", timeout=120)
    await asyncio.sleep(1.0)


async def scroll_to(cdp: CDP, text: str, block: str = "start", pause: float = 1.5) -> None:
    await wait_js(cdp, f"Boolean(__find({json.dumps(text)}))", timeout=20)
    await cdp.js(
        f"__find({json.dumps(text)}).scrollIntoView({{block: '{block}', behavior: 'smooth'}})"
    )
    await asyncio.sleep(pause)


async def focus_label(cdp: CDP, label: str) -> str:
    """Return a JS expression for the input belonging to an antd form label."""
    return (
        f"(() => {{ const l = [...document.querySelectorAll('label')].find(e => __norm(e.innerText) === __norm({json.dumps(label)}));"
        " return document.getElementById(l.htmlFor) })()"
    )


async def scenes(cdp: CDP, rec: Recorder) -> None:
    # 1 产品
    await goto(cdp, "/")
    rec.mark("scene", title="1 / 8  保险产品")
    await asyncio.sleep(2.5)
    await click(cdp, "保险产品", "a, .ant-menu-item, span")
    await asyncio.sleep(2.5)
    await cdp.js("window.scrollBy({top: 650, behavior: 'smooth'})")
    await asyncio.sleep(3)

    # 2 条款问答
    await goto(cdp, "/chat")
    rec.mark("scene", title="2 / 8  条款问答")
    await asyncio.sleep(1.5)
    await ask(cdp, rec, "众安尊享e生2026版的等待期多久？等待期内确诊会怎样？")
    await asyncio.sleep(3)
    await click(cdp, "1众安尊享e生百万医疗险2026版条款", ".ant-collapse-header", pause=1.0)
    await asyncio.sleep(5)

    # 3 顾问
    rec.mark("scene", title="3 / 8  顾问配方案")
    await click(cdp, "新对话", "button")
    await ask(cdp, rec, "我 30 岁，男，有社保，预算每年 5000，帮我配一份保险")
    await scroll_to(cdp, "保障方案", "start", pause=2)
    await asyncio.sleep(6)

    # 4 画像 + 确认
    rec.mark("scene", title="4 / 8  画像建议与方案确认")
    await scroll_to(cdp, "保存到画像？", "center", pause=2.5)
    await click(cdp, "保存到画像", "button", pause=2.5)
    await scroll_to(cdp, "确认方案", "center", pause=1.5)
    await click(cdp, "确认方案", "button", pause=0.5)
    rec.mark("speed_start")
    await wait_js(cdp, f"!{CHAT_BOX}.disabled", timeout=60)
    rec.mark("speed_end")
    await asyncio.sleep(1)
    await cdp.js("document.querySelector('textarea').scrollIntoView({block:'end'})")
    await asyncio.sleep(3.5)

    # 5 理赔
    rec.mark("scene", title="5 / 8  理赔咨询")
    await click(cdp, "新对话", "button")
    await ask(cdp, rec, "我去年 3 月给自己买了星相守2号，现在因为胆结石要住院做手术，能赔吗？")
    await asyncio.sleep(1)
    await scroll_to(
        cdp,
        "我去年 3 月给自己买了星相守2号，现在因为胆结石要住院做手术，能赔吗？",
        "start",
        pause=2,
    )
    await asyncio.sleep(6)

    # 6 报价
    await goto(cdp, "/products/2")
    rec.mark("scene", title="6 / 8  按费率表报价")
    await asyncio.sleep(2)
    await scroll_to(cdp, "在线报价", "start", pause=1.5)
    await type_text(cdp, await focus_label(cdp, "被保险人年龄"), "30", delay=0.15)
    await click(cdp, "男", "label", pause=0.6)
    await click(cdp, "20年交", "label", pause=0.6)
    await type_text(cdp, await focus_label(cdp, "基本保额（元）"), "300000", delay=0.1)
    await click(cdp, "终身", "label", pause=0.6)
    await click(cdp, "计算保费", "button", pause=2)
    await scroll_to(cdp, "计算明细", "center", pause=2)
    await asyncio.sleep(5)

    # 7 检索调试 + 条款管理
    await goto(cdp, "/admin/search")
    rec.mark("scene", title="7 / 8  检索调试与条款管理")
    await asyncio.sleep(1)
    await type_text(cdp, "document.querySelector('main input')", "之前就有的病能报吗")
    await press_enter(cdp)
    rec.mark("speed_start")
    await wait_js(cdp, RESULTS, timeout=60)
    rec.mark("speed_end")
    await asyncio.sleep(5)
    await click(cdp, "纯向量", ".ant-segmented-item", pause=0.3)
    rec.mark("speed_start")
    await wait_js(cdp, f"!{RESULTS}", timeout=10)
    await wait_js(cdp, RESULTS, timeout=60)
    rec.mark("speed_end")
    await asyncio.sleep(4)
    await click(cdp, "混合+重排", ".ant-segmented-item", pause=0.3)
    await wait_js(cdp, RESULTS, timeout=60)
    await asyncio.sleep(3)
    await goto(cdp, "/admin/documents", settle=1)
    await wait_js(cdp, "document.querySelectorAll('main tbody tr').length > 3", timeout=30)
    await asyncio.sleep(4)

    # 8 评测记录
    await goto(cdp, "/admin/evals", settle=1)
    rec.mark("scene", title="8 / 8  评测记录")
    await wait_js(cdp, "document.querySelectorAll('main tbody tr').length > 3", timeout=30)
    await asyncio.sleep(3.5)
    await click(cdp, "路由", "[role=tab]", pause=3.5)
    await click(cdp, "端到端", "[role=tab]", pause=4)
    rec.mark("end")


def api_login() -> dict[str, Any]:
    req = urllib.request.Request(
        BASE.replace("8080", "8000") + "/api/v1/auth/login",
        data=json.dumps({"username": USER, "password": PASSWORD}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return dict(json.load(r))


async def main() -> None:
    proc = launch(9334, W, H)
    try:
        cdp = await CDP.connect(await page_ws_url(9334))
        await cdp.send("Page.enable")
        await cdp.send("Runtime.enable")
        await cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        await cdp.send("Page.bringToFront")
        await cdp.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": W, "height": H, "deviceScaleFactor": 1, "mobile": False},
        )
        tok = api_login()
        await cdp.send("Page.navigate", {"url": BASE + "/"})
        await asyncio.sleep(1.5)
        state = {"state": {"token": tok["access_token"], "user": tok["user"]}, "version": 0}
        await cdp.js(
            f"localStorage.setItem('insurance-agent-auth', {json.dumps(json.dumps(state))}); true"
        )
        rec = Recorder(cdp)
        await cdp.send(
            "Page.startScreencast", {"format": "jpeg", "quality": 85, "maxWidth": W, "maxHeight": H}
        )
        try:
            await scenes(cdp, rec)
        finally:
            await cdp.send("Page.stopScreencast")
            await asyncio.sleep(0.5)
            rec.save()
            print("frames", len(rec.frames), flush=True)
        await cdp.close()
    finally:
        proc.terminate()


asyncio.run(main())
