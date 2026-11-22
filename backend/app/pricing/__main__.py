"""费率表工具。

  uv run python -m app.pricing extract  从 data/raw/rate_tables/ 的 PDF 提取，
                                       写 app/pricing/rates/*.json
  uv run python -m app.pricing check    校验 JSON（结构 + 业务规则），有问题时退出码为 1
  uv run python -m app.pricing load     把 JSON 写入数据库（容器启动时执行，内容没变则跳过）

extract 之后必须人工审查 JSON 的 diff，并核对/补充
tests/test_pricing_samples.py 的抽查点（spec D11）。
"""

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.infra.db import Database
from app.pricing.engine import rule_for
from app.pricing.tables import RATES_DIR, check_structure, dumps_book, load_all_books

logger = get_logger(__name__)


def cmd_extract() -> int:
    from app.pricing.extract import SOURCES, extract_book

    rate_dir = get_settings().rag.data_dir / "raw" / "rate_tables"
    RATES_DIR.mkdir(exist_ok=True)
    failed = 0
    for source in SOURCES:
        book = extract_book(source, rate_dir)
        problems = check_structure(book)
        rule = rule_for(book.product)
        problems += rule.check(book) if rule else [f"{book.product}: 没有报价规则"]
        cells = sum(len(t.items) for t in book.tables.values())
        if problems:
            failed += 1
            print(f"✗ {book.product}：{len(problems)} 个问题，未写入")
            for p in problems[:20]:
                print("   ", p)
            continue
        (RATES_DIR / f"{source.slug}.json").write_text(
            dumps_book(book), encoding="utf-8", newline="\n"
        )
        print(f"✓ {book.product}：{len(book.tables)} 张表，{cells} 个单元格 → {source.slug}.json")
    return 1 if failed else 0


def cmd_check() -> int:
    books = load_all_books()
    failed = 0
    for book in books.values():
        rule = rule_for(book.product)
        problems = check_structure(book)
        problems += rule.check(book) if rule else [f"{book.product}: 没有报价规则"]
        status = "✓" if not problems else "✗"
        print(f"{status} {book.product}（版本 {book.version}）")
        for p in problems:
            print("   ", p)
        failed += bool(problems)
    return 1 if failed else 0


async def cmd_load() -> int:
    from app.services.pricing_service import load_rate_books

    settings = get_settings()
    db = Database(settings.db)
    try:
        async with db.session_factory() as session:
            report = await load_rate_books(session)
    finally:
        await db.dispose()
    logger.info(
        "pricing.load",
        inserted=report.inserted,
        unchanged=len(report.unchanged),
        unknown_products=report.unknown_products,
    )
    return 0


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(prog="python -m app.pricing", description="费率表工具")
    parser.add_argument("command", choices=["extract", "check", "load"])
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log.level, settings.log.json_format)
    if args.command == "extract":
        code = cmd_extract()
    elif args.command == "check":
        code = cmd_check()
    else:
        code = asyncio.run(cmd_load())
    sys.exit(code)


main()
