import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

import anyio.to_thread
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.runtime import agent_runtime
from app.api import health
from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.infra.db import Database
from app.rag.factory import RagNotConfiguredError, check_configured, retriever
from app.rag.tokenizer import tokenize

logger = get_logger(__name__)

# 只接受短的、安全字符组成的外部 request id，防止日志注入和超长头
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _resolve_request_id(incoming: str | None) -> str:
    if incoming and _REQUEST_ID_PATTERN.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log.level, settings.log.json_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = Database(settings.db)
        async with AsyncExitStack() as stack:
            # 检索器持有向量化/重排的 HTTP 连接池，整个进程共用一个；未配置 Key 时为 None
            app.state.retriever = None
            try:
                check_configured(settings.rag, need_parser=False)
            except RagNotConfiguredError as exc:
                logger.warning("app.retriever_disabled", reason=str(exc))
            else:
                app.state.retriever = await stack.enter_async_context(
                    retriever(settings.rag, app.state.db.session_factory)
                )
                # jieba 首次分词要加载词典（约 1 秒）：启动时在线程里预热，不让第一个检索请求卡住
                await anyio.to_thread.run_sync(tokenize, "预热分词词典")
            # 对话图（M4）：依赖检索器和对话模型 Key，缺一个就不启用，对话接口返回 503
            app.state.agent = None
            if app.state.retriever is not None and settings.llm_api_key() is not None:
                app.state.agent = await stack.enter_async_context(
                    agent_runtime(settings, app.state.db.session_factory, app.state.retriever)
                )
            else:
                logger.warning("app.agent_disabled")
            logger.info("app.startup", env=settings.app.env)
            try:
                yield
            finally:
                await app.state.db.dispose()
                logger.info("app.shutdown")

    app = FastAPI(title=settings.app.name, debug=settings.app.debug, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _resolve_request_id(request.headers.get("x-request-id"))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 未处理异常：记录带 request_id 的堆栈，返回统一的 500，不向客户端泄露细节
            logger.exception("http.unhandled_error", method=request.method, path=request.url.path)
            response = JSONResponse(
                status_code=500,
                content={"detail": "服务器内部错误", "request_id": request_id},
            )
        response.headers["x-request-id"] = request_id
        logger.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )
        return response

    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(api_router)
    return app


app = create_app()
