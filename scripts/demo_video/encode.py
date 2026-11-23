# ruff: noqa: E501  # 长行都在嵌入的 JavaScript 和字幕文字里
"""Turn a recording (frames + timeline.json) into a captioned MP4 using a headless Edge's MediaRecorder.

Usage: python encode.py <run_dir> <out_file>
"""

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))
from cdp import EDGE  # noqa: E402

RUN = Path(sys.argv[1])
OUT = Path(sys.argv[2])
SPEED = 5.0  # 首 token 之前的等待加速倍数
MAX_HOLD = 5.0  # 画面静止最长保留秒数
INTRO, OUTRO = 4.5, 7.0

CAPTIONS = {
    "1": "12 个真实保险产品，其中 10 个有费率表。核心原则：LLM 只做理解与表达，产品、条款、保费、日期这些数字和事实都由代码负责。",
    "2": "条款问答：代码先定位这款产品的条款再混合检索 + 重排；回答里的 [n] 由代码校验，只保留本轮真实检索到的段落，点开就是条款原文。",
    "3": "顾问：险种、产品、保额由规则选，保费由报价引擎按费率表计算（每行注明费率表第几页），LLM 只写说明。方案卡片是 LangGraph 的 interrupt，等用户确认。",
    "4": "画像抽取与路由并行，抽到的信息只是建议，用户点「保存到画像」才写库；确认方案后，对话从中断处恢复，方案写入「我的方案」。",
    "5": "理赔咨询：投保日期、是否已过等待期由代码算好写进提示词，不让模型推算；同时检索保险责任、责任免除、等待期、理赔材料四处条款。",
    "6": "同一个报价引擎：保费由纯函数按费率表计算，明细写到费率表第几页、哪一格；报价绑定费率表版本，以后能按当时的版本重算。",
    "7": "检索调试：口语「之前就有的病」与条款术语有鸿沟，重排把「既往症」释义排到第 1；顶部是向量化、召回、重排的耗时拆分。条款管理页是每份条款的结构识别统计。",
    "8": "每个数字都有评测记录：检索难题集 R@5 0.941，路由测试集准确率 0.956，端到端人工抽检正确性 0.917，无答案题拒答率 100%。",
}
CARDS = [
    {
        "title": "安心保 · 智能保险顾问",
        "lines": [
            "RAG + LangGraph 的保险顾问 Agent",
            "条款问答 · 顾问配方案 · 按费率表报价 · 理赔咨询",
        ],
    },
    {
        "title": "LLM 只做理解与表达",
        "lines": [
            "检索难题集 R@5 0.941 · 路由准确率 0.956",
            "端到端人工抽检正确性 0.917 · 无答案题拒答 100%",
            "报价对照 PDF 人工读出的 107 个抽样点全部一致（单测）",
            "评测数字均可在 eval_runs 查到对应记录",
        ],
    },
]


def build_manifest() -> dict:
    tl = json.loads((RUN / "timeline.json").read_text("utf-8"))
    frames: list[tuple[float, str]] = [tuple(f) for f in tl["frames"]]
    events = tl["events"]
    spans, start = [], None
    for e in events:
        if e["kind"] == "speed_start":
            start = e["t"]
        elif e["kind"] == "speed_end" and start is not None:
            spans.append((start, e["t"]))
            start = None
    end_t = next((e["t"] for e in events if e["kind"] == "end"), frames[-1][0]) + 2.0

    def factor(t: float) -> float:
        return SPEED if any(a <= t < b for a, b in spans) else 1.0

    entries = [{"at": 0, "card": 0}]
    vt = INTRO
    real_to_video: list[tuple[float, float]] = []
    for idx, (t, name) in enumerate(frames):
        entries.append({"at": round(vt * 1000), "frame": name})
        real_to_video.append((t, vt))
        nxt = frames[idx + 1][0] if idx + 1 < len(frames) else end_t
        vt += min((nxt - t) / factor(t), MAX_HOLD)
    entries.append({"at": round(vt * 1000), "card": 1})
    total = vt + OUTRO

    def video_at(t: float) -> float:
        best = INTRO
        for rt, v in real_to_video:
            if rt <= t:
                best = v
            else:
                break
        return best

    scenes = []
    for e in events:
        if e["kind"] == "scene":
            num = e["title"].split()[0]
            scenes.append(
                {
                    "at": round(video_at(e["t"]) * 1000),
                    "title": e["title"],
                    "caption": CAPTIONS[num],
                }
            )
    # 片头、片尾是卡片，不画字幕；场景只在录屏帧上叠加
    return {"entries": entries, "scenes": scenes, "cards": CARDS, "total": round(total * 1000)}


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a: object) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        q = parse_qs(urlparse(self.path).query)
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if "error" in q:
            (RUN / "encode_error.txt").write_text(q["error"][0], "utf-8")
        else:
            OUT.write_bytes(body)
            (RUN / "encode_type.txt").write_text(q.get("type", [""])[0], "utf-8")
        self.send_response(200)
        self.end_headers()


def main() -> None:
    manifest = build_manifest()
    (RUN / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
    shutil.copy(Path(__file__).parent / "encode.html", RUN / "encode.html")
    print(
        f"video length {manifest['total'] / 1000:.1f}s, frames {len(manifest['entries']) - 2}",
        flush=True,
    )
    for name in ("encode_error.txt",):
        (RUN / name).unlink(missing_ok=True)
    OUT.unlink(missing_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(RUN)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/encode.html"
    edge = subprocess.Popen(
        [
            EDGE,
            "--headless=new",
            f"--user-data-dir={tempfile.mkdtemp(prefix='demo-enc-')}",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--window-size=1280,840",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + manifest["total"] / 1000 + 300
    try:
        while time.time() < deadline:
            if (RUN / "encode_error.txt").exists():
                raise SystemExit("encode error: " + (RUN / "encode_error.txt").read_text("utf-8"))
            if OUT.exists() and OUT.stat().st_size > 0:
                time.sleep(1)
                print(
                    "written",
                    OUT,
                    OUT.stat().st_size,
                    (RUN / "encode_type.txt").read_text("utf-8"),
                    flush=True,
                )
                return
            time.sleep(2)
        raise SystemExit("encode timed out")
    finally:
        edge.terminate()
        server.shutdown()


main()
