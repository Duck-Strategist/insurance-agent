"""PDF → Markdown。调用 MinerU 云端解析，结果按文件哈希缓存到 data/parsed/<hash>/。

解析一次要几十秒到几分钟、还消耗额度；缓存后，调整清洗和切分规则时可以反复重跑而不重新解析。
"""

import json
from pathlib import Path
from typing import Protocol

import anyio
import anyio.to_thread

from app.core.logging import get_logger

logger = get_logger(__name__)


class ParseError(Exception):
    pass


class ParseAuthError(ParseError):
    """凭证无效：所有文件都会失败，调用方应立即停止，而不是继续逐个重试。"""


class Parser(Protocol):
    async def parse(self, path: Path, file_hash: str) -> str: ...


def cache_path(cache_dir: Path, file_hash: str) -> Path:
    return cache_dir / file_hash / "full.md"


class MinerUParser:
    def __init__(self, token: str, cache_dir: Path, timeout_seconds: int = 600) -> None:
        self._token = token
        self._cache_dir = cache_dir
        self._timeout = timeout_seconds

    async def parse(self, path: Path, file_hash: str) -> str:
        cached = anyio.Path(cache_path(self._cache_dir, file_hash))
        if await cached.exists():
            logger.info("parse.cache_hit", file=path.name)
            return await cached.read_text(encoding="utf-8")

        logger.info("parse.mineru_start", file=path.name)
        # SDK 是同步阻塞的（内部轮询任务状态），放到线程里，不阻塞事件循环
        markdown, content_list = await anyio.to_thread.run_sync(self._extract, path)

        await cached.parent.mkdir(parents=True, exist_ok=True)
        await cached.write_text(markdown, encoding="utf-8")
        if content_list is not None:
            await (cached.parent / "content_list.json").write_text(
                json.dumps(content_list, ensure_ascii=False), encoding="utf-8"
            )
        logger.info("parse.mineru_done", file=path.name, chars=len(markdown))
        return markdown

    def _extract(self, path: Path) -> tuple[str, list[dict[str, object]] | None]:
        import httpx
        from mineru import AuthError, MinerU, MinerUError

        client = MinerU(self._token)
        try:
            # 条款是数字版 PDF，不需要 OCR；language=ch 提升中文版面识别
            result = client.extract(str(path), language="ch", timeout=self._timeout)
        except AuthError as exc:
            raise ParseAuthError("MinerU token 无效或已过期") from exc
        except httpx.HTTPStatusError as exc:
            # SDK 有时直接抛出 httpx 的异常，而不是包装成 AuthError
            if exc.response.status_code in (401, 403):
                raise ParseAuthError(
                    f"MinerU token 无效或已过期（HTTP {exc.response.status_code}）"
                ) from exc
            raise ParseError(f"MinerU 请求失败：HTTP {exc.response.status_code}") from exc
        except MinerUError as exc:
            raise ParseError(f"MinerU 解析失败：{exc}") from exc
        finally:
            client.close()
        if result.state != "done" or not result.markdown:
            raise ParseError(f"MinerU 解析失败：state={result.state} error={result.error}")
        return result.markdown, result.content_list
