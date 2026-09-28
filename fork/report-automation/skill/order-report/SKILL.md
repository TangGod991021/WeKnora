---
name: order-report
description: 查询达人订单业务库并把结果导出成 xlsx 报表文件。当用户要求导出/下载/生成达人订单明细、按时间段或达人筛选订单、要 Excel 版本的订单数据、统计某达人某周期的出单情况时使用。需要报表文档里的 SQL 模板和参数契约作为输入。
---

# 达人订单报表执行器

把「报表文档」里定义好的 SELECT 模板跑在只读业务库上，结果写成 xlsx 落到沙箱产物
目录，由 WeKnora 的产物链路自动收集成用户可下载的文件。

**这个技能只负责执行，不负责决定查什么。** 报表的库名、表名、字段口径、SQL 模板、
参数契约一律来自知识库里的报表文档；本技能不接受「自己编一条 SQL」的用法。

## 工作流程

1. 先从知识库读出报表文档，确定用哪个查询模板（文档 §7 的某个小节）、需要哪些参数
   （文档 §6 参数契约）。
2. 把渲染好的 SQL 用 `write_sandbox_file` 写到 `/workspace/query.sql`。
   **不要把 SQL 内联进 shell 命令** —— heredoc 会在命令长度上限处被截断，引号也会被
   搞坏。走文件是唯一可靠的方式。
3. 调本技能执行（注意用 `$WEKNORA_SKILL_DIR`，见下方「为什么」）。
4. 读返回的 JSON，把文件引用写进回答。

## 调用方式

```bash
python "$WEKNORA_SKILL_DIR/scripts/run_report.py" \
  --sql-file /workspace/query.sql \
  --params '{"start_date": "2026-08-01", "end_date": "2026-09-01", "influencer": "张三"}' \
  --out "达人订单报表_20260801_20260831.xlsx" \
  --sheet 明细 \
  --headers '{"order_id": "订单号", "pay_amount": "支付金额"}'
```

传给 `shell_exec` 时要带 `skill_name`，凭证才会被注入：

```
shell_exec(
  skill_name="order-report",
  command='python "$WEKNORA_SKILL_DIR/scripts/run_report.py" --sql-file /workspace/query.sql --params \'{...}\' --out "达人订单报表_20260801_20260831.xlsx" --sheet 明细 --headers \'{...}\''
)
```

**为什么必须用 `$WEKNORA_SKILL_DIR`**：已安装技能执行时的工作目录是 `/workspace`，
不是技能自己的目录，所以 `python scripts/run_report.py` 这样的相对路径会找不到文件。
技能自带的一切资源都要通过这个环境变量定位。

> 环境变量 `WEKNORA_SKILL_DIR` 由 WeKnora 在执行时注入，指向技能目录本身；同批注入的
> 还有 `WEKNORA_SKILL_OUTPUT_DIR`（产物目录，脚本默认往这里写）、
> `WEKNORA_SKILL_HISTORY_ROOT`、`WEKNORA_SESSION_INPUT_DIR`。这些名字由平台保留，
> 不要覆盖。

## 参数

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `--sql-file` | 是 | 已按模板渲染好参数的 SQL 文件。**留 `:参数` 占位符也可以**，本脚本会用驱动自带转义来绑定，比你自己拼字符串安全 |
| `--params` | 否 | 参数 JSON，或 `@参数文件路径`。键名必须与 SQL 里的 `:占位符` 一致 |
| `--out` | 否 | 输出文件名。空格与括号会被自动清洗成 `_`，保证 `sandbox:` 引用无歧义 |
| `--out-dir` | 否 | 输出目录，默认 `$WEKNORA_SKILL_OUTPUT_DIR` |
| `--sheet` | 否 | 明细工作表名，默认 `明细` |
| `--headers` | 否 | 中文表头映射 `{"列名": "表头"}`，或 `@文件路径`。取自报表文档 §7 的「输出列」表 |
| `--summary-sql-file` | 否 | 可选的汇总查询，结果写到「汇总」工作表 |
| `--allowed-tables` | 否 | 表名白名单，逗号分隔。未给出时读 `REPORT_DB_ALLOWED_TABLES`；都不给则放行任意表（会在结果说明里告警） |
| `--max-rows` | 否 | 行数上限，默认 50000 |
| `--timeout-ms` | 否 | 查询超时毫秒数，默认 30000 |
| `--dry-run` | 否 | 只做校验与参数绑定并打印最终 SQL，不连库、不写文件。**排查问题时先用它** |

## 环境变量（数据库凭证）

凭证由平台加密存储、按次注入，**绝不能写进报表文档**（文档会进向量库，任何能检索该
知识库的用户都可能把连接串问出来）。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `REPORT_DB_HOST` | 是 | 数据库主机 |
| `REPORT_DB_PORT` | 是 | 端口（MySQL 默认 3306，PostgreSQL 默认 5432） |
| `REPORT_DB_USER` | 是 | **只读账号** |
| `REPORT_DB_PASSWORD` | 是 | 只读账号密码 |
| `REPORT_DB_NAME` | 是 | 库名 |
| `REPORT_DB_DRIVER` | 否 | `mysql`（默认）/ `postgres` |
| `REPORT_DB_ALLOWED_TABLES` | 否 | 表名白名单，逗号分隔 |
| `REPORT_DB_MAX_ROWS` | 否 | 行数上限，被 `--max-rows` 覆盖 |
| `REPORT_DB_TIMEOUT_MS` | 否 | 超时毫秒数，被 `--timeout-ms` 覆盖 |

`REPORT_DB_PASSWORD` 这类变量名必须**字面出现在包内某个文件里**才会被登记成声明
（平台的装包校验会逐文件匹配，脚本里 `os.environ.get("REPORT_DB_HOST")` 这样的读取
就是匹配依据）。所以不要用变量名拼接的方式读环境变量。

## 安全约束（脚本内建，不在模型控制之下）

1. **只允许单条 SELECT** —— 语句必须以 `SELECT` 或 `WITH` 开头；出现语句分隔符、或命中
   `INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/GRANT/REPLACE/MERGE/INTO/OUTFILE/
   LOAD_FILE/USE/SET/CALL` 等关键字一律拒绝。
2. **校验在「骨架串」上做** —— 字符串字面量与注释先被抹掉再校验，所以
   `WHERE name = 'a;b'`、`-- drop table` 这类不会误判，而注释里藏关键字也不会漏判。
3. **加锁读取被拒** —— `FOR UPDATE` / `LOCK IN SHARE MODE` 不是报表语义。
4. **危险函数被拒** —— `sleep()` / `benchmark()` / `pg_sleep()` / `get_lock()`。
5. **行数强制压制** —— 无 `LIMIT` 时外包一层 `LIMIT`，过大则压到上限；取数时还按
   `--max-rows` 用 `fetchmany` 兜底，即使 SQL 写法绕开了重写也不会拉爆内存。
6. **只读会话** —— 连接后设置只读事务（MySQL `SET SESSION TRANSACTION READ ONLY`，
   PostgreSQL `set_session(readonly=True)`，SQLite `PRAGMA query_only=ON`）。
7. **参数不做字符串拼接** —— `:占位符` 一律由驱动自带的转义函数生成字面量。
8. **失败信息可读** —— 所有错误都变成 stdout 上的 JSON，模型能读懂并纠正，不会被堆栈
   带偏。

**这些都是第二道防线。第一道防线是数据库侧的只读账号** —— 请务必用只读账号，脚本护栏
的定位是「防模型写错」而不是「防提权」。

## 返回值

stdout 最后一行是 JSON（过程日志走 stderr）：

```json
{"ok": true, "file": "达人订单报表_20260801_20260831.xlsx", "path": "/workspace/output/...",
 "rows": 128, "columns": ["order_id", "order_time", "..."],
 "sheets": ["明细", "汇总", "查询说明"], "used_params": ["end_date", "influencer", "start_date"],
 "elapsed_ms": 412}
```

失败时：

```json
{"ok": false, "error": "缺少参数：end_date。请先向用户确认这些条件，不要自行假设默认值。", "code": 2}
```

退出码：`0` 成功 / `2` 配置或凭证缺失 / `3` SQL 被安全策略拒绝 / `4` 数据库错误 /
`5` 写文件失败。

**拿到文件后**，务必用工具返回的 Output files 列表里的确切链接在回答里引用：
`![达人订单报表](sandbox:达人订单报表_20260801_20260831.xlsx)`。不要写
`/workspace/output/...` 这样的路径，也不要写裸文件名 —— 两者在浏览器里都解析不了。

`rows: 0` 时如实告诉用户「按此条件没有数据」，不要编造内容，也不要换个参数偷偷再查。

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `缺少数据库环境变量` | 凭证还没录。去「设置 → 沙箱密钥」填，或在 `shell_exec` 的 `env` 参数里就地传 |
| `只允许 SELECT 查询` / `含被禁止的关键字` | 报表文档里的模板本身有问题，或模型自行编了 SQL。回到文档 §7 用原模板 |
| `缺少参数：xxx` | 先向用户确认这个条件再查，不要假设默认值 |
| `白名单外的表` | 模板用了白名单没登记的表；确认是同一张表后补进 `REPORT_DB_ALLOWED_TABLES` |
| `连接 xxx 失败` | 沙箱到数据库的网络不通，或凭证错。先按自检清单第 0 层验证连通性 |
| 出来是空表但应该有数据 | 用 `--dry-run` 看实际执行的 SQL 和绑定后的参数，多半是日期区间边界写错 |
