# CLAUDE.md

安心保 · 智能保险顾问：RAG + LangGraph 的保险顾问 Agent（学习 + 面试作品项目）。

- 设计与决策：[docs/SPEC.md](docs/SPEC.md)（决策 D1–D43、里程碑 M0–M9、风险 Q1–Q18）。做设计取舍前先查，取舍定了就补一条决策。
- 面试故事集：[docs/INTERVIEW.md](docs/INTERVIEW.md)。每个里程碑结束后补充（见「工作流程」）。
- 参考实现 `../hm-insurance/` 是教学项目，只读对照，不复制代码，它的 `.env` 里有泄露过的密钥，不要使用。

## 目录

| 目录 | 内容 |
|---|---|
| `backend/app/api/v1/` | 路由（auth、products、chat：会话与 SSE 问答、admin：条款管理、检索调试、评测记录），依赖在 `app/api/deps.py` |
| `backend/app/agent/` | LangGraph 对话图（graph）、提示词（prompts）、模型（llm）、运行时组装与 checkpointer（runtime） |
| `backend/app/services/` | 业务逻辑；路由不直接写 SQL |
| `backend/app/models/` | ORM 模型；新模型必须在 `models/__init__.py` 导入，否则 Alembic 看不到 |
| `backend/app/rag/` | 入库流水线：parser → structure → chunker → embedder → pipeline；检索：retriever（+ reranker） |
| `backend/app/pricing/` | 报价引擎：`extract`（PDF → JSON，开发依赖 pdfplumber）、`tables`（数据结构与结构校验）、`engine`（报价规则，纯函数）；费率数据 `rates/*.json` 入库 |
| `backend/app/eval/` | 离线评测：生成评测题、跑检索三组对照、写 `eval_runs` |
| `backend/migrations/` | Alembic 迁移 |
| `frontend/src/` | React 19 + antd 6 + React Router + TanStack Query + Zustand |
| `data/` | 原始 PDF（`raw/`）与 MinerU 解析缓存（`parsed/`），不入库；评测集 `data/eval/*.jsonl` 入库 |
| `eval/` | 评测报告（`reports/`）与结果汇总（`README.md`） |
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
uv run python -m app.pricing extract    # 费率表 PDF → app/pricing/rates/*.json（校验不过不写）；check 只校验，load 写库（容器启动时自动执行）
uv run python -m app.manage promote-admin <用户名>
uv run python -m app.eval retrieval    # 检索三组对照（真实调用 DashScope，约 10 分钟）；参数用环境变量覆盖，如 RAG__RETRIEVAL__FULLTEXT_WEIGHT=0.5
uv run python -m app.eval generate     # 用 LLM 生成检索题草稿（需人工审核后另存为正式评测集）

npm run dev                            # :5173，/api 与 /health 代理到 :8000
```

提交前的检查（与 CI 一致）：

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run mypy app ; uv run alembic check ; uv run pytest
npm run lint ; npx tsc -b ; npm run test ; npm run build
```

## 本机环境（Windows）

- 本机装了原生 PostgreSQL 占用 5432，容器映射到主机 **5433**（`DB__URL` 默认值已是 5433）。镜像用 `pgvector/pgvector:pg17`。
- 密钥只放 `backend/.env`（已 gitignore）：`RAG__MINERU_TOKEN`、`RAG__DASHSCOPE_API_KEY`、`LLM__API_KEY`（可选）。不要打印、提交或写进日志；查看 `.env` 时只看键名（`sed 's/=.*/=***/'`），不要 grep 出值。
- psycopg 的异步连接不支持 Windows 默认的 ProactorEventLoop：本机跑 uvicorn 要带 `--reload`（会用 SelectorEventLoop），否则对话退化为内存 checkpointer（日志 `agent.checkpointer_in_memory`）。
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
- 文档可不可检索看 `documents.indexed_hash IS NOT NULL`，**不要看 `status`**（它只是最近一次操作的结果，重新入库失败时旧块仍在、仍可检索）。
- 删除内容的规则（如删目录块）必须有"不会误删"的约束和回归测试：曾因只比较编号不比较标题，误删了 3 份条款的第 1 章标题和正文。改完规则在全部真实条款上对比删除的行。
- 子块向量文本带前缀「条款名｜章节路径」；查询时的分词必须和入库共用 `app.rag.tokenizer.tokenize`。送去重排的文本也带同样的前缀。
- 检索参数（k、召回条数、权重）只在 `RetrievalSettings` 里改，不接受 LLM 传入。条款过滤：`document_ids=None` 表示不限，空列表表示没有可查的条款（返回空，不能退化成不限）。
- 改了结构规则、切分或检索参数后重跑 `python -m app.eval retrieval`，和 `eval/README.md` 里的基线对比；简历和文档里的数字必须能在 `eval_runs` 里找到对应记录。
- 评测集 `data/eval/*.jsonl` 改动后数据集哈希会变，新旧结果不能直接比；手写题的标准章节路径从库里复制，跑评测时会校验路径存在，生成题还会校验原文依据仍在标准章节里（D40）。改了结构规则重新入库后，按依据重新标注生成题的 `gold`，不要只按路径改。

## Agent 约定

- LLM 只做「理解与表达」：意图、改写查询、组织回答。产品 id 必须在候选目录里（代码校验）、查哪份条款由代码根据产品决定、检索参数不暴露给 LLM（spec §6.3）。
- 路由、改写用 JSON 模式 + `parse_json_object` 自己解析，不用函数调用（各家兼容接口支持不一致，测试里假模型也能直接返回 JSON）；解析失败要有兜底，不能让整轮失败。
- 回答的引用由代码从正文 `[n]` 解析，只保留本轮检索到的编号；前端也只把有效编号渲染成可点击标记。
- 流式输出：回答 token 只转发 `answer` 节点里的 `AIMessageChunk`（节点返回的完整 AIMessage 也会出现在 messages 流里）；状态、引用、固定话术走 `get_stream_writer()`。
- 改了提示词要在真实模型上跑对话验证；影响检索的改动（改写提示词）要跑 `python -m app.eval retrieval --rewrite --keep-original`。
- **提示词里的示例不能取自评测集**（包括看过的失败案例）：曾把难题集错题的口语→术语映射写进改写提示词，难题集 R@5 虚高到 0.971，去掉后只有 0.912，比不改写还差（spec Q16）。
- 测试用 `tests/test_agent.py` 的 `make_runtime`（脚本化假 LLM + 假检索器 + InMemorySaver）。
- checkpointer 的表由 `AsyncPostgresSaver.setup()` 管理，`migrations/env.py` 的 `include_name` 把它们排除在 Alembic 之外。

## 报价约定

- 报价数字只由 `app/pricing/engine.py` 按费率表计算，LLM 不生成、不改写数字（D11）。参数缺失返回 `missing_params`、不可投保返回 `ineligible` 和原因，不抛异常。
- 费率 JSON 只放单元格；怎么算、首投年龄上限等写在规则类里（D42）。不要因为表里有某一行就认为能投保（众安 71 岁以上的行只适用于续保）。
- 提取按单元格位置，不按空格切文本；新增提取函数必须先核对表头文字（`_expect`），并让规则的 `check` 有能发现列错位的业务校验（D43）。
- 重新提取后审查 JSON 的 diff；`tests/test_pricing_samples.py` 的期望值要对照 PDF 页面图像人工读出，**不能从提取结果里抄**。新增产品时至少补：每张表的首尾行、每个维度、空白格（应不可投保）。
- 金额参数只接受字符串或整数（拒绝 float）；保额四舍五入到分。保存报价时服务端重新计算，不信任前端传来的金额。

## 前端约定

- 所有请求走 `src/lib/api.ts` 的 `request()`（自动带 token、401 自动退出、转换错误信息）。只有字符串请求体才加 JSON Content-Type，FormData 不能手动设。
- 属于当前用户的数据，queryKey 必须以 `USER_SCOPE`（`src/lib/queryKeys.ts`）开头，登录身份变化时会被统一清掉。
- 非组件的工具函数放 `src/lib/`，不要从页面文件导出（react-refresh lint）。
- 筛选、页码等放 URL 参数；跳转回来源页用 `safeRedirect`。
- 对话流式状态放 `stores/chat.ts`（Zustand），历史消息放 TanStack Query；SSE 用 `chatApi.stream`（fetch + `lib/sse.ts`，带 JWT），不用 EventSource。

## 测试约定

- 不访问网络：MinerU/DashScope 用假实现或 `httpx.MockTransport`。
- 数据库测试用 `tests/conftest.py` 的夹具：每个测试在外层事务里运行并回滚，业务代码的 commit 落到 SAVEPOINT。
- **不要假设开发库是空的**（里面有真实入库的数据）：需要干净表时在测试事务内先 `delete`。
- `conftest.py` 在导入应用前用环境变量清空 RAG 密钥、把数据目录指到临时目录，防止测试读到本机 `.env` 后真的调用外部服务、写入真实 `data/`（曾经发生过）。需要「已配置」的测试自己构造 `RagSettings`。
- 评测（`app.eval`）会真实调用外部服务，不进 pytest/CI；它用到的纯函数（指标、RRF、抽样）要有单测。
- 写完测试确认它能在修复前失败：去掉修复跑一次，或加对照组（如 `test_security.py`）。

## 工作流程

- 按 SPEC §10 的里程碑推进。每个里程碑：实现 → 测试 → 在真实环境（compose + 浏览器）验证 → 代码审查并修复 → 更新 SPEC（决策/风险）与 `docs/INTERVIEW.md` → 提交。
- `docs/INTERVIEW.md`：只写真实测到的数字，没测的写「待测」；有新的「原以为……结果发现……」时当场补进去。
- 提交信息用中文，前缀 `feat(mN)` / `fix(mN)` / `docs`，正文列出改了什么和测试结果。
- 验证时创建的临时账号、测试文件，验证完要清理。
