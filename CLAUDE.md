# CLAUDE.md

安心保 · 智能保险顾问：RAG + LangGraph 的保险顾问 Agent（学习 + 面试作品项目）。

- 设计与决策：[docs/SPEC.md](docs/SPEC.md)（决策 D1–D30、里程碑 M0–M9、风险 Q1–Q11）。做设计取舍前先查，取舍定了就补一条决策。
- 面试故事集：[docs/INTERVIEW.md](docs/INTERVIEW.md)。每个里程碑结束后补充（见「工作流程」）。
- 参考实现 `../hm-insurance/` 是教学项目，只读对照，不复制代码，它的 `.env` 里有泄露过的密钥，不要使用。

## 目录

| 目录 | 内容 |
|---|---|
| `backend/app/api/v1/` | 路由（auth、products、admin），依赖在 `app/api/deps.py` |
| `backend/app/services/` | 业务逻辑；路由不直接写 SQL |
| `backend/app/models/` | ORM 模型；新模型必须在 `models/__init__.py` 导入，否则 Alembic 看不到 |
| `backend/app/rag/` | 入库流水线：parser → structure → chunker → embedder → pipeline |
| `backend/migrations/` | Alembic 迁移 |
| `frontend/src/` | React 19 + antd 6 + React Router + TanStack Query + Zustand |
| `data/` | 原始 PDF（`raw/`）与 MinerU 解析缓存（`parsed/`），不入库 |
| `deploy/` | docker compose |

## 常用命令

后端在 `backend/` 下用 uv，前端在 `frontend/` 下用 npm。

```powershell
docker compose -f deploy/docker-compose.yml up -d postgres   # 只起数据库
docker compose -f deploy/docker-compose.yml up -d --build    # 全套：前端 :8080，后端 :8000

uv run alembic upgrade head            # 迁移；改模型后：uv run alembic revision --autogenerate -m "..."
uv run uvicorn app.main:app --reload
uv run python -m app.seed              # 补齐演示产品（--overwrite 覆盖）
uv run python -m app.rag ingest --all  # 条款入库（--force 用缓存重跑）
uv run python -m app.manage promote-admin <用户名>

npm run dev                            # :5173，/api 与 /health 代理到 :8000
```

提交前的检查（与 CI 一致）：

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run mypy app ; uv run alembic check ; uv run pytest
npm run lint ; npx tsc -b ; npm run test ; npm run build
```

## 本机环境（Windows）

- 本机装了原生 PostgreSQL 占用 5432，容器映射到主机 **5433**（`DB__URL` 默认值已是 5433）。镜像用 `pgvector/pgvector:pg17`。
- 密钥只放 `backend/.env`（已 gitignore）：`RAG__MINERU_TOKEN`、`RAG__DASHSCOPE_API_KEY`。不要打印、提交或写进日志。
- 命令行输出中文前设 `PYTHONIOENCODING=utf-8`；Git Bash 会把 `/health` 这类参数改写成 Windows 路径，用 curl 时设 `MSYS_NO_PATHCONV=1`。
- `alembic.ini` 必须保持纯 ASCII：Windows 上按 GBK 读取，写中文注释会直接报错。
- Docker Hub 拉镜像很慢，优先复用本机已有镜像。

## 后端约定

- 配置用 pydantic-settings，嵌套键双下划线（`DB__URL`、`AUTH__JWT_SECRET`、`RAG__DATA_DIR`）。compose 里未设置的变量会变成空字符串，密钥类字段要把空串当作未配置。
- 全链路 async。CPU 密集或阻塞的调用必须放线程池（`anyio.to_thread.run_sync`）：Argon2 哈希、MinerU SDK、读 PDF 算哈希、文件读写用 `anyio.Path`。ruff 的 ASYNC 规则会抓一部分，但调用同步辅助函数时抓不到。
- 金额用 `Decimal`，API 输出统一为两位小数字符串（`schemas.product.Money`）。
- 有默认值的列同时写 `default` 和 `server_default`；改模型后 `alembic check` 必须无差异（CI 会查）。
- 认证：路由用 `CurrentUser` / `AdminUser` 依赖；每次请求按 token 的 `sub` 查库确认用户仍存在。
- 外部服务（MinerU、DashScope）的 401/403 要抛专门的 AuthError 并停止批处理，不能当作单个文件失败吞掉。

## RAG 约定

- **不用 LLM 改写条款原文**（D28）。`structure.py` 只删行、拆行、接行、改标题标记。
- 新增或修改结构规则时：先在真实解析结果上看问题（`data/parsed/<hash>/full.md`），再写规则，并把真实输出里的片段加成 `tests/test_rag_structure.py` 的回归用例。改完用 `--force` 重跑入库，在管理后台对比统计。
- 入库写库必须在单事务内，网络调用放在事务外；同一文档写入前先锁文档行。
- 子块向量文本带前缀「条款名｜章节路径」；查询时的分词必须和入库共用 `app.rag.tokenizer.tokenize`。

## 前端约定

- 所有请求走 `src/lib/api.ts` 的 `request()`（自动带 token、401 自动退出、转换错误信息）。只有字符串请求体才加 JSON Content-Type，FormData 不能手动设。
- 属于当前用户的数据，queryKey 必须以 `USER_SCOPE`（`src/lib/queryKeys.ts`）开头，登录身份变化时会被统一清掉。
- 非组件的工具函数放 `src/lib/`，不要从页面文件导出（react-refresh lint）。
- 筛选、页码等放 URL 参数；跳转回来源页用 `safeRedirect`。

## 测试约定

- 不访问网络：MinerU/DashScope 用假实现或 `httpx.MockTransport`。
- 数据库测试用 `tests/conftest.py` 的夹具：每个测试在外层事务里运行并回滚，业务代码的 commit 落到 SAVEPOINT。
- **不要假设开发库是空的**（里面有真实入库的数据）：需要干净表时在测试事务内先 `delete`。
- `conftest.py` 在导入应用前用环境变量清空 RAG 密钥、把数据目录指到临时目录，防止测试读到本机 `.env` 后真的调用外部服务、写入真实 `data/`（曾经发生过）。需要「已配置」的测试自己构造 `RagSettings`。
- 写完测试确认它能在修复前失败：去掉修复跑一次，或加对照组（如 `test_security.py`）。

## 工作流程

- 按 SPEC §10 的里程碑推进。每个里程碑：实现 → 测试 → 在真实环境（compose + 浏览器）验证 → 代码审查并修复 → 更新 SPEC（决策/风险）与 `docs/INTERVIEW.md` → 提交。
- `docs/INTERVIEW.md`：只写真实测到的数字，没测的写「待测」；有新的「原以为……结果发现……」时当场补进去。
- 提交信息用中文，前缀 `feat(mN)` / `fix(mN)` / `docs`，正文列出改了什么和测试结果。
- 验证时创建的临时账号、测试文件，验证完要清理。
