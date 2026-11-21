# 安心保 · 智能保险顾问

基于 RAG + LangGraph 的保险顾问 Agent：条款问答（带出处）、需求采集与推荐、按费率表精确报价、理赔咨询。

设计文档见 [docs/SPEC.md](docs/SPEC.md)。

## 目录

| 目录 | 内容 |
|---|---|
| `backend/` | FastAPI + Agent 服务（Python 3.12，uv） |
| `frontend/` | React + antd + Vite |
| `deploy/` | docker compose |
| `docs/` | 设计文档 |

## 本地开发

前置：Docker Desktop、[uv](https://docs.astral.sh/uv/)、Node 24。

```powershell
# 1. 启动数据库（pgvector）
docker compose -f deploy/docker-compose.yml up -d postgres

# 2. 后端
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload        # http://localhost:8000/docs

# 3. 前端（另开终端）
cd frontend
npm install
npm run dev                                  # http://localhost:5173
```

## 一键启动全套

```powershell
docker compose -f deploy/docker-compose.yml up -d --build
```

前端 http://localhost:8080，后端健康检查 http://localhost:8000/health/ready。

## 检查

```powershell
cd backend;  uv run ruff check . ; uv run mypy app ; uv run pytest
cd frontend; npm run lint ; npm run test ; npm run build
```
