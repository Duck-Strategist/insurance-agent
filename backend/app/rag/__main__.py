"""条款入库命令行。

uv run python -m app.rag ingest --all            入库 data/raw/clauses 下全部 PDF（未变化的跳过）
uv run python -m app.rag ingest 某条款.pdf --force  强制重新入库指定文件
"""

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.infra.db import Database
from app.rag.embedder import EmbeddingAuthError
from app.rag.factory import RagNotConfiguredError, check_configured, ingestion_pipeline
from app.rag.parser import ParseAuthError


async def ingest(files: list[str], all_files: bool, force: bool) -> int:
    settings = get_settings()
    rag = settings.rag
    try:
        check_configured(rag)
    except RagNotConfiguredError as exc:
        print(f"{exc}（写在 backend/.env）", file=sys.stderr)
        return 2

    clauses = rag.clauses_dir
    paths = sorted(clauses.glob("*.pdf")) if all_files else [clauses / f for f in files]
    not_found = [p.name for p in paths if not p.exists()]
    if not paths or not_found:
        print(f"找不到文件：{not_found or clauses}", file=sys.stderr)
        return 2

    db = Database(settings.db)
    try:
        async with ingestion_pipeline(rag, db.session_factory) as pipeline:
            results = [await pipeline.ingest(p, force=force) for p in paths]
    except (ParseAuthError, EmbeddingAuthError) as exc:
        print(f"\n已停止：{exc}。请检查 backend/.env 中的密钥后重试。", file=sys.stderr)
        return 2
    finally:
        await db.dispose()

    print(f"\n{'结果':<8}{'父块':>6}{'子块':>6}  文件")
    for r in results:
        print(f"{r.outcome:<8}{r.parents:>6}{r.children:>6}  {r.file_name}")
        if r.error:
            print(f"        {r.error}")
    return 1 if any(r.outcome == "failed" for r in results) else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.rag")
    sub = parser.add_subparsers(dest="command", required=True)
    p_ingest = sub.add_parser("ingest", help="解析、切分、向量化并写入条款")
    p_ingest.add_argument("files", nargs="*", help="data/raw/clauses 下的文件名")
    p_ingest.add_argument("--all", action="store_true", help="处理目录下全部 PDF")
    p_ingest.add_argument("--force", action="store_true", help="内容未变也重新入库")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log.level, settings.log.json_format)
    if args.command == "ingest":
        if not args.files and not args.all:
            parser.error("请指定文件名或 --all")
        sys.exit(asyncio.run(ingest(args.files, args.all, args.force)))


main()
