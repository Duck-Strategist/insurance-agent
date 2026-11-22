"""评测命令行（离线运行，会真实调用 DashScope，不进 CI）。

uv run python -m app.eval generate --per-doc 18
    按父块生成检索题 → data/eval/retrieval_generated.raw.jsonl（人工审核后另存为正式评测集）
uv run python -m app.eval retrieval
    在生成集与难题集上跑三种检索配置，写入 eval_runs，并输出 Markdown 报告到 eval/reports/
uv run python -m app.eval routing [routing_dev.jsonl]
    路由评测（默认测试集 routing_test.jsonl；调提示词时只看开发集，spec Q16）
uv run python -m app.eval e2e [--judge-model qwen-max]
    端到端评测：条款问答 + LLM 裁判，写入 eval_runs，另导出 20% 抽样 CSV 供人工标注
uv run python -m app.eval rejudge 147 --exclude ../eval/reports/e2e-147-human-labeled.csv
    用当前版本的裁判重判某次运行里已有的回答（回答不重新生成），写一条新的 eval_runs；
    --exclude 里的题（调裁判时看过的）不进新的人工抽检样本
uv run python -m app.eval agreement eval/reports/e2e-<run>-human.csv [--run N] [--key human]
    人工标注后计算裁判与人工的一致率；--run 指定和哪次运行的裁判比（默认文件里的 run_id）

评测集读自 RAG__DATA_DIR/eval；报告默认写到仓库的 eval/reports/，
在容器里（没有仓库目录）则写到 RAG__DATA_DIR/eval/reports/，也可用 --reports-dir 指定。
"""

import argparse
import asyncio
import functools
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

import anyio
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agent.graph import answer_from_clauses, load_catalog, rewrite_query
from app.agent.llm import make_llms, parse_json_object
from app.agent.prompts import NOT_FOUND
from app.core.config import RagSettings, Settings, get_settings
from app.core.logging import configure_logging
from app.eval import dataset as ds
from app.eval import e2e, routing
from app.eval.generate import (
    GenerationAuthError,
    QuestionGenerator,
    evidence_in_section,
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
    product_names_by_file,
    save_runs,
)
from app.eval.retry import with_retries
from app.infra.db import Database
from app.infra.tracing import flush_traces, init_tracing, trace_config
from app.models import EvalRun
from app.rag.factory import RagNotConfiguredError, check_configured, retriever
from app.rag.retriever import SearchMode

DATASET_NAMES = ["retrieval_generated.jsonl", "retrieval_hard.jsonl"]
ROUTING_TEST = "routing_test.jsonl"
E2E_DATASET = "e2e_clause.jsonl"


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


async def rewrite_all(
    items: list[ds.RetrievalItem], names: dict[str, list[str]], concurrency: int = 5
) -> tuple[dict[str, str], str]:
    """用线上同一个改写函数和模型改写每道题，返回 (题目 id → 查询, 模型名)。"""
    settings = get_settings()
    json_llm, _ = make_llms(settings.llm, settings.llm_api_key())
    sem = asyncio.Semaphore(concurrency)
    queries: dict[str, str] = {}

    async def one(item: ds.RetrievalItem) -> None:
        async with sem:
            products = names.get(item.file_name) or [PurePath(item.file_name).stem]
            queries[item.id] = await rewrite_query(json_llm, item.question, products=products)

    await asyncio.gather(*(one(i) for i in items))
    return queries, settings.llm.model


async def cmd_retrieval(
    datasets: list[Path],
    modes: list[SearchMode],
    scope: Scope,
    reports_dir: Path | None,
    rewrite: bool = False,
    keep_original: bool = False,
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
        # 路径还在、但原文已经挪到别的章节（结构规则改动后常见，spec Q13）：生成题按 evidence 核对
        stale = [
            i.id
            for _, items, _ in loaded
            for i in items
            if i.evidence
            and not any(evidence_in_section(i.evidence, sections[k]) for k in i.gold_keys())
        ]
        if stale:
            print(
                "以下题目的原文依据已不在标准章节里，请重新标注：" + "、".join(stale),
                file=sys.stderr,
            )
            return 2
        async with retriever(rag, db.session_factory) as r:
            names = await product_names_by_file(db.session_factory) if rewrite else {}
            for path, items, digest in loaded:
                queries: dict[str, str] = {i.id: i.question for i in items}
                extra: dict[str, object] = {}
                if rewrite:
                    print(f"{path.name}：改写查询……")
                    queries, llm_model = await rewrite_all(items, names)
                    extra = {"rewrite": True, "rewrite_model": llm_model}
                    if keep_original:
                        extra["keep_original"] = True
                vectors: dict[str, list[float]] = {}
                alt_vectors: dict[str, list[float]] | None = (
                    {} if rewrite and keep_original else None
                )
                embed_ms: dict[str, float] = {}
                for item in items:
                    t0 = time.perf_counter()
                    texts = [queries[item.id]]
                    if alt_vectors is not None:
                        texts.append(item.question)  # 与线上一样，一次请求算两条
                    got = await r.embed_queries(texts)
                    vectors[item.id] = got[0]
                    if alt_vectors is not None:
                        alt_vectors[item.id] = got[1]
                    embed_ms[item.id] = round((time.perf_counter() - t0) * 1000, 1)
                print(f"\n{path.name}：{len(items)} 题，范围 {scope}")
                results = await evaluate(
                    r, items, modes, scope, doc_ids, vectors, embed_ms, queries, alt_vectors
                )
                saved = await save_runs(
                    db.session_factory,
                    dataset=path.name,
                    dataset_hash=digest,
                    scope=scope,
                    settings=rag.retrieval,
                    extra_config={
                        "embedding_model": rag.embedding_model,
                        "rerank_model": rag.rerank_model,
                        **extra,
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


async def write_report(rag: RagSettings, reports_dir: Path | None, name: str, text: str) -> Path:
    reports = reports_dir
    if reports is None:
        reports = await anyio.to_thread.run_sync(default_reports_dir, rag)
    await anyio.Path(reports).mkdir(parents=True, exist_ok=True)
    path = reports / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.md"
    await anyio.Path(path).write_text(text, encoding="utf-8")
    return path


def eval_trace_config(settings: Settings, suite: str, dataset: str) -> RunnableConfig:
    """评测也上报 Langfuse（配置了才启用），标签 eval + 套件名，和线上 trace 分开筛选。"""
    init_tracing(settings.langfuse, environment="eval")
    return trace_config(f"eval-{suite}", tags=["eval", suite, dataset])


async def cmd_routing(datasets: list[Path], reports_dir: Path | None) -> int:
    settings = get_settings()
    if not datasets:
        datasets = [eval_data_dir(settings.rag) / ROUTING_TEST]
    json_llm, _ = make_llms(settings.llm, settings.llm_api_key())
    db = Database(settings.db)
    lines = [
        "# 路由评测报告",
        "",
        f"- 时间：{datetime.now():%Y-%m-%d %H:%M}；模型：{settings.llm.model}（JSON 模式，温度 0）",
        "- 用线上同一个路由函数（寒暄规则 + 模型 + 产品 id 校验 + 追问沿用产品）",
        "",
    ]
    try:
        catalog = await load_catalog(db.session_factory)
        for path in datasets:
            items = await anyio.to_thread.run_sync(routing.load_items, path)
            unknown = routing.unknown_products(items, catalog)
            if unknown:
                print("以下产品名不在产品目录里：\n  " + "\n  ".join(unknown), file=sys.stderr)
                return 2
            digest = await anyio.to_thread.run_sync(ds.file_hash, path)
            print(f"{path.name}：{len(items)} 题……")
            results = await routing.run_items(
                json_llm,
                catalog,
                items,
                history_turns=settings.llm.history_turns,
                config=eval_trace_config(settings, "routing", path.name),
            )
            run_id, m = await routing.save_run(
                db.session_factory,
                dataset=path.name,
                dataset_hash=digest,
                config={"model": settings.llm.model, "history_turns": settings.llm.history_turns},
                results=results,
            )
            print(
                f"  准确率 {m['accuracy']:.3f}  宏 F1 {m['macro_f1']:.3f}  "
                f"多轮 {m['multi_turn']['accuracy']:.3f}  产品 {m['product_accuracy']:.3f}"
                f"  run #{run_id}"
            )
            lines += routing.render(path.name, run_id, m, results)
    finally:
        await db.dispose()
        flush_traces()
    report = await write_report(settings.rag, reports_dir, "routing", "\n".join(lines) + "\n")
    print(f"\n报告：{report}")
    return 0


async def judge_one(
    judge_llm: BaseChatModel,
    item: e2e.E2EItem,
    answer: str,
    cited: list[int],
    passages: dict[int, tuple[list[str], str]],
    gold_text: str,
    config: RunnableConfig,
) -> e2e.Judgement:
    """passages：回答模型看到的全部段落（编号 → (章节路径, 原文)），编号与回答里的 [n] 一致。"""
    prompt = e2e.JUDGE_USER.format(
        question=item.question,
        reference_block=e2e.reference_block(item, gold_text),
        passages=e2e.passages_block(passages),
        answer=answer,
    )
    resp = await judge_llm.ainvoke(
        [SystemMessage(content=e2e.JUDGE_SYSTEM), HumanMessage(content=prompt)], config=config
    )
    return e2e.parse_judgement(parse_json_object(str(resp.content)), cited, passages)


Sections = dict[tuple[str, tuple[str, ...]], str]


async def e2e_context(
    db: Database, items: list[e2e.E2EItem]
) -> tuple[dict[str, int], Sections, dict[str, str]] | None:
    """条款 id、全部章节原文、每题的标准章节原文；评测集与库对不上时打印原因并返回 None。"""
    doc_ids = await document_ids_by_name(db.session_factory)
    sections = {
        (s.file_name, tuple(s.path)): s.content for s in await load_sections(db.session_factory)
    }
    missing = [
        f"{i.id}: {' > '.join(p)}"
        for i in items
        for p in i.gold
        if (i.file_name, tuple(p)) not in sections
    ] + [f"{i.id}: 条款未入库 {i.file_name}" for i in items if i.file_name not in doc_ids]
    if missing:
        print("评测集与库不一致：\n  " + "\n  ".join(missing), file=sys.stderr)
        return None
    references = {
        i.id: "\n\n".join(sections[(i.file_name, tuple(p))] for p in i.gold) for i in items
    }
    return doc_ids, sections, references


async def save_e2e(
    db: Database,
    *,
    dataset: str,
    digest: str,
    config: dict[str, object],
    results: list[e2e.E2EResult],
) -> tuple[int, dict[str, Any]]:
    metrics = e2e.summarize(results)
    async with db.session_factory() as session, session.begin():
        run = EvalRun(
            suite="e2e",
            dataset=dataset,
            dataset_hash=digest,
            config=config,
            metrics=metrics,
            details=[x.as_detail() for x in results],
        )
        session.add(run)
        await session.flush()
        return run.id, metrics


async def report_e2e(
    rag: RagSettings,
    reports_dir: Path | None,
    *,
    dataset: str,
    run_id: int,
    metrics: dict[str, Any],
    results: list[e2e.E2EResult],
    references: dict[str, str],
    exclude: frozenset[str] = frozenset(),
    blind: bool = False,
) -> None:
    reports: Path = (
        reports_dir
        if reports_dir is not None
        else await anyio.to_thread.run_sync(default_reports_dir, rag)
    )
    await anyio.Path(reports).mkdir(parents=True, exist_ok=True)
    sample = e2e.sample_for_human(results, exclude=exclude)
    human_csv = reports / f"e2e-{run_id}-human.csv"
    await anyio.to_thread.run_sync(
        functools.partial(e2e.write_human_csv, blind=blind),
        human_csv,
        run_id,
        sample,
        references,
    )
    report = await write_report(
        rag, reports, "e2e", "\n".join(render_e2e(dataset, run_id, metrics, results)) + "\n"
    )
    a, u = metrics["answerable"], metrics["unanswerable"]
    print(
        f"\n正确性 {a['correctness']}（correct {a['correct_rate']}）  "
        f"引用忠实度 {metrics['citation_precision']}  无答案题拒答率 {u['refusal_rate']}  "
        f"有答案题误拒率 {a['false_refusal_rate']}  run #{run_id}"
    )
    print(f"报告：{report}\n人工抽检（{len(sample)} 题）：{human_csv}")


async def cmd_e2e(
    dataset: Path | None, judge_model: str, reports_dir: Path | None, concurrency: int
) -> int:
    settings = get_settings()
    rag = settings.rag
    path = dataset or eval_data_dir(rag) / E2E_DATASET
    items = await anyio.to_thread.run_sync(e2e_load, path)
    digest = await anyio.to_thread.run_sync(ds.file_hash, path)
    key = settings.llm_api_key()
    _, answer_llm = make_llms(settings.llm, key)
    if judge_model == settings.llm.model:
        print("裁判模型和回答模型相同，会有自评偏差；建议用 --judge-model 换一个", file=sys.stderr)
    judge_llm, _ = make_llms(settings.llm.model_copy(update={"model": judge_model}), key)
    trace = eval_trace_config(settings, "e2e", path.name)
    db = Database(settings.db)
    try:
        ctx = await e2e_context(db, items)
        if ctx is None:
            return 2
        doc_ids, _, references = ctx
        sem = asyncio.Semaphore(concurrency)
        async with retriever(rag, db.session_factory) as r:

            async def attempt(item: e2e.E2EItem) -> e2e.E2EResult:
                t0 = time.perf_counter()
                # 第一句话：不改写，直接用原问题检索（与线上一致，spec D39）
                got = await answer_from_clauses(
                    r,
                    answer_llm,
                    item.question,
                    item.question,
                    [doc_ids[item.file_name]],
                    settings.llm.context_chars_per_section,
                    config=trace,
                )
                latency = round((time.perf_counter() - t0) * 1000, 1)
                answer = got.content if got else NOT_FOUND
                citations = got.citations if got else []
                passages = {
                    i: (h.section_path, h.parent_content)
                    for i, h in enumerate(got.hits if got else [], start=1)
                }
                judgement = await judge_one(
                    judge_llm,
                    item,
                    answer,
                    [int(str(c["index"])) for c in citations],
                    passages,
                    references[item.id],
                    trace,
                )
                flag = "（拒答）" if judgement.refused else ""
                print(f"  {item.id}: {judgement.correctness}{flag}")
                return e2e.E2EResult(
                    item,
                    answer,
                    citations,
                    judgement,
                    latency,
                    got is None,
                    extra={"context": [{"index": i, "path": p} for i, (p, _) in passages.items()]},
                )

            async def one(item: e2e.E2EItem) -> e2e.E2EResult:
                async with sem:
                    return await with_retries(lambda: attempt(item), label=item.id)

            print(f"{path.name}：{len(items)} 题，回答 {settings.llm.model}，裁判 {judge_model}")
            results = list(await asyncio.gather(*(one(i) for i in items)))
        run_id, metrics = await save_e2e(
            db,
            dataset=path.name,
            digest=digest,
            config={
                "answer_model": settings.llm.model,
                "judge_model": judge_model,
                "judge_version": e2e.JUDGE_VERSION,
                "mode": "hybrid_rerank",
                "rerank_model": rag.rerank_model,
                "context_chars_per_section": settings.llm.context_chars_per_section,
            },
            results=results,
        )
    finally:
        await db.dispose()
        flush_traces()
    await report_e2e(
        rag,
        reports_dir,
        dataset=path.name,
        run_id=run_id,
        metrics=metrics,
        results=results,
        references=references,
    )
    return 0


async def cmd_rejudge(
    source_id: int,
    judge_model: str,
    reports_dir: Path | None,
    exclude_csv: Path | None,
    concurrency: int,
) -> int:
    """用当前版本的裁判重判一次运行里的回答：回答不重新生成，只换裁判，前后两版的差别只来自裁判。

    v1 的运行没有记下回答模型看到的全部段落，按原问题重新检索还原（检索是确定的，重排模型相同）；
    被引用的编号以当时记下的章节为准，和重新检索的结果对不上的计数写进 config.context_mismatch。"""
    settings = get_settings()
    rag = settings.rag
    exclude: frozenset[str] = frozenset()
    if exclude_csv is not None:
        _, rows = await anyio.to_thread.run_sync(e2e.read_human_csv, exclude_csv)
        exclude = frozenset(r["id"] for r in rows)
    judge_llm, _ = make_llms(
        settings.llm.model_copy(update={"model": judge_model}), settings.llm_api_key()
    )
    db = Database(settings.db)
    try:
        async with db.session_factory() as session:
            source = await session.get(EvalRun, source_id)
        if source is None or source.suite != "e2e":
            print(f"eval_run #{source_id} 不存在或不是端到端评测", file=sys.stderr)
            return 2
        path = eval_data_dir(rag) / source.dataset
        digest = await anyio.to_thread.run_sync(ds.file_hash, path)
        if digest != source.dataset_hash:
            print(f"{path.name} 在 #{source_id} 之后改过，不能按题目 id 对回答", file=sys.stderr)
            return 2
        items = {i.id: i for i in await anyio.to_thread.run_sync(e2e_load, path)}
        ctx = await e2e_context(db, list(items.values()))
        if ctx is None:
            return 2
        doc_ids, sections, references = ctx
        trace = eval_trace_config(settings, "e2e-rejudge", path.name)
        sem = asyncio.Semaphore(concurrency)
        counts = {"rebuilt": 0, "mismatch": 0}
        async with retriever(rag, db.session_factory) as r:

            async def passages_of(
                detail: dict[str, Any], item: e2e.E2EItem
            ) -> dict[int, tuple[list[str], str]]:
                if detail.get("context"):
                    return {
                        c["index"]: (
                            c["path"],
                            sections.get((item.file_name, tuple(c["path"])), ""),
                        )
                        for c in detail["context"]
                    }
                if detail.get("no_hits"):
                    return {}
                counts["rebuilt"] += 1
                hits = (
                    await r.search(
                        item.question,
                        mode=SearchMode.HYBRID_RERANK,
                        document_ids=[doc_ids[item.file_name]],
                    )
                ).hits
                passages = {
                    i: (h.section_path, h.parent_content) for i, h in enumerate(hits, start=1)
                }
                for c in detail["cited_sections"]:
                    if passages.get(c["index"], ([], ""))[0] != c["path"]:
                        counts["mismatch"] += 1
                        key = (item.file_name, tuple(c["path"]))
                        passages[c["index"]] = (c["path"], sections.get(key, ""))
                return passages

            async def attempt(detail: dict[str, Any]) -> e2e.E2EResult:
                item = items[detail["id"]]
                passages = await passages_of(detail, item)
                cited = [int(c["index"]) for c in detail["cited_sections"]]
                citations = [
                    {
                        "index": i,
                        "section_path": passages.get(i, ([], ""))[0],
                        "parent_content": passages.get(i, ([], ""))[1],
                    }
                    for i in cited
                ]
                judgement = await judge_one(
                    judge_llm,
                    item,
                    detail["answer"],
                    cited,
                    passages,
                    references[item.id],
                    trace,
                )
                print(f"  {item.id}: {detail['judge']['correctness']} → {judgement.correctness}")
                return e2e.E2EResult(
                    item,
                    detail["answer"],
                    citations,
                    judgement,
                    float(detail["latency_ms"]),
                    bool(detail.get("no_hits")),
                    extra={"context": [{"index": i, "path": p} for i, (p, _) in passages.items()]},
                )

            async def one(detail: dict[str, Any]) -> e2e.E2EResult:
                async with sem:
                    return await with_retries(lambda: attempt(detail), label=detail["id"])

            print(
                f"重判 #{source_id}：{len(source.details)} 题，"
                f"裁判 {judge_model}（{e2e.JUDGE_VERSION}）"
            )
            results = list(await asyncio.gather(*(one(d) for d in source.details)))
        run_id, metrics = await save_e2e(
            db,
            dataset=source.dataset,
            digest=digest,
            config={
                **source.config,
                "judge_model": judge_model,
                "judge_version": e2e.JUDGE_VERSION,
                "source_run": source_id,
                "context_rebuilt": counts["rebuilt"],
                "context_mismatch": counts["mismatch"],
            },
            results=results,
        )
    finally:
        await db.dispose()
        flush_traces()
    print(f"还原上下文 {counts['rebuilt']} 题，被引用段落与重新检索对不上 {counts['mismatch']} 处")
    # 新的抽检样本：排除调裁判时看过的题，并且不带裁判结论（盲标）
    await report_e2e(
        rag,
        reports_dir,
        dataset=source.dataset,
        run_id=run_id,
        metrics=metrics,
        results=results,
        references=references,
        exclude=exclude,
        blind=True,
    )
    return 0


def e2e_load(path: Path) -> list[e2e.E2EItem]:
    rows = [
        e2e.E2EItem.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len({i.id for i in rows}) != len(rows):
        raise ValueError(f"{path.name} 里有重复的题目 id")
    return rows


def render_e2e(
    name: str, run_id: int, m: dict[str, object], results: list[e2e.E2EResult]
) -> list[str]:
    a = m["answerable"]
    u = m["unanswerable"]
    lat = m["latency_ms"]
    assert isinstance(a, dict) and isinstance(u, dict) and isinstance(lat, dict)
    lines = [
        f"# 端到端评测报告（{name}，eval_run #{run_id}）",
        "",
        f"- 时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 有答案 {a['n']} 题：正确性 **{a['correctness']}**（correct {a['correct_rate']}，"
        f"partial {a['partial_rate']}），误拒率 {a['false_refusal_rate']}，"
        f"回答了但没有引用 {a['uncited_rate']}",
        f"- 引用忠实度（{m['citations_total']} 处引用里有依据的比例）**{m['citation_precision']}**",
        f"- 无答案 {u['n']} 题：拒答率 **{u['refusal_rate']}**，编造率 {u['hallucination_rate']}",
        f"- 裁判输出不完整 {m['judge_unparsed']} 次；"
        f"回答延迟 P50 {lat['p50']} ms，P95 {lat['p95']} ms",
        "- 裁判与人工的一致率：待人工标注（`python -m app.eval agreement`）",
        "",
        "## 失败案例",
        "",
    ]
    for x in results:
        j = x.judgement
        bad_cites = [k for k, ok in j.citations.items() if not ok]
        if j.correctness == "correct" and not bad_cites and (j.refused != x.item.answerable):
            continue
        lines.append(
            f"- `{x.item.id}` {j.correctness}{'，拒答' if j.refused else ''}"
            f"{'，无依据的引用 ' + str(bad_cites) if bad_cites else ''}：{x.item.question}  \n"
            f"  裁判：{j.reason}"
        )
    lines.append("")
    return lines


async def cmd_agreement(path: Path, run_override: int | None, key: str) -> int:
    """人工标注 vs 某次运行的裁判结论（按题目 id 对上），一致率写进那次运行的 metrics[key]。
    同一批人工标注可以分别和 v1、v2 裁判比；用作调裁判开发集的那批，key 写 human_dev。"""
    settings = get_settings()
    try:
        file_run, rows = await anyio.to_thread.run_sync(e2e.read_human_csv, path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    run_id = run_override or file_run
    db = Database(settings.db)
    try:
        async with db.session_factory() as session, session.begin():
            run = await session.get(EvalRun, run_id, with_for_update=True)
            if run is None or run.suite != "e2e":
                print(f"eval_run #{run_id} 不存在或不是端到端评测", file=sys.stderr)
                return 2
            try:
                result = e2e.agreement(e2e.with_judge(rows, run.details))
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            # JSONB 整体替换（原地修改字典不会被 SQLAlchemy 发现）
            run.metrics = {
                **run.metrics,
                key: {
                    **result,
                    "labels": path.name,
                    "checked_at": f"{datetime.now():%Y-%m-%d %H:%M}",
                },
            }
    finally:
        await db.dispose()
    print(
        f"人工标注 {result['n']}/{result['of']} 题 vs #{run_id} 的裁判："
        f"正确性一致率 {result['correctness_agreement']}（kappa {result['correctness_kappa']}），"
        f"拒答一致率 {result['refused_agreement']}，"
        f"引用判断一致率 {result['citations_agreement']}；"
        f"已写入 metrics.{key}"
    )
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
    p_ret.add_argument(
        "--rewrite", action="store_true", help="先用对话模型把问题改写成条款用语再检索（spec Q14）"
    )
    p_ret.add_argument(
        "--keep-original", action="store_true", help="配合 --rewrite：原问题也一起召回（spec D39）"
    )
    p_ret.add_argument("--scope", choices=["document", "global"], default="document")
    p_ret.add_argument(
        "--modes", default=",".join(m.value for m in SearchMode), help="逗号分隔的检索配置"
    )
    p_e2e = sub.add_parser("e2e", help="端到端评测（条款问答 + LLM 裁判）")
    p_e2e.add_argument(
        "dataset", nargs="?", type=Path, help="默认 RAG__DATA_DIR/eval/e2e_clause.jsonl"
    )
    p_e2e.add_argument("--judge-model", default="qwen-max", help="裁判模型，建议与回答模型不同")
    p_e2e.add_argument("--concurrency", type=int, default=4)
    p_e2e.add_argument("--reports-dir", type=Path)
    p_agree = sub.add_parser("agreement", help="人工抽检后计算裁判与人工的一致率")
    p_agree.add_argument("csv", type=Path)
    p_agree.add_argument("--run", type=int, help="和哪次运行的裁判比，默认文件里的 run_id")
    p_agree.add_argument("--key", default="human", help="写入 metrics 的键，如 human / human_dev")
    p_rej = sub.add_parser("rejudge", help="用当前版本的裁判重判某次端到端运行的回答")
    p_rej.add_argument("run", type=int)
    p_rej.add_argument("--judge-model", default="qwen-max")
    p_rej.add_argument("--exclude", type=Path, help="已看过的人工标注 CSV：这些题不进新的抽检样本")
    p_rej.add_argument("--concurrency", type=int, default=4)
    p_rej.add_argument("--reports-dir", type=Path)
    p_route = sub.add_parser("routing", help="路由评测（默认测试集）")
    p_route.add_argument(
        "datasets", nargs="*", type=Path, help="默认 RAG__DATA_DIR/eval/routing_test.jsonl"
    )
    p_route.add_argument("--reports-dir", type=Path)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log.level, settings.log.json_format)
    try:
        if args.command == "generate":
            out = args.out or eval_data_dir(settings.rag) / "retrieval_generated.raw.jsonl"
            code = asyncio.run(cmd_generate(args.per_doc, out))
        elif args.command == "routing":
            code = asyncio.run(cmd_routing(args.datasets, args.reports_dir))
        elif args.command == "e2e":
            code = asyncio.run(
                cmd_e2e(args.dataset, args.judge_model, args.reports_dir, args.concurrency)
            )
        elif args.command == "agreement":
            code = asyncio.run(cmd_agreement(args.csv, args.run, args.key))
        elif args.command == "rejudge":
            code = asyncio.run(
                cmd_rejudge(
                    args.run, args.judge_model, args.reports_dir, args.exclude, args.concurrency
                )
            )
        else:
            modes = [SearchMode(m) for m in args.modes.split(",")]
            code = asyncio.run(
                cmd_retrieval(
                    args.datasets,
                    modes,
                    args.scope,
                    args.reports_dir,
                    args.rewrite,
                    args.keep_original,
                )
            )
    except RagNotConfiguredError as exc:
        print(f"{exc}（写在 backend/.env）", file=sys.stderr)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
