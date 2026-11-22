# 安心保 · 智能保险顾问 —— 从零重构 Spec

> 状态：v0.3（采访第 1–6 轮后定稿，所有待确认项已关闭）
> 参考实现：`../hm-insurance`（教学项目，只读对照，不复制代码）
> 标记 `【待确认】` 的条目将在下一轮采访中定稿。

---

## 0. 决策记录

| 编号 | 决策 | 理由 |
|---|---|---|
| D1 | 在 `insurance-agent/` 从零实现前端、后端、Agent 三端，原项目只作对照 | 学习完整链路，所有代码自己写 |
| D2 | 项目目的：**学习完整链路 + 简历/面试作品** | 取舍上优先"能讲清楚的设计"和"真实测得的指标"，而不是功能堆砌 |
| D3 | 技术栈基本沿用：React + antd / FastAPI + 异步 SQLAlchemy / LangGraph / DeepSeek | 降低学习面，把精力放在设计和质量上 |
| D4 | 业务范围：**以 AI 顾问为核心**；商城只保留产品浏览，不做下单/支付/保单 | 聚焦 Agent 与 RAG |
| D5 | Agent 能力：条款问答（带出处）、需求采集 + 推荐、确定性报价、理赔咨询 | |
| D6 | 编排：**意图路由 + 业务子图**（每个业务是独立子图） | 结构清晰，可按子图单独评测 |
| D7 | Human-in-the-loop：**方案确认**处使用 LangGraph `interrupt` | 方案生成 → 用户确认/修改 → 再报价 |
| D8 | 长期记忆：**结构化用户画像**入 PG，跨会话注入，用户可在页面查看修改 | |
| D9 | 存储：**PostgreSQL + pgvector** 一库搞定（业务 + 向量 + 全文） | 去掉 Milvus/etcd/minio/attu；入库可放进同一事务 |
| D10 | 模型：对话 DeepSeek；向量化与重排 阿里云 DashScope API | 免本地 GPU |
| D11 | 费率表：解析成结构化数据 + 脚本校验 + 人工抽查后入库 | 报价数字由代码计算，LLM 不生成数字 |
| D12 | 评测：检索评测、端到端回答评测、路由/报价单测 三类都做 | 简历指标来源 |
| D13 | 前端页面：对话页、产品浏览/详情、我的画像/方案、简易管理后台 | |
| D14 | 认证：账号密码 + JWT（access token），所有接口按用户隔离 | |
| D15 | 工程化：测试 + CI、LLM 可观测性、Docker 一键部署（Alembic 管理表结构）；暂不上云 | |
| D16 | 节奏：不赶时间，按里程碑推进，每个里程碑可独立演示 | |
| D17 | LLM 可观测性：**Langfuse 自托管**，加入 compose | 数据留在本地，面试时可演示 trace |
| D18 | 理赔咨询**结合画像中的已有保障**：只检索用户持有产品的条款；未登记时先追问 | |
| D19 | 评测集：**LLM 按章节生成 + 人工审核**，另手写一小批口语化难题 | 控制工作量，同时保留真实感 |
| D20 | 画像写入：每轮后台抽取增量 → 前端提示"保存到画像？" → **用户确认后才写库** | 避免误抽取污染长期记忆 |
| D21 | 画像字段：基础（出生日期/性别/社保）+ 职业类别/城市/年预算 + 家庭成员 + 已有保障；只记每字段来源与 `updated_at`，不留变更历史 | |
| D22 | 前端：React Router + TanStack Query（服务端数据）+ Zustand（对话流式等客户端状态） | |
| D23 | Python 3.12（uv 管理） | 依赖兼容性最稳 |
| D24 | 产品列表/详情**公开访问**，对话、画像、方案等个人数据才需要登录 | 与常见商城一致；先看产品再注册 |
| D25 | JWT 用 HS256，密钥至少 32 字节；`APP__ENV=prod` 仍用默认密钥时拒绝启动；每次请求按 `sub` 查库确认用户仍存在 | RFC 7518 §3.2；防止误用开发密钥上线 |
| D26 | 金额在 API 中统一输出为两位小数字符串（如 `"285.30"`），前端负责格式化 | 避免浮点误差，格式不随数据来源变化 |
| D27 | 演示产品由 `python -m app.seed` 按产品名幂等写入，容器启动时自动执行；不使用第三方图片外链 | 外链不可控，改用按险种的图标 |
| D28 | 条款结构化用规则而非 LLM；规则来自逐份检查 10 份真实条款的 MinerU 输出，每条有回归测试 | LLM 改写会截断长文、可能改动原文；规则可解释、可测试、零成本 |
| D29 | 一份条款可被多个产品共用（个人版/转保版），文档与产品按 `products.clause_file = documents.file_name` 关联 | 避免同一条款重复入库 |
| D30 | 测试与本机配置隔离：`conftest.py` 在导入应用前用环境变量清空 RAG 密钥、把数据目录指向临时目录 | 曾因测试读到本机 `.env` 而真实调用 MinerU、写入真实目录 |
| D31 | 文档状态拆成「最近一次操作结果」(`status`) 与「库里可用内容的版本」(`indexed_hash`)；检索按后者过滤 | 重新入库失败时旧块仍在：只有一个状态字段会让检索要么丢掉这份条款、要么不知道在用旧版本 |
| D32 | 全文召回按 BM25 的 IDF 给命中词加权（SQL 里逐词求和），不用 `ts_rank`；出现在超过 50% 子块里的词不参与 | `ts_rank` 不看词的稀有程度，且每个子块前缀都有条款名，几乎所有块同分 |
| D33 | 线上默认检索配置为「混合 + 重排」，重排模型 qwen3-rerank；RRF 全文权重保持 1.0 | M3 评测：重排在生成集上把 R@1 从 0.82 提到 0.96–0.97；难题集上 qwen3-rerank 明显优于 gte-rerank-v2（R@5 0.79→0.91，MRR 0.62→0.82，eval_runs #6 vs #22），生成集持平、延迟相同；重排后全文权重 1.0/0.5/0.3 的结果差异不超过 1 题（eval_runs #3/#8/#15，#6/#10/#17），不为 34 道难题单独调权重。代价是 P50 延迟增加约 1.7 秒 |
| D34 | 检索评测的标准答案用「条款文件名 + 章节路径」而不是块 id，一题可有多个标准章节；评测前校验标准章节在库里存在 | 重新入库后块 id 会变；手写题抄错路径会让指标悄悄变低 |
| D36 | 对话模型走 OpenAI 兼容接口，默认 DashScope `qwen-plus`（复用 DashScope Key），换 DeepSeek 只改 `LLM__*` 环境变量 | 修订 D3/D10：项目还没有可用的 DeepSeek Key；两家都兼容 OpenAI 接口，切换不改代码 |
| D37 | 路由与改写用 JSON 模式 + 自己解析，不用函数调用 / with_structured_output；解析失败有兜底（路由落到 clause_qa，改写用原问题） | 各家兼容接口对 tools 的支持不一致；测试里假模型直接返回 JSON 文本即可 |
| D38 | 消息正文由 LangGraph `AsyncPostgresSaver` 存在同一个库，`chat_threads` 只存会话元数据；checkpoint 表不归 Alembic 管（`include_name` 排除） | 不重复存消息；checkpointer 自带迁移，交给 Alembic 会被当成多余的表生成 DROP |
| D39 | 查询改写只补全指代（「那它」「那生孩子呢」），不换术语；第一句不改写；改写后的查询与原问题一起召回、用改写后的查询重排 | M4 评测：换术语的改写在两个集上都比不改写差（难题集 MRR 0.818→0.785，生成集 0.980→0.962，eval_runs #21/#22 vs #46/#47）；早先看起来的提升来自提示词示例泄漏了难题集（Q16） |
| D35 | 评测集生成用 DashScope `qwen-plus`（与向量化共用 Key），LLM 同时给出原文依据，脚本校验依据逐字出现在该章节，否则丢弃 | 本项目还没有 DeepSeek Key；依据校验能挡住编造的题 |

---

## 1. 目标与非目标

### 1.1 目标
1. 用户用自然语言咨询保险，Agent 能：
   - 回答条款问题，并**标注出处**（产品名 + 条款章节路径，可展开原文）；
   - 通过多轮追问采集需求，推荐产品组合；
   - 基于费率表给出**可复现的精确报价**；
   - 解答理赔问题：是否属于保障责任、需要哪些材料、流程是什么。
2. 每个能力都有评测数据集和可复现的指标。
3. 一条命令在本地拉起全套服务。

### 1.2 非目标（明确不做）
- 下单、支付、保单生成、真实理赔报案。
- 多租户、第三方登录、refresh token。
- 公网部署（以后可以加）。
- 移动端适配（只保证桌面浏览器）。

---

## 2. 原项目问题清单（重构要避免的）

| # | 问题 | 新方案对应 |
|---|---|---|
| P1 | 用户身份直接信任请求头 `x-user-id` | D14 JWT |
| P2 | 条款 Markdown 交给 LLM 一次性改写（`max_tokens=10000`），长文会被截断，也不校验原文是否被改动 | §5.1 规则化清洗，LLM 不改写正文 |
| P3 | Milvus 与 PG 双写无事务，父子块可能不一致 | D9 单库事务 |
| P4 | 异步图里调用同步 `invoke`；模型名硬编码 | 全链路 async；模型配置化 |
| P5 | 检索工具的 `k`、`product_id` 全由 LLM 填写，没有校验 | §6.3 工具参数校验，k 由代码固定 |
| P6 | 回答不返回出处 | D5 / §7 SSE `citations` 事件 |
| P7 | 没有评测 | D12 / §8 |
| P8 | 有费率表数据但没有报价工具 | D11 / §6.4 |
| P9 | 没有测试和可观测性 | D15 |
| P10 | `.env` 里有真实 API Key | `.env.example` + gitignore；密钥只从环境变量读 |

---

## 3. 总体架构

```
┌──────────── frontend (React + antd, Vite) ────────────┐
│ 对话页 │ 产品浏览/详情 │ 我的画像/方案 │ 管理后台        │
└───────────────┬───────────────────────────────────────┘
                │ REST + SSE（JWT）
┌───────────────▼──────── backend (FastAPI) ────────────┐
│ api/       路由层（auth、chat、threads、products、     │
│            profile、plans、admin）                      │
│ services/  业务层                                       │
│ agent/     LangGraph 图（路由 + 子图 + 工具）           │
│ rag/       入库流水线 + 混合检索                        │
│ pricing/   报价引擎（纯函数，可单测）                   │
│ infra/     DB、LLM/Embedding/Rerank 客户端、日志、追踪  │
└───────────────┬───────────────────────────────────────┘
                │
   PostgreSQL 17 + pgvector（业务表 / 向量 / tsvector / checkpointer）
   外部：DeepSeek、DashScope（embedding + rerank）、MinerU
```

目录结构（monorepo）：
```
insurance-agent/
  backend/     # FastAPI + Agent，uv 管理
  frontend/    # React + Vite
  data/        # 原始 PDF、解析产物、评测集
  eval/        # 评测脚本与报告
  deploy/      # docker-compose、init SQL
  docs/
```

---

## 4. 数据模型（初稿）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `users` | id, username, password_hash, role(`user`/`admin`) | role 用于管理后台 |
| `user_profiles` | user_id, birth_date, gender, social_security, occupation_class, city, annual_budget, family(jsonb), existing_coverage(jsonb), field_sources(jsonb：字段 → `chat`/`manual`), updated_at | D8/D21 长期画像 |
| `products` | id, name, insurer, category, description, target_group, highlights, min_premium, status | 4 类：medical / critical_illness / accident / life |
| `documents` | id, product_id, kind(`clause`/`rate_table`), file_path, file_hash, status, parser_version | 入库状态可在后台查看 |
| `chunks` | id, document_id, product_id, parent_id, level(`parent`/`child`), section_path[], content, embedding vector(1024), tsv tsvector | 父子块同表，父块不存向量 |
| `rate_plans` / `rate_items` | 沿用原项目思路：plan_code, rate_type, 维度（年龄段、性别、社保、缴费期、保额…）, premium / rate_value, source_page | 报价引擎的数据源 |
| `chat_threads` | id(=LangGraph thread_id), user_id, title, updated_at | 消息正文由 checkpointer 保存 |
| `plans` | id, user_id, thread_id, status(`draft`/`confirmed`), items(jsonb), quote_snapshot(jsonb) | 方案确认结果 |
| `quotes` | id, user_id, product_id, params(jsonb), breakdown(jsonb), total_premium, rate_plan_version | 报价可复现 |
| `eval_runs` | id, suite, config(jsonb), metrics(jsonb), created_at | 后台展示评测结果 |

画像 JSON 结构：
- `family`：`[{relation: spouse|child|parent, birth_date, gender, social_security}]`，推荐和报价时可选择"为谁配置"。
- `existing_coverage`：`[{insured: self|family 下标, product_id?, product_name, category, sum_assured, start_date}]`；`product_id` 为空表示库外产品，只能做通用理赔咨询。

---

## 5. RAG 设计

### 5.1 入库流水线
1. **解析**：MinerU 云端 API 把 PDF 转成 Markdown，结果按文件 SHA-256 缓存在 `data/parsed/<hash>/`；调整规则后重跑不再调用 MinerU。
2. **结构化**（`app/rag/structure.py`，纯规则，**不用 LLM 改写正文**，只删行/拆行/接行/改标题标记）：
   1. 删噪声：图片、页码、跨页重复的页眉页脚、带点线的目录行、多栏目录挤成一行的行；
   2. 删目录块：从"目录"标记到"第 1 章编号第二次出现"之前；
   3. 拆双栏合并行：`1.1 合同构成 本合同由……` → 标题 + 正文；
   4. 接断行：≥30 字且无句末标点的行与下一行正文合并（PDF 换行/分页截断）；
   5. 识别标题：8 类编号（第X部分/第X条/（一）/一、/（1）/1./1.1/1.1.1）按首次出现顺序定层级；带层级的栈保证同级是兄弟；数字编号的上级必须是其前缀（"9.4"只挂在"9."下）；无编号的 `#` 标题挂在最近编号标题下（附表/释义除外），含逗号或超 20 字的视为被误标的半句话。
   6. 每一步的删除/合并/拆分次数写入 `documents.stats`，在管理后台展示。
3. **切分**：父块 = 章节正文（超 2500 字按段落拆，路径不变）；子块 ≈ 400 字按句子边界切、重叠 60 字，同一父块内相邻短段落先合并再切；长表格按行拆分且每段带表头；子块向量化文本前缀"条款名｜章节路径"。
4. **向量化**：DashScope `text-embedding-v4`（1024 维），每批 10 条，429/5xx 指数退避重试，401/403 立即停止整个批处理。
5. **写入**：网络调用全部在事务外完成；锁文档行 → 删旧块 → 写父块/子块 → 更新状态在**同一事务**内；失败时上次成功的块原样保留。文档有两组状态（D31）：
   - `status`（`pending → parsed → indexed / failed`）= **最近一次操作**的结果；
   - `indexed_hash` = **库里现有块**对应的文件版本，只在成功时更新。检索按 `indexed_hash IS NOT NULL` 判断文档是否可用；`indexed_hash ≠ file_hash` 表示检索在用旧版本。
   - 文件内容未变且最近一次成功才跳过；上次失败的不带 `--force` 也会重试。登记用 `INSERT … ON CONFLICT DO NOTHING`，并发首次入库不撞唯一约束。
6. 管理后台（管理员）：上传条款 PDF（校验文件头、防路径穿越、≤30MB）、查看状态与统计、重新入库、查看父块切分结果。入库用 BackgroundTasks 在进程内执行（多实例部署需换任务队列）。
7. 命令行：`python -m app.rag ingest --all [--force]`；设管理员：`python -m app.manage promote-admin <用户名>`。

### 5.2 检索

实现：`app/rag/retriever.py`（M3）。参数在 `RetrievalSettings`（`RAG__RETRIEVAL__*`），由代码固定。
- 召回两路：pgvector 余弦相似度 + `tsvector('simple')` 全文检索（jieba 在应用层分词）。
- 用 RRF 融合两路结果，再交给 DashScope rerank 取 top-n。
- 命中子块后回溯父块作为上下文；返回结构化的 citation（product、section_path、chunk_id、片段）。
- `product_id` 过滤由代码根据对话状态决定，LLM 只能从候选集合里选，不能编造。

- 细节（M3）：
  - 两路各召回 30 个子块；HNSW 带条款过滤时开启 `hnsw.iterative_scan = relaxed_order`（pgvector 0.8），否则"先取 40 个近邻再过滤"会剩下很少几条；
  - 全文召回按 IDF 加权（D32）；融合用加权 RRF（k=60）；
  - 重排对融合后的前 30 个子块打分，送入的文本带「条款名｜章节路径」前缀（与向量化一致）；
  - 子块回溯父块并去重，返回前 5 个章节（父块）；重排的网络调用期间不占数据库连接。
- 管理后台「检索调试」页可对比三种配置的结果和各阶段耗时（`POST /api/v1/admin/search`）。

### 5.3 对照实验（写进简历）
三组配置：纯向量 / 混合 / 混合 + 重排，在同一评测集上比较 Recall@5、MRR、延迟。

M3 结果（范围 = 只在题目对应的条款内检索；报告 `eval/reports/retrieval-document-20261121-185832.md`，汇总见 `eval/README.md`）：

| 评测集 | 配置 | R@1 | R@5 | MRR@10 | P50 延迟 | eval_run |
|---|---|---|---|---|---|---|
| 生成集 166 题 | 纯向量 | 0.819 | 0.982 | 0.891 | 569 ms | #1 |
| | 混合 | 0.843 | 0.994 | 0.907 | 580 ms | #2 |
| | 混合 + 重排 | 0.970 | 1.000 | 0.984 | 2310 ms | #3 |
| 难题集 34 题 | 纯向量 | 0.471 | 0.794 | 0.592 | 547 ms | #4 |
| | 混合 | 0.235 | 0.676 | 0.439 | 548 ms | #5 |
| | 混合 + 重排 | 0.500 | 0.794 | 0.619 | 2047 ms | #6 |

上表重排为 gte-rerank-v2。换成 qwen3-rerank（其他不变）：生成集 R@1 0.964 / R@5 1.000 / MRR 0.980（#21）；难题集 **R@1 0.735 / R@5 0.912 / MRR 0.818**（#22），P50 延迟 2337 / 2391 ms。据此默认改用 qwen3-rerank（D33）。

---

## 6. Agent 设计

### 6.1 图结构

M4 实现（`app/agent/graph.py`）：`route → prepare（定位产品；缺产品追问、未入库说明；有上文时补全指代）→ answer（检索 + 带引用生成）`，
寒暄走规则不调 LLM，advisor / claim 暂时返回「开发中」的固定话术，other 返回范围说明。下图是完整设计。
```
START → load_context（取画像、上轮工作流）
      → route（规则寒暄 + LLM 结构化输出意图）
         ├─ clause_qa       条款问答子图
         ├─ advisor         需求采集 → 推荐 → [interrupt 方案确认] → 报价
         ├─ claim_consult   理赔咨询子图
         ├─ chitchat
         └─ fallback / human_handoff（固定话术）
      → extract_profile（抽取画像增量，只生成"建议"，通过 SSE profile_suggestion 推给前端，用户确认后经 PUT /profile 写库）
      → END
```

### 6.2 各子图要点
- **clause_qa**：定位产品（从对话中识别，或追问）→ 检索 → 生成回答，并强制带引用；检索不到时明确说"条款中未找到"。
- **advisor**：维护一个"需求槽位"状态（被保人、年龄、预算、关注险种…），缺什么问什么；槽位齐了调用 `query_candidate_products` 和 `quote`，生成方案卡片 → `interrupt` 等用户确认、删减或调整 → 确认后写入 `plans`。
- **claim_consult**：识别出险类型和被保人 → 从画像 `existing_coverage` 找出该被保人持有的库内产品（未登记则追问并给出登记建议）→ 只在这些产品的条款中检索保障责任、免责、等待期和理赔材料 → 输出结构化清单（是否可能赔付 + 依据条款 + 材料 + 流程），并声明"最终以保险公司审核为准"。
- 路由时带上"上一轮所在工作流"，处理"那它等待期多久？"这类追问。

### 6.3 工具约束
- 工具参数用 Pydantic 校验；`k` 等检索参数由代码固定，不暴露给 LLM。
- 所有工具都是 async；工具报错时返回结构化错误，交给 LLM 组织话术。

### 6.4 报价引擎
- `pricing/` 是纯函数：`quote(product, plan, params) -> QuoteResult(breakdown, total)`，不依赖 LLM。
- 入参缺失时返回 `missing_params`，由 Agent 去追问。
- 单测覆盖：每个产品至少抽 N 个点，与费率表原值逐一比对，要求完全一致。

---

## 7. 接口契约（初稿）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/register` `/login` | 返回 JWT |
| GET | `/api/v1/products` `/products/{id}` | 分页、按类别筛选 |
| GET/POST/PATCH/DELETE | `/api/v1/threads` | 会话 CRUD |
| GET | `/api/v1/threads/{id}/messages` | 历史消息（从 checkpointer 读） |
| POST | `/api/v1/chat` | SSE 流式对话（M4）：`{thread_id?, message}`，不带 thread_id 时新建会话；同一会话正在回答时 409 |
| POST | `/api/v1/chat/resume` | 方案确认后恢复图执行（interrupt resume） |
| GET/PUT | `/api/v1/profile` | 我的画像 |
| GET | `/api/v1/plans` | 我的方案 |
| POST/GET | `/api/v1/admin/documents` | 上传条款、查看入库状态 |
| GET | `/api/v1/admin/eval-runs` | 查看评测结果 |

**SSE 事件类型**：`thread`（会话 id 与标题，第一个事件）、`token`（文本增量）、`citations`（引用列表）、`plan_card`（待确认方案，同时表示进入 interrupt）、`quote_card`、`profile_suggestion`（待用户确认的画像增量）、`status`（如"正在检索条款…"）、`done`、`error`。

**前端技术**：React 18 + antd + React Router + TanStack Query + Zustand；SSE 用 `fetch` + ReadableStream 解析（需要带 JWT 头，不用原生 EventSource）。

---

## 8. 评测与指标

| 套件 | 数据集 | 指标 | 方法 |
|---|---|---|---|
| 检索 | 自建条款问答集（`data/eval/`，M3 已建：生成集 166 题、难题集 34 题）（目标 ≥150 条，覆盖 10 个产品，带标准章节）：LLM 按父块生成问题 + 人工审核，另手写 ≥30 条口语化/跨章节难题单独统计 | Recall@k、MRR、P95 延迟 | 三组配置对照（§5.3） |
| 端到端 | 同上 + 参考答案 | 正确性、引用忠实度、拒答正确率 | LLM-as-judge + 人工抽检 20%，报告两者一致率 |
| 路由 | 标注意图集（目标 ≥200 条，含多轮追问） | 准确率、混淆矩阵 | pytest 参数化 |
| 报价 | 费率表抽样点 | 精确一致率（目标 100%） | 单测 |

- 评测脚本放在 `eval/`，每次运行写入 `eval_runs`，管理后台可查看。
- 所有简历数字必须来自 `eval_runs` 的真实记录。

---

## 9. 工程化

- **配置**：pydantic-settings；`.env.example` 入库，真实 `.env` 不入库。
- **数据库**：Alembic 迁移；pgvector 用官方镜像。
- **日志**：structlog JSON 日志，每个请求带 request_id / thread_id。
- **LLM 可观测性**：Langfuse 自托管，通过 LangChain callback 接入；trace 带 user_id / thread_id / 子图名；评测运行也上报，便于对照。
- **Python**：3.12，uv 管理依赖。
- **测试**：pytest + 测试库（docker 起的临时 PG）；LLM 调用在单测里 mock，评测才真实调用；前端用 Vitest 做基础测试。
- **CI**：GitHub Actions 跑 lint、类型检查、单测（评测不进 CI，太慢也太贵）。
- **部署**：`docker compose up` 拉起 postgres、backend、frontend（nginx）；Langfuse 及其依赖放在单独的 compose profile（`--profile observability`），不需要时可以不启动。

---

## 10. 里程碑（每个都可独立演示）

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M0 骨架 | monorepo、compose（PG+pgvector）、FastAPI 骨架、Alembic、CI、前端脚手架 | `docker compose up` 后 `/health` 返回 ok，CI 通过 |
| M1 用户与产品 | 注册登录 JWT、产品列表/详情、前端产品页与登录 | 登录后浏览产品 |
| M2 RAG 入库 | 解析 → 清洗 → 切分 → 向量化 → 事务写入，后台查看入库状态 | 10 份条款全部 indexed |
| M3 检索 + 评测 v1 | 混合检索、重排、检索评测集与三组对照 | 产出第一份检索指标 |
| M4 对话与条款问答 | 会话管理、SSE、路由、clause_qa、引用展示 | 前端能问条款并展开出处 |
| M5 报价引擎 | 费率表解析与校验、pricing 模块、报价单测 | 抽样点 100% 一致 |
| M6 顾问与方案确认 | 需求槽位、推荐、interrupt 方案确认、plans | 端到端走完"咨询 → 方案确认 → 报价" |
| M7 画像与理赔 | 长期画像抽取与页面、claim_consult | 新会话自动带入画像 |
| M8 评测与观测 | 端到端评测、路由评测、LLM tracing、后台评测页 | 完整指标报告 |
| M9 打磨 | README、架构图、演示视频、简历要点 | |

---

## 11. 风险与未决问题

| # | 问题 | 状态 |
|---|---|---|
| Q1 | 费率表格式差异大（固定保费 / 年龄段 / 按保额千分比 / 矩阵），解析和校验的工作量可能被低估 | 风险：M5 可先做 3–4 个产品，再扩展 |
| Q2 | LLM 可观测性选型 | 已定 → D17 |
| Q3 | 理赔咨询是否结合已有保障 | 已定 → D18 |
| Q4 | 评测集构建方式 | 已定 → D19 |
| Q5 | 画像抽取时机 | 已定 → D20 |
| Q6 | 前端状态管理 | 已定 → D22 |
| Q7 | Python 版本 | 已定 → D23 |
| Q8 | LLM 生成的评测问题容易和条款原文措辞过于相似，检索指标会偏高 | 风险：用手写难题子集单独报告，作为对照 |
| Q9 | Langfuse 自托管 v3 需要额外的 ClickHouse/Redis 等组件，本机资源占用较大 | 风险：用 compose profile 按需启动 |
| Q10 | 双栏排版条款的左栏标题会被 MinerU 截断（如"1.2 合同成立与生"，"效"进了正文），只影响引用展示的标题，不影响正文 | 已知局限；可用 content_list.json 的版面坐标修复，暂不做 |
| Q11 | 部分附表疾病定义被 MinerU 识别为表格；目前按行拆分并保留表头，但同一表格内的疾病没有单独的章节路径 | M3 评测：生成集里的附表题在「混合 + 重排」下全部进前 5，未观察到对召回的影响；仍影响引用展示，暂不处理 |
| Q12 | 众安释义中「八十四、……」等条目挂在更早出现的「（十三）原发性肉碱缺乏症」下：释义条目用的「一、」比「（一）」首次出现得晚，按首次出现顺序被排在更深层级 | 已知局限（只影响引用里的章节名，不影响召回）；与 Q13 一起修，需要重新入库并更新评测集标准路径，单独做 |
| Q13 | 评测集审核时发现的结构问题：① 超过 20 字的无编号病种标题（如「器官移植导致的人类免疫缺陷病毒（HIV）感染」）被当成正文，内容挂在上一个病种下（166 题里 4 题受影响：gen-082/100/115/122）；② 星辰守护的「条款目录」块未被删除 | 已知局限：只影响引用的章节名，不影响召回。M4 未修：修复要重新入库，所有块 id 和部分评测集标准路径会变，作为单独的一次改动（附表/释义里放宽标题长度限制，需在全部条款上核对误识别） |
| Q14 | 口语化提问与条款术语之间的词汇鸿沟：「自己先掏多少钱」↔免赔额、「之前就有的病」↔既往症、「刚买完就查出」↔等待期。gte-rerank-v2 下难题集 7 题未进前 5；换 qwen3-rerank 后剩 3 题（心梗↔猝死/疾病免责、既往症、等待期） | M4 试过换术语的查询改写：去掉泄漏后反而更差（D39），未采用。仍存在：如「那生孩子住院能报吗」答案在责任免除里，检索不到时只能回答「视情况而定」。后续可试：对「赔不赔」类问题额外检索责任免除章节 |
| Q15 | 重排让 P50 延迟增加约 1.0–1.7 秒（gte-rerank-v2 与 qwen3-rerank 相近，30 个候选），向量化 P50 约 490 ms，两路召回的 SQL P50 约 70–90 ms | M4 实测首个 token 5–9 秒（路由约 1–2 秒、检索 + 重排约 2–3 秒、回答模型首 token 约 2–3 秒；有上文时再加改写约 1–2 秒）。可选：路由与检索并行、减少重排候选数 |
| Q16 | 评测集泄漏：改写提示词的示例取自难题集的错题，难题集 R@5 虚高到 0.971（#45），去掉后 0.912（#49） | 已处理：示例不再取自评测集（CLAUDE.md 约定）。教训：评测集只用于评估，看过的失败案例不能回流到提示词或规则里；需要调参时另留开发集 |
| Q17 | 删除用户时 `chat_threads` 级联删除，但 LangGraph checkpoint 表里的消息不会跟着删（不在同一套外键里） | 目前没有删除用户的功能；做账号注销时要先逐个会话调用 `checkpointer.adelete_thread` 再删用户 |
