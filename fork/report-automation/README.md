# 报表自动化：让智能体按文档口径查库并出 xlsx

用户问「上个月张三的达人订单明细，导个 Excel」，智能体读报表文档、把自然语言条件换算成
参数、跑 SQL、生成 xlsx 并给出可下载的文件卡片。

**本目录不改 WeKnora 一行 Go 代码**，全部落在系统既有的扩展点上：上传技能包、沙箱密钥、
自定义 Agent、沙箱产物链路。

---

## 三层结构

| 层 | 承担什么 | 载体 | 文件 |
| --- | --- | --- | --- |
| **业务语义层** | 库名、表名、表结构、字段口径、SQL 模板、参数契约 | 语雀文档 | `doc-spec.md`、`templates/`、`samples/` |
| **执行层** | 真正连库、校验 SQL、写 xlsx | Skill 包（沙箱内脚本） | `skill/order-report/` |
| **编排层** | 检索文档 → 映射参数 → 调脚本 → 回答案并附文件 | 自定义 Agent 的 system prompt | `agent-prompt.md` |

这样切的理由：**凭证不能进文档**（文档会切成 chunk 进向量库，任何能检索该知识库的用户
都可能把连接串问出来），而**口径必须由业务方自助维护**（改口径不该改代码）。所以文档只放
语义，凭证放平台的加密存储，执行放技能脚本。

## 数据流

```
用户提问
  │
  ├─ 1. search_knowledge 在「梦宇-报表自动化」库里命中《达人订单报表》
  ├─ 2. read_document 读出 §5 口径 / §6 参数契约 / §7 SQL 模板与输出列
  ├─ 3. 把「上个月」「张三」映射成 :start_date / :end_date / :influencer，缺参数就问用户
  ├─ 4. write_sandbox_file 写 /workspace/query.sql
  │     shell_exec(skill_name="order-report", command='python "$WEKNORA_SKILL_DIR/scripts/run_report.py" ...')
  │       └─ 凭证由平台注入 → 护栏校验 → 查询 → 写 xlsx 到 $WEKNORA_SKILL_OUTPUT_DIR
  ├─ 5. ArtifactCollector 自动收集产物、落存储、挂到 message_artifacts
  └─ 6. 回答里 `![达人订单报表](sandbox:达人订单报表_20260801_20260831.xlsx)` → 前端渲染下载卡片
```

---

## 一个必须先知道的前提

**「把数据库连接信息写进文档」是走不通的。**

WeKnora 里唯一能执行 SQL 的内置工具是 `database_query`，它的表白名单是**硬编码**的：

```go
// internal/agent/tools/database_query.go:260
utils.WithSoftDeleteFilter("knowledge_bases", "knowledges", "chunks")
```

它只能查 WeKnora 自身业务库的三张表，且没有任何「读文档里的连接串然后连库」的能力。
全仓也没有 text2sql / nl2sql 模块，`internal/datasource/connector/` 下只有语雀、Notion、
飞书这类**文档源**连接器，不是数据库连接器。

所以要从文档规范之外补一个执行载体 —— 就是这个 Skill 包。**不要勾选 `database_query`**，
它只会让模型以为自己能查库然后一直报错。

---

## 目录索引

```
fork/report-automation/
├── README.md                          ← 本文件：方案总览与上线步骤
├── doc-spec.md                        ← 核心：语雀报表文档规范（八节模板 + 逐节要求 + 反例）
├── agent-prompt.md                    ← 编排层：Agent 配置 + system prompt 全文
├── checklist.md                       ← 分层自检清单 + 回归问题集
├── templates/
│   └── report-doc-template.md         ← 可直接复制粘贴的空模板
├── samples/
│   └── 达人订单报表.md                 ← 结构示范（占位 schema，发布前须替换）
├── skill/order-report/                ← 要上传到 WeKnora 的技能包
│   ├── SKILL.md
│   ├── requirements.txt
│   ├── .weknora/requirements.json     ← 环境变量声明
│   └── scripts/run_report.py          ← 连库 + 护栏 + 写 xlsx
└── tests/
    └── selftest.py                    ← 自检套件：从样板文档解析模板并实跑
```

---

## 上线步骤

### 1. 上传技能包

把 `skill/order-report/` 打包上传到 WeKnora 的技能设置页（技能名取自 `SKILL.md` 的
frontmatter，即 `order-report`）。装包代理会读取 `.weknora/requirements.json` 的声明，
把需要的环境变量登记成表单。

声明了 9 个变量，其中 5 个必填（`REPORT_DB_HOST` / `PORT` / `USER` / `PASSWORD` / `NAME`）。

> ⚠️ **打包时注意 `.weknora/` 是隐藏目录** —— 不少打包工具默认会跳过以 `.` 开头的目录。
> 用 `zip -r order-report.zip order-report/` 或系统的「压缩文件夹」（会带上隐藏文件）都行，
> 但打完请**确认包里有 `.weknora/requirements.json`**。
>
> 万一漏了也不是致命的：装包代理本身会分析包内容并生成这份声明（脚本里
> `os.environ.get("REPORT_DB_HOST")` 这样的读取就是它的判断依据）。带上这份是为了让声明
> 有权威版本、可版本管理，而不是依赖每次让模型重新推断。

### 2. 填凭证

「设置 → 沙箱密钥」填入 5 个必填项，用**只读账号**。建议一并填
`REPORT_DB_ALLOWED_TABLES`（逗号分隔的表名白名单）。

凭证 AES-GCM 加密存储，`json:"-"` 保证永不下发前端。

### 3. 写报表文档

复制 `templates/report-doc-template.md`，按 `doc-spec.md` 的要求填。首篇照
`samples/达人订单报表.md` 的结构来，**用你真实的库表结构替换掉全部占位内容**。

在语雀 `梦宇-报表自动化` 知识库里新建**「文档」类型**（不是表格/画板），标题填报表名称，
写完**发布**（草稿不会被同步）。

> 语雀连接器只取 `type == "Doc"`、已发布状态、Markdown 格式的正文。用错类型或存成草稿
> 会导致文档永远同步不进来。

### 4. 建 Agent

按 `agent-prompt.md` 建自定义 Agent：模式选 `smart-reasoning`，绑定知识库，启用沙箱与技能，
勾选 6 个工具（**别勾 `database_query` 和 `data_analysis`**），贴进 system prompt。

### 5. 按清单验证

从 `checklist.md` 的第 0 层开始逐层验。前 4 层是确定性的，第 5~9 层是端到端效果。

本地能复验的部分（第 3、4 层 + 文档一致性）：

```bash
python fork/report-automation/tests/selftest.py
```

---

## 已知约束与风险

| 事项 | 说明 |
| --- | --- |
| **代码块不被切块保护** | 切块器保护 Markdown 表格行（跨块还会重复表头），但**不保护代码块**。所以规范要求单个 SQL 模板不超过约 60 行、超长就拆场景；智能体侧还有 `read_document(query=...)` 文内查找兜底 |
| **产物单文件 50 MiB 上限** | `ArtifactCollector` 的硬上限，且一次性读进内存。超大报表要在提示词里引导用户缩小范围 |
| **表名白名单只识别 `FROM`/`JOIN`** | `FROM a, b` 里的 `b` 会被漏掉。所以规范要求模板一律用显式 `JOIN`（自检会检查这条） |
| **沙箱需能访问数据库** | 默认允许出网，但「仅白名单出网」模式或 Docker `network_mode=none` 会挡掉。`checklist.md` 第 0 层专门验这个 |
| **脚本护栏是第二道防线** | 定位是「防模型写错」，不是「防提权」。**第一道防线是数据库侧的只读账号** |
| **白名单默认放行任意表** | 不填 `REPORT_DB_ALLOWED_TABLES` 时会带告警运行。生产建议填上 |
| **上游无冲突面** | 本目录全是新增文件、且在上游不存在的路径下。唯一接触的上游机制是「上传技能包」，不涉及改动 |

---

## 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-09-28 | 首版：文档规范、样板、Skill 包、Agent 提示词、分层自检清单与回归集 |
| 2026-09-28 | 修 `agent-prompt.md` 两处与实际文档冲突的示例：① `--params` 示例补上 `platform`，并写明**模板里每个 `:占位符` 都必须在 `--params` 里有键**，可选参数要显式传 `null`（脚本不给默认值，漏传报 `code: 2`）；② 删掉「结束日期要换算成次日」这条通用旧约定 —— 《达人订单报表》的 `:end_date` 是**含当日**、由 SQL 自己 `DATE_ADD` 补次日，照旧约定会整体差一天 |
| 2026-09-28 | 收口空参数语义：`:influencer` / `:platform` 传 `null` **或空串 `''`** 一律表示「不限」（模板守卫由 `IS NULL` 扩成 `IS NULL OR = ''`）。原写法只判 `IS NULL`，传空串时 `:platform` 会静默返回 **0 行**、`:influencer` 会静默返回**全量** —— 同样是「空」却一个空结果一个全结果。同步改《达人订单报表》四份模板与 `agent-prompt.md` |
| 2026-09-28 | 规范层收口两处通用坑（本次由《达人订单报表》暴露）：① **日期口径** —— `doc-spec.md` 原文只写「建议统一左闭右开」，现改为「SQL 层必须左闭右开」并给出**含当日**（推荐，参数与用户原话字面对应）与**传次日**两种合法写法，强调 §6 与 §7 必须一致，否则整体差一天且不报错；模板骨架相应改为含当日写法。② **可选参数** —— 新增「不适用时传什么、空值等价于什么」两条要求，并点名「必填：否」≠「可以省略这个键」（脚本不补默认值）。`samples/达人订单报表.md` **不动**：它要被 `selftest.py` 拿到 sqlite 上实跑，而 `DATE_ADD` 是 MySQL 方言 |
