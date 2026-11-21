"""向量化客户端（用 httpx MockTransport 模拟 DashScope）与解析缓存的测试，不访问网络。"""

import json
from pathlib import Path

import httpx
import pytest

from app.rag import embedder as embedder_module
from app.rag.embedder import DashScopeEmbedder, EmbeddingError
from app.rag.parser import MinerUParser, cache_path
from app.rag.tokenizer import tokenize

DIM = 4


def vec(i: int) -> list[float]:
    return [float(i)] * DIM


def make_embedder(handler, **kw) -> DashScopeEmbedder:  # type: ignore[no-untyped-def]
    return DashScopeEmbedder(
        "test-key",
        base_url="https://example.test/v1",
        model="m",
        dim=DIM,
        batch_size=3,
        transport=httpx.MockTransport(handler),
        **kw,
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(embedder_module.asyncio, "sleep", instant)


async def test_batches_and_reorders_by_index() -> None:
    seen_batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer test-key"
        assert body["dimensions"] == DIM
        seen_batches.append(body["input"])
        # 故意倒序返回，客户端应按 index 还原
        data = [{"index": i, "embedding": vec(int(t))} for i, t in enumerate(body["input"])][::-1]
        return httpx.Response(200, json={"data": data})

    emb = make_embedder(handler)
    result = await emb.embed([str(i) for i in range(7)])
    await emb.aclose()
    assert seen_batches == [["0", "1", "2"], ["3", "4", "5"], ["6"]]
    assert result == [vec(i) for i in range(7)]


async def test_retries_on_rate_limit_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": vec(1)}]})

    emb = make_embedder(handler)
    assert await emb.embed(["a"]) == [vec(1)]
    assert calls == 3


async def test_non_retryable_error_raises_immediately() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, text="invalid api key")

    with pytest.raises(EmbeddingError, match="HTTP 401"):
        await make_embedder(handler).embed(["a"])
    assert calls == 1


async def test_gives_up_after_max_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(EmbeddingError, match="503"):
        await make_embedder(handler, max_retries=2).embed(["a"])


async def test_rejects_wrong_dimension() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    with pytest.raises(EmbeddingError, match="维度"):
        await make_embedder(handler).embed(["a"])


async def test_parser_uses_cache_without_calling_mineru(tmp_path: Path) -> None:
    cached = cache_path(tmp_path, "abc123")
    cached.parent.mkdir(parents=True)
    cached.write_text("# 缓存的条款", encoding="utf-8")

    parser = MinerUParser("unused-token", tmp_path)
    # 若未命中缓存会去调用 MinerU 并因假 token 失败；这里应直接返回缓存
    assert await parser.parse(tmp_path / "x.pdf", "abc123") == "# 缓存的条款"


def test_tokenizer_keeps_insurance_terms() -> None:
    tokens = tokenize("等待期内确诊恶性肿瘤，不承担保险责任。").split()
    assert "等待期" in tokens
    assert "恶性肿瘤" in tokens
    assert "，" not in tokens and "。" not in tokens


async def test_auth_error_is_distinct_and_not_retried() -> None:
    from app.rag.embedder import EmbeddingAuthError

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, text="invalid api key")

    with pytest.raises(EmbeddingAuthError):
        await make_embedder(handler).embed(["a"])
    assert calls == 1
