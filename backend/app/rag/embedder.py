"""文本向量化：DashScope 的 OpenAI 兼容 /embeddings 接口。"""

import asyncio
from typing import Protocol

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class EmbeddingError(Exception):
    pass


class EmbeddingAuthError(EmbeddingError):
    """API Key 无效：所有请求都会失败，调用方应立即停止。"""


class Embedder(Protocol):
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class DashScopeEmbedder:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        model: str,
        dim: int,
        batch_size: int = 10,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.dim = dim
        self._model = model
        self._batch_size = batch_size
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(60.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors += await self._embed_batch(texts[start : start + self._batch_size])
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        payload = {
            "model": self._model,
            "input": batch,
            "dimensions": self.dim,
            "encoding_format": "float",
        }
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post("/embeddings", json=payload)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise EmbeddingError(f"向量化请求失败：{exc}") from exc
            else:
                if resp.status_code == 200:
                    return self._parse(resp.json(), len(batch))
                if resp.status_code in (401, 403):
                    raise EmbeddingAuthError(f"DashScope API Key 无效（HTTP {resp.status_code}）")
                if resp.status_code not in RETRYABLE_STATUS or attempt == self._max_retries:
                    raise EmbeddingError(f"向量化失败 HTTP {resp.status_code}: {resp.text[:300]}")
            delay = 2**attempt
            logger.warning("embedding.retry", attempt=attempt + 1, delay_s=delay)
            await asyncio.sleep(delay)
        raise EmbeddingError("unreachable")

    def _parse(self, body: dict[str, object], expected: int) -> list[list[float]]:
        data = body.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise EmbeddingError(f"向量化返回条数不符：期望 {expected}")
        # 按 index 排序，不依赖返回顺序
        items = sorted(data, key=lambda d: d["index"])
        vectors: list[list[float]] = [item["embedding"] for item in items]
        if any(len(v) != self.dim for v in vectors):
            raise EmbeddingError(f"向量维度不是 {self.dim}")
        return vectors
