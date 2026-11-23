"""High-resolution README screenshots (docs/images/*.jpg): 1280x800 viewport at 2x pixel density.

Usage: python screenshots.py <out_dir> <username> <password>
The user must exist and be admin (see reset_user.py).
"""

import asyncio
import base64
import io
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from record import (  # noqa: E402
    RESULTS,
    Marks,
    ask,
    click,
    focus_label,
    goto,
    open_session,
    press_enter,
    scroll_to,
    type_text,
    wait_js,
)

W, H, SCALE = 1280, 800, 2


async def shot(cdp, out: Path, name: str) -> None:  # type: ignore[no-untyped-def]
    await asyncio.sleep(1.2)  # 等点击波纹、动画消失
    r = await cdp.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
    # 4:4:4 不做色度抽样，文字边缘不发糊
    img.save(out / f"{name}.jpg", quality=92, subsampling=0, optimize=True)
    print("saved", name, img.size, flush=True)


CLAUSE_Q = "众安尊享e生2026版的等待期多久？等待期内确诊会怎样？"
SEARCH_BOX = "document.querySelector('main input')"


async def main(out: Path, user: str, password: str) -> None:
    proc, cdp = await open_session(9337, user, password, W, H, SCALE)
    marks = Marks()
    try:
        # 条款问答：展开第 1 条引用
        await goto(cdp, "/chat")
        await ask(cdp, marks, CLAUSE_Q)
        await click(cdp, "1众安尊享e生百万医疗险2026版条款", ".ant-collapse-header", pause=0.8)
        await scroll_to(cdp, CLAUSE_Q, "start", pause=1)
        await shot(cdp, out, "chat-clause")

        # 顾问：方案卡片；画像建议
        await click(cdp, "新对话", "button")
        advisor_q = "我 30 岁，男，有社保，预算每年 5000，帮我配一份保险"
        await ask(cdp, marks, advisor_q)
        # 回答结束后页面会重新拉取历史消息并自动滚到底部，等它完成再滚动，否则会被覆盖
        await asyncio.sleep(3)
        # 顾问：方案卡片顶到上方后，对话区再往上移，露出模型写的说明。
        # 两步都用瞬时滚动：平滑滚动进行中再调 scrollBy 会被打断，位置不确定
        await cdp.js(
            "(() => { const card = __find('保障方案'); card.scrollIntoView({block: 'start'});"
            " let p = card.parentElement;"
            " while (p && p.scrollHeight <= p.clientHeight) p = p.parentElement;"
            " p.scrollBy(0, -200); return true })()"
        )
        await shot(cdp, out, "chat-advisor")
        # 画像：方案卡片放到顶部，下面是展开的画像建议
        await scroll_to(cdp, "保障方案", "start", pause=1.5)
        await shot(cdp, out, "chat-profile")

        # 理赔
        await click(cdp, "新对话", "button")
        q = "我去年 3 月给自己买了星相守2号，现在因为胆结石要住院做手术，能赔吗？"
        await ask(cdp, marks, q)
        await scroll_to(cdp, q, "start", pause=1)
        await shot(cdp, out, "chat-claim")

        # 报价
        await goto(cdp, "/products/2")
        await scroll_to(cdp, "在线报价", "start", pause=0.5)
        await type_text(cdp, await focus_label(cdp, "被保险人年龄"), "30", delay=0.02)
        await click(cdp, "男", "label", pause=0.3)
        await click(cdp, "20年交", "label", pause=0.3)
        await type_text(cdp, await focus_label(cdp, "基本保额（元）"), "300000", delay=0.02)
        await click(cdp, "终身", "label", pause=0.3)
        await click(cdp, "计算保费", "button", pause=1.5)
        await scroll_to(cdp, "计算明细", "center", pause=1)
        await shot(cdp, out, "quote")

        # 检索调试
        await goto(cdp, "/admin/search")
        await type_text(cdp, SEARCH_BOX, "之前就有的病能报吗", delay=0.02)
        await press_enter(cdp)
        await wait_js(cdp, RESULTS, timeout=60)
        await shot(cdp, out, "admin-search")

        # 条款管理、评测记录
        await goto(cdp, "/admin/documents", settle=1)
        await wait_js(cdp, "document.querySelectorAll('main tbody tr').length > 3", timeout=30)
        await shot(cdp, out, "admin-documents")
        await goto(cdp, "/admin/evals", settle=1)
        await wait_js(cdp, "document.querySelectorAll('main tbody tr').length > 3", timeout=30)
        await shot(cdp, out, "admin-evals")
        await cdp.close()
    finally:
        proc.terminate()


if __name__ == "__main__":
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(out_dir, sys.argv[2], sys.argv[3]))
