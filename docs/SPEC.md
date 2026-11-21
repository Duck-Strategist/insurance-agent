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
   PostgreSQL 16 + pgvector（业务表 / 向量 / tsvector / checkpointer）
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
1. **解析**：MinerU 把 PDF 转成 Markdown（结果缓存，按 file_hash 去重）。
2. **清洗**：规则化处理（页眉页脚、断行、标题层级修复），**不用 LLM 改写正文**；输出前后做字符级 diff 统计，异常时报警。
3. **切分**：按标题层级切成父块（章节级），父块内再按长度切成子块，每个子块带上 `section_path`。
4. **向量化**：DashScope embedding，批量调用，失败重试。
5. **写入**：一个文档的父子块在**同一事务**内写入；文档状态 `pending → parsed → indexed / failed`。
6. 支持在管理后台上传条款 PDF 并触发入库。

### 5.2 检索
- 召回两路：pgvector 余弦相似度 + `tsvector('simple')` 全文检索（jieba 在应用层分词）。
- 用 RRF 融合两路结果，再交给 DashScope rerank 取 top-n。
- 命中子块后回溯父块作为上下文；返回结构化的 citation（product、section_path、chunk_id、片段）。
- `product_id` 过滤由代码根据对话状态决定，LLM 只能从候选集合里选，不能编造。

### 5.3 对照实验（写进简历）
三组配置：纯向量 / 混合 / 混合 + 重排，在同一评测集上比较 Recall@5、MRR、延迟。

---

## 6. Agent 设计

### 6.1 图结构
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
| POST | `/api/v1/chat` | SSE 流式对话 |
| POST | `/api/v1/chat/resume` | 方案确认后恢复图执行（interrupt resume） |
| GET/PUT | `/api/v1/profile` | 我的画像 |
| GET | `/api/v1/plans` | 我的方案 |
| POST/GET | `/api/v1/admin/documents` | 上传条款、查看入库状态 |
| GET | `/api/v1/admin/eval-runs` | 查看评测结果 |

**SSE 事件类型**：`token`（文本增量）、`citations`（引用列表）、`plan_card`（待确认方案，同时表示进入 interrupt）、`quote_card`、`profile_suggestion`（待用户确认的画像增量）、`status`（如"正在检索条款…"）、`done`、`error`。

**前端技术**：React 18 + antd + React Router + TanStack Query + Zustand；SSE 用 `fetch` + ReadableStream 解析（需要带 JWT 头，不用原生 EventSource）。

---

## 8. 评测与指标

| 套件 | 数据集 | 指标 | 方法 |
|---|---|---|---|
| 检索 | 自建条款问答集（目标 ≥150 条，覆盖 10 个产品，带标准章节）：LLM 按父块生成问题 + 人工审核，另手写 ≥30 条口语化/跨章节难题单独统计 | Recall@k、MRR、P95 延迟 | 三组配置对照（§5.3） |
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
