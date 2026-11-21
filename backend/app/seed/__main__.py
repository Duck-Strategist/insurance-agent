import argparse
import asyncio

from app.seed import main

parser = argparse.ArgumentParser(prog="python -m app.seed", description="写入演示产品数据")
parser.add_argument("--overwrite", action="store_true", help="用种子文件覆盖同名产品")
args = parser.parse_args()

asyncio.run(main(overwrite=args.overwrite))
