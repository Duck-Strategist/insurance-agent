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
| D40 | 跑检索评测前，除了校验标准章节路径存在，还要校验生成题的原文依据（evidence）仍在标准章节里 | 结构规则改动后，路径可能还在、但原文已挪到别的章节：Q13 修复后 gen-100/115 的路径仍有效，答案却已在新识别出的病种章节里，只校验路径会让指标悄悄失真 |
| D35 | 评测集生成用 DashScope `qwen-plus`（与向量化共用 Key），LLM 同时给出原文依据，脚本校验依据逐字出现在该章节，否则丢弃 | 本项目还没有 DeepSeek Key；依据校验能挡住编造的题 |
| D41 | 费率数据存为「每产品一份 JSON」：提取后提交进仓库（`app/pricing/rates/`）审查 diff，入库时整份存进 `rate_books.content`（JSONB），版本 = JSON 文本哈希；新版本成为当前版本，旧版本保留，报价记录引用具体版本。修订 §4 的 `rate_plans`/`rate_items` | 引擎每次都整产品读取，从不按单元格查 SQL；整份存储让「报价可复现」变成引用一行记录，费率表更新后旧报价仍能按当时的数据重算；JSON 进仓库，改动在 PR 里逐格可审 |
| D42 | 数据与规则分开：JSON 只放单元格（年龄区间 × 维度 → 数值 + 页码），怎么算（哪几张表相加、按保费反算保额、首投年龄上限）写在代码里的规则类（固定计划 / 医疗年龄段 + 加油包 / 终身寿保额表）。可投保年龄以规则为准，不以表里有没有这一行为准 | 众安费率表列到 105 岁、门急诊列到 65 岁，但注释写明 71 岁以上（门急诊 56 岁以上）只适用于续保；只看表会把续保价报给首次投保的人 |
| D43 | 费率表提取按表格线取单元格（pdfplumber），不按空格切文本；每个提取函数先逐列核对表头文字再按固定列映射取值；提取后跑结构校验（年龄区间无重叠无缺口、正数）和业务校验（交费期越长每千元保费对应保额越高、同列随年龄不增、无社保不低于有社保、计划档次递增），任何一项不过就不写文件 | 高龄行的空白格不在行尾：福有余 59 岁有 9 年交没有 10 年交、新华 61 岁起只有趸交且男女各一列，按文本切分会让数字整体左移到别的列；列映射写错时结构上看不出来，只有业务规律能发现（有测试：对调两列后校验必须报错） |

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
| `rate_books` | product_id, version, source_file, source_sha256, content(jsonb：全部费率表), is_current | 报价引擎的数据源（M5 起替代原计划的 `rate_plans`/`rate_items`，见 D41） |
| `chat_threads` | id(=LangGraph thread_id), user_id, title, updated_at | 消息正文由 checkpointer 保存 |
| `plans` | id, user_id, thread_id, status(`draft`/`confirmed`), items(jsonb), quote_snapshot(jsonb) | 方案确认结果 |
| `quotes` | id, user_id, product_id, rate_book_id, params(jsonb), breakdown(jsonb), annual_premium, pay_years, total_premium, sum_assured | 报价可复现（引用具体费率版本） |
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

M5 实现（`app/pricing/`）：
- 流程：`python -m app.pricing extract`（PDF → JSON，校验不过不写）→ 人工审查 diff + 对照 PDF 页面图像补抽查点 → 提交 → 容器启动时 `python -m app.pricing load`（内容没变则跳过）。
- 规则：`FixedPlanRule`（意外险：年龄段 × 计划）、`MedicalRule`（百万医疗：年龄段 × 社保 + 可选加油包，首投年龄上限在规则里）、`LifeRule`（增额终身寿：按「每 1000 元年交保费对应的基本保额」由保费反算保额，四舍五入到分）。
- 结果状态：`ok` / `missing_params`（缺哪些参数）/ `invalid`（格式错误）/ `ineligible`（不可投保及原因，如「65 周岁不能选5年交，可选：趸交」）。每行明细带计算依据和费率表页码。
- 第一批 7 个产品（3 种格式）：小蜜蜂6号、小蜜蜂6号玫瑰版、小学童2号Pro、众安尊享e生2026、中英福满佳C、太保福有余2025、新华E增福，共 1,776 个单元格。其余见 Q18。
- 只报首次投保、个人单、年交、标准体的价格；家庭单折扣、续保、月交/半年交、加费（EM）暂不支持。年龄按整岁计算，「0 周岁须出生满 30 天」这类出生天数要求只在参数提示里说明，不做校验。

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
| GET | `/api/v1/products/{id}/pricing` | 报价所需参数及可选值（公开，M5）；不支持报价时 `supported=false` |
| POST | `/api/v1/products/{id}/quote` | 试算（公开，不保存）：`{params}`；缺参、不可投保也返回 200，看 `status` |
| POST/GET | `/api/v1/quotes` | 保存报价（服务端重新计算，不信任前端金额；算不出 422）/ 我的报价记录 |
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
| 报价 | 费率表抽样点（M5 第一批：7 个产品 60 个点，期望值人工对照 PDF 页面图像读出，不来自提取脚本；含 10 个空白格应不可投保） | 精确一致率（目标 100%；M5 第一批 60/60） | 单测 `tests/test_pricing_samples.py` |

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
| Q1 | 费率表格式差异大（固定保费 / 年龄段 / 按保额千分比 / 矩阵），解析和校验的工作量可能被低估 | M5 实测：10 份费率表是 5 种格式。第一批做了 3 种格式 7 个产品（D41–D43），剩余见 Q18 |
| Q2 | LLM 可观测性选型 | 已定 → D17 |
| Q3 | 理赔咨询是否结合已有保障 | 已定 → D18 |
| Q4 | 评测集构建方式 | 已定 → D19 |
| Q5 | 画像抽取时机 | 已定 → D20 |
| Q6 | 前端状态管理 | 已定 → D22 |
| Q7 | Python 版本 | 已定 → D23 |
| Q8 | LLM 生成的评测问题容易和条款原文措辞过于相似，检索指标会偏高 | 风险：用手写难题子集单独报告，作为对照 |
| Q9 | Langfuse 自托管 v3 需要额外的 ClickHouse/Redis 等组件，本机资源占用较大 | 风险：用 compose profile 按需启动 |
| Q10 | 双栏排版条款的左栏标题会被 MinerU 截断（如"1.2 合同成立与生"，"效"进了正文），只影响引用展示的标题，不影响正文 | 已知局限；可用 content_list.json 的版面坐标修复，暂不做 |
| Q11 | 部分附表疾病定义被 MinerU 识别为表格；目前按行拆分并保留表头，但同一表格内的疾病没有单独的章节路径 | M3 评测：生成集里的附表题在「混合 + 重排」下全部进前 5，未观察到对召回的影响；仍影响引用展示，暂不处理。结构修复后最大的章节是星相守2 附表里的一张疾病定义表（24,861 字，挂在「严重结核性脑膜炎」下），属于这一类 |
| Q12 | 众安释义中「八十四、……」等条目挂在更早出现的「（十三）原发性肉碱缺乏症」下：释义条目用的「一、」比「（一）」首次出现得晚，按首次出现顺序被排在更深层级 | 已解决（`fix(m2)`）：最高一级是「第X部分/章」时，每个部分内部重新按首次出现顺序分配层级；众安释义里的条目回到「第七部分 释义」下，病种挂在「二十九、重大疾病」「三十、特定疾病」「四十、罕见病」下 |
| Q13 | 评测集审核时发现的结构问题：① 超过 20 字的无编号病种标题（如「器官移植导致的人类免疫缺陷病毒（HIV）感染」）被当成正文，内容挂在上一个病种下（166 题里 4 题受影响：gen-082/100/115/122）；② 星辰守护的「条款目录」块未被删除 | 已解决（`fix(m2)`）：附表/释义里无编号病种名放宽到 30 字（排除以「条款」结尾的条款名）；标题行去掉图标字体私用区字符和 `<sub>` 标签后「条款目录」能被识别，被识别成表格的目录一并删除。修复中还发现并修了：编号末级 3 位（信美 8.1.100–8.1.117）、4 级编号（1.5.1.12）、只剩编号的标题（达尔文「## 2.3.3.4」）都识别不出。仍存在的是 MinerU 本身的问题：gen-082 的病种标题在解析结果里缺失，gen-122 的标题被错放在别的病种定义中间 |
| Q14 | 口语化提问与条款术语之间的词汇鸿沟：「自己先掏多少钱」↔免赔额、「之前就有的病」↔既往症、「刚买完就查出」↔等待期。gte-rerank-v2 下难题集 7 题未进前 5；换 qwen3-rerank 后剩 3 题（心梗↔猝死/疾病免责、既往症、等待期） | M4 试过换术语的查询改写：去掉泄漏后反而更差（D39），未采用。仍存在：如「那生孩子住院能报吗」答案在责任免除里，检索不到时只能回答「视情况而定」。后续可试：对「赔不赔」类问题额外检索责任免除章节 |
| Q15 | 重排让 P50 延迟增加约 1.0–1.7 秒（gte-rerank-v2 与 qwen3-rerank 相近，30 个候选），向量化 P50 约 490 ms，两路召回的 SQL P50 约 70–90 ms | M4 实测首个 token 5–9 秒（路由约 1–2 秒、检索 + 重排约 2–3 秒、回答模型首 token 约 2–3 秒；有上文时再加改写约 1–2 秒）。可选：路由与检索并行、减少重排候选数 |
| Q16 | 评测集泄漏：改写提示词的示例取自难题集的错题，难题集 R@5 虚高到 0.971（#45），去掉后 0.912（#49） | 已处理：示例不再取自评测集（CLAUDE.md 约定）。教训：评测集只用于评估，看过的失败案例不能回流到提示词或规则里；需要调参时另留开发集 |
| Q18 | M5 第一批未覆盖：① 达尔文12号（887 页长表：96 种可选责任组合 × 性别 × 交费期 × 年龄，每千元保额费率 + EM 加费列，另有附加险）；② 医联有盟（180 页长表，8 个计划，保额固定 10 万）；③ 星相守2号个人版/转保版（基准费率 × 家庭单因子 × 无理赔折扣 × 免赔额因子，另有可选责任）；④ 达尔文宝贝12号没有费率表；⑤ 众安「重疾关爱加油包」按每 5 万保额计价但费率表没给保额范围，暂不开放；⑥ 种子数据里的参考保费区间来自原项目，与费率表不完全一致（如众安写 ¥255–1,420，费率表首投最低 142、最高 11,926；小学童已改为 180，已有的库要执行 `python -m app.seed --overwrite` 才会更新，容器启动时的 seed 不覆盖已有产品） | 待办：①② 是一行一条记录的长表，用 pypdfium2 取文本（pdfplumber 处理 887 页超过 10 分钟，pypdfium2 约 35 秒）；③ 需要新规则类型；⑥ 参考保费可改为从费率表推出 |
| Q17 | 删除用户时 `chat_threads` 级联删除，但 LangGraph checkpoint 表里的消息不会跟着删（不在同一套外键里） | 目前没有删除用户的功能；做账号注销时要先逐个会话调用 `checkpointer.adelete_thread` 再删用户 |
