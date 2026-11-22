# ---- 构建阶段：用 uv 安装依赖到 /app/.venv ----
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# 先只拷依赖清单，利用层缓存
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- 运行阶段 ----
FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    LOG__JSON_FORMAT=true
USER app
EXPOSE 8000

# 启动前先执行迁移、写入演示产品（幂等），再用 exec 让 uvicorn 接管 PID 1，才能收到 SIGTERM 并优雅关闭
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && python -m app.pricing load && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
