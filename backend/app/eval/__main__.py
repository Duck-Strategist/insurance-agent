"""评测命令行（离线运行，会真实调用 DashScope，不进 CI）。

uv run python -m app.eval generate --per-doc 18
    按父块生成检索题 → data/eval/retrieval_generated.raw.jsonl（人工审核后另存为正式评测集）
uv run python -m app.eval retrieval
    在生成集与难题集上跑三种检索配置，写入 eval_runs，并输出 Markdown 报告到 eval/reports/

评测集读自 RAG__DATA_DIR/eval；报告默认写到仓库的 eval/reports/，
在容器里（没有仓库目录）则写到 RAG__DATA_DIR/eval/reports/，也可用 --reports-dir 指定。
"""

import argparse
import asyncio
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path, PurePath

import anyio

from app.core.config import RagSettings, get_settings
from app.core.logging import configure_logging
from app.eval import dataset as ds
from app.eval.generate import (
    GenerationAuthError,
    QuestionGenerator,
    generate_items,
    load_sections,
    sample_evenly,
)
from app.eval.metrics import longest_common_substring
from app.eval.retrieval import (
    ItemResult,
    Scope,
    document_ids_by_name,
    evaluate,
    save_runs,
)
from app.infra.db import Database
from app.rag.factory import RagNotConfiguredError, check_configured, retriever
from app.rag.retriever import SearchMode

DATASET_NAMES = ["retrieval_generated.jsonl", "retrieval_hard.jsonl"]


def eval_data_dir(rag: RagSettings) -> Path:
    return rag.data_dir / "eval"


def default_reports_dir(rag: RagSettings) -> Path:
    """本地开发时 data/ 的上一级是仓库根目录，报告写到仓库的 eval/reports/（入库）；
    容器里 RAG__DATA_DIR=/data，上一级是 /，改写到挂载卷 /data/eval/reports/，不随容器丢失。"""
    repo_eval = rag.data_dir.parent / "eval"
    return repo_eval / "reports" if repo_eval.is_dir() else eval_data_dir(rag) / "reports"


async def cmd_generate(per_doc: int, out: Path) -> int:
    settings = get_settings()
    rag = settings.rag
    check_configured(rag, need_parser=False)
    assert rag.dashscope_api_key
    db = Database(settings.db)
    generator = QuestionGenerator(
        rag.dashscope_api_key.get_secret_value(),
        base_url=rag.dashscope_base_url,
        model=rag.eval_llm_model,
    )
    try:
        sections = sample_evenly(await load_sections(db.session_factory), per_doc)
        print(f"抽样 {len(sections)} 个章节，开始生成……")
        items, dropped = await generate_items(sections, generator)
    except GenerationAuthError as exc:
        print(f"已停止：{exc}。请检查 backend/.env 中的密钥后重试。", file=sys.stderr)
        return 2
    finally:
        await generator.aclose()
        await db.dispose()
    await anyio.to_thread.run_sync(ds.save, out, items)
    print(f"生成 {len(items)} 题 → {out}；丢弃：{dropped}")
    return 0


async def cmd_retrieval(
    datasets: list[Path], modes: list[SearchMode], scope: Scope, reports_dir: Path | None
) -> int:
    settings = get_settings()
    rag = settings.rag
    if not datasets:
        datasets = [eval_data_dir(rag) / name for name in DATASET_NAMES]
    loaded = []
    for path in datasets:
        items = await anyio.to_thread.run_sync(ds.load, path)
        if not items:
            # 先检查：否则要等全部检索跑完、写进 eval_runs 后才在出报告时出错
            print(f"评测集是空的：{path}", file=sys.stderr)
            return 2
        digest = await anyio.to_thread.run_sync(ds.file_hash, path)
        loaded.append((path, items, digest))

    db = Database(settings.db)
    report_rows: list[str] = []
    try:
        doc_ids = await document_ids_by_name(db.session_factory)
        sections = {
            (s.file_name, tuple(s.path)): s.content for s in await load_sections(db.session_factory)
        }
        # 标准章节必须真实存在：手写题抄错路径、或结构规则改动后路径变了，都会让指标悄悄变低
        unknown = [
            f"{i.id}: {' > '.join(key[1])}"
            for _, items, _ in loaded
            for i in items
            for key in i.gold_keys()
            if key not in sections
        ]
        if unknown:
            print("以下标准章节在库里不存在：\n  " + "\n  ".join(unknown), file=sys.stderr)
            return 2
        async with retriever(rag, db.session_factory) as r:
            for path, items, digest in loaded:
                vectors: dict[str, list[float]] = {}
                embed_ms: dict[str, float] = {}
                for item in items:
                    t0 = time.perf_counter()
                    vectors[item.id] = await r.embed_query(item.question)
                    embed_ms[item.id] = round((time.perf_counter() - t0) * 1000, 1)
                print(f"\n{path.name}：{len(items)} 题，范围 {scope}")
                results = await evaluate(r, items, modes, scope, doc_ids, vectors, embed_ms)
                saved = await save_runs(
                    db.session_factory,
                    dataset=path.name,
                    dataset_hash=digest,
                    scope=scope,
                    settings=rag.retrieval,
                    extra_config={
                        "embedding_model": rag.embedding_model,
                        "rerank_model": rag.rerank_model,
                    },
                    results=results,
                )
                # 最长公共子串是纯 Python 的 O(n·m) 计算，放线程池
                overlaps = await anyio.to_thread.run_sync(question_overlaps, items, sections)
                report_rows += render_dataset(path.name, len(items), overlaps, results, saved)
    finally:
        await db.dispose()

    reports = reports_dir
    if reports is None:
        reports = await anyio.to_thread.run_sync(default_reports_dir, rag)
    await anyio.Path(reports).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = reports / f"retrieval-{scope}-{stamp}.md"
    header = [
        f"# 检索评测报告（{scope}）",
        "",
        f"- 时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 向量模型：{rag.embedding_model}；重排模型：{rag.rerank_model}",
        f"- 参数：{rag.retrieval.model_dump()}",
        "- 命中：检索到的前 k 个父块里有任一标准章节；延迟 = 向量化 + 检索墙钟时间（毫秒）",
        "",
    ]
    await anyio.Path(report).write_text("\n".join(header + report_rows) + "\n", encoding="utf-8")
    print(f"\n报告：{report}")
    return 0


def question_overlaps(
    items: list[ds.RetrievalItem], sections: dict[tuple[str, tuple[str, ...]], str]
) -> list[int]:
    """问题与标准章节原文的最长公共子串：数值越大，题目越像在"照着原文问"（spec Q8）。"""
    return [
        longest_common_substring(i.question, sections.get((i.file_name, tuple(i.gold[0])), ""))
        for i in items
    ]


def render_dataset(
    name: str,
    n_items: int,
    overlaps: list[int],
    results: dict[SearchMode, list[ItemResult]],
    saved: dict[SearchMode, tuple[int, dict[str, object]]],
) -> list[str]:
    overlap_line = (
        f"问题与标准章节原文的最长公共子串：中位数 {statistics.median(overlaps):.0f} 字，"
        f"平均 {statistics.mean(overlaps):.1f} 字"
        if overlaps
        else "（没有题目）"
    )
    lines = [
        f"## {name}（{n_items} 题）",
        "",
        overlap_line,
        "",
        "| 配置 | R@1 | R@3 | R@5 | R@10 | MRR@10 | 延迟 P50 | 延迟 P95 | 重排 P50 | eval_run |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for mode, (run_id, m) in saved.items():
        lat = m["latency_ms"]
        assert isinstance(lat, dict)
        rerank = lat["rerank"].get("p50", "—") if lat["rerank"] else "—"
        lines.append(
            f"| {mode.value} | {m['recall@1']:.3f} | {m['recall@3']:.3f} | {m['recall@5']:.3f} "
            f"| {m['recall@10']:.3f} | {m['mrr@10']:.3f} | {lat['total']['p50']} "
            f"| {lat['total']['p95']} | {rerank} | #{run_id} |"
        )
        print(f"  {mode.value:<14} R@5={m['recall@5']:.3f} MRR={m['mrr@10']:.3f}  run #{run_id}")

    # 最强配置下仍未进前 5 的题，供失败案例分析
    last = list(results)[-1]
    misses = [r for r in results[last] if r.rank is None or r.rank > 5]
    lines += ["", f"### {last.value} 未进前 5 的题（{len(misses)}）", ""]
    for r in misses:
        got = "；".join(" > ".join(p[-2:]) for _, p in r.retrieved[:3])
        lines.append(
            f"- `{r.item.id}` 名次 {r.rank or '>10'}：{r.item.question}  \n"
            f"  标准：{' > '.join(r.item.gold[0][-2:])}（{PurePath(r.item.file_name).stem}）  \n"
            f"  前 3：{got}"
        )
    lines.append("")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.eval")
    sub = parser.add_subparsers(dest="command", required=True)
    p_gen = sub.add_parser("generate", help="按父块生成检索评测题")
    p_gen.add_argument("--per-doc", type=int, default=18)
    p_gen.add_argument(
        "--out", type=Path, help="默认 RAG__DATA_DIR/eval/retrieval_generated.raw.jsonl"
    )
    p_ret = sub.add_parser("retrieval", help="跑检索三组对照")
    p_ret.add_argument(
        "datasets", nargs="*", type=Path, help="默认 RAG__DATA_DIR/eval 下的生成集与难题集"
    )
    p_ret.add_argument("--reports-dir", type=Path, help="报告目录，默认见模块说明")
    p_ret.add_argument("--scope", choices=["document", "global"], default="document")
    p_ret.add_argument(
        "--modes", default=",".join(m.value for m in SearchMode), help="逗号分隔的检索配置"
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log.level, settings.log.json_format)
    try:
        if args.command == "generate":
            out = args.out or eval_data_dir(settings.rag) / "retrieval_generated.raw.jsonl"
            code = asyncio.run(cmd_generate(args.per_doc, out))
        else:
            modes = [SearchMode(m) for m in args.modes.split(",")]
            code = asyncio.run(cmd_retrieval(args.datasets, modes, args.scope, args.reports_dir))
    except RagNotConfiguredError as exc:
        print(f"{exc}（写在 backend/.env）", file=sys.stderr)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
