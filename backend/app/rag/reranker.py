"""重排：DashScope 文本重排接口（默认 qwen3-rerank，也支持 gte-rerank-v2）。

重排模型同时看到问题和候选文本（cross-encoder），比向量的余弦相似度更准，但更慢、按次计费，
所以只对两路召回融合后的前若干条做重排。
"""

import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RERANK_PATH = "/services/rerank/text-rerank/text-rerank"


class RerankError(Exception):
    pass


class RerankAuthError(RerankError):
    """API Key 无效：所有请求都会失败，调用方应停止。"""


@dataclass(frozen=True)
class RerankResult:
    index: int  # 在输入 documents 里的下标
    score: float


class Reranker(Protocol):
    async def rerank(self, query: str, documents: list[str]) -> list[RerankResult]: ...


class DashScopeReranker:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://dashscope.aliyuncs.com/api/v1",
        model: str = "qwen3-rerank",
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def rerank(self, query: str, documents: list[str]) -> list[RerankResult]:
        """返回按相关度从高到低排序的全部结果。"""
        if not documents:
            return []
        payload = {
            "model": self.model,
            "input": {"query": query, "documents": documents},
            "parameters": {"top_n": len(documents), "return_documents": False},
        }
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post(RERANK_PATH, json=payload)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise RerankError(f"重排请求失败：{exc}") from exc
            else:
                if resp.status_code == 200:
                    return self._parse(resp.json(), len(documents))
                if resp.status_code in (401, 403):
                    raise RerankAuthError(f"DashScope API Key 无效（HTTP {resp.status_code}）")
                if resp.status_code not in RETRYABLE_STATUS or attempt == self._max_retries:
                    raise RerankError(f"重排失败 HTTP {resp.status_code}: {resp.text[:300]}")
            delay = 2**attempt
            logger.warning("rerank.retry", attempt=attempt + 1, delay_s=delay)
            await asyncio.sleep(delay)
        raise RerankError("unreachable")

    @staticmethod
    def _parse(body: dict[str, object], n_docs: int) -> list[RerankResult]:
        output = body.get("output")
        results = output.get("results") if isinstance(output, dict) else None
        if not isinstance(results, list):
            raise RerankError("重排返回格式不符：缺少 output.results")
        parsed = [RerankResult(int(r["index"]), float(r["relevance_score"])) for r in results]
        if any(not 0 <= r.index < n_docs for r in parsed):
            raise RerankError("重排返回的下标越界")
        return sorted(parsed, key=lambda r: r.score, reverse=True)
