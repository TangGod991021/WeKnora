#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_report.py —— 报表查询执行器（WeKnora Skill: order-report）

把「报表文档」里渲染好的 SELECT 语句跑到只读数据库上，结果写成 xlsx，
落到沙箱产物目录（$WEKNORA_SKILL_OUTPUT_DIR），由 WeKnora 的产物链路自动
收集并生成用户可下载的卡片。

调用方式（注意用 $WEKNORA_SKILL_DIR 定位脚本，见下方「为什么」）::

    python "$WEKNORA_SKILL_DIR/scripts/run_report.py" \
        --sql-file /workspace/query.sql \
        --params '{"start_date": "2026-08-01", "end_date": "2026-09-01"}' \
        --out "达人订单报表_20260801_20260831.xlsx" \
        --sheet 明细 \
        --headers '{"order_id": "订单号", "pay_amount": "支付金额"}'

为什么是 $WEKNORA_SKILL_DIR 而不是相对路径：已安装的技能执行时 WorkDir 是
/workspace，不是技能自己的目录（internal/agent/skills/manager.go:33）。技能
自带的脚本必须用这个环境变量定位。sql 文件则建议放 /workspace，因为那是
agent 用 write_sandbox_file 能写的地方。

stdout 最后一行是给 agent 解析的 JSON 结果；所有过程日志走 stderr，因此调用方
可以先读整个输出、取最后一行 JSON。退出码：
    0 成功 / 2 配置或凭证缺失 / 3 SQL 被安全策略拒绝 / 4 数据库错误 / 5 写文件失败
"""

from __future__ import annotations

import argparse
import datetime as _dt
import decimal
import json
import os
import re
import sys
import time

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_SQL_REJECTED = 3
EXIT_DB_ERROR = 4
EXIT_WRITE_ERROR = 5

DEFAULT_MAX_ROWS = 50000
DEFAULT_TIMEOUT_MS = 30000
DEFAULT_SHEET = "明细"

# 长数字（订单号、达人人称 ID）超过这个值就按文本写，否则 Excel 会显示成
# 科学计数法。2**53 是 double 能精确表示整数的上限，超过必然失真。
_INT_SAFE_LIMIT = 2 ** 53

# 报表文件名里不该出现的字符：空格和括号会让 agent 的 `sandbox:<文件名>`
# 引用产生歧义（prompts.go 的产物引用规范明确要求文件名不含空格与括号）。
_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|()\[\]{}\s]+')


# --------------------------------------------------------------------------
# 错误
# --------------------------------------------------------------------------

class ReportError(Exception):
    """带退出码的业务错误。message 会原样进 stdout 的 JSON，供 agent 读。"""

    def __init__(self, code: int, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def log(msg: str) -> None:
    """过程日志一律走 stderr，保证 stdout 只有最后一行 JSON。"""
    print(msg, file=sys.stderr, flush=True)


def emit(payload: dict) -> None:
    """stdout 输出结果 JSON —— 这是给 agent 的唯一机读出口。"""
    print(json.dumps(payload, ensure_ascii=False), flush=True)


# --------------------------------------------------------------------------
# 第一层：SQL 安全校验
#
# 校验全部在「骨架串」(skeleton) 上做：把字符串字面量内容、注释内容替换成
# 等长空格，保留引号/标记本身。这样
#   - `'12:30'` 里的冒号不会被当成参数占位符
#   - `WHERE note = 'a;b'` 里的分号不会被当成多语句
#   - `-- drop table` 注释里的关键字不会误判
# 骨架串与原串**等长且逐字符对齐**，所以校验得到的偏移量可以直接用来改写原串。
# --------------------------------------------------------------------------

_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "grant", "revoke", "replace", "merge", "call", "rename", "prepare",
    "deallocate", "handler", "lock", "unlock", "use", "set", "into",
    "outfile", "dumpfile", "load_file", "import",
)

# 会阻塞/放大负载的函数：报表查询不需要，出现即为误用或攻击
_FORBIDDEN_FUNCS = ("sleep", "benchmark", "pg_sleep", "get_lock", "release_lock")

_TABLE_RE = re.compile(
    r'\b(?:from|join)\s+[`"\[]?([A-Za-z_][A-Za-z0-9_$]*)'
    r'(?:\s*\.\s*[`"\[]?([A-Za-z_][A-Za-z0-9_$]*))?',
    re.IGNORECASE,
)

# 尾部的简单 LIMIT，用于把过大的行数上限压回去。偏移量在原串/骨架串上都成立。
_TRAILING_LIMIT_RE = re.compile(r'\blimit\s+(\d+)\s*;?\s*$', re.IGNORECASE)

# 参数占位符 :name。前面不能是 : 或单词字符（避开 PG 的 :: 转型与 12:30），
# 后面不能紧跟 : （避开 ::）。
_PARAM_RE = re.compile(r'(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)(?![:\w])')

# 参数值的日期/时间格式白名单 —— 只有长得像日期时间的字符串才允许作为日期参数传入
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_DATETIME_RE = re.compile(r'^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$')


def skeletonize(sql: str) -> str:
    """把字符串字面量内容与整段注释抹成空格，长度与原文严格一致。

    抹而不是删除，是为了让所有基于偏移量的正则校验与改写都能直接作用在原串
    上（参数替换、LIMIT 压制都依赖这一点）。

    注释是**连标记一起抹掉**的，不只是抹内容：否则 `-- 说明\\nSELECT ...` 这样
    以注释开头的模板，骨架串会残留 `--`，让「必须以 SELECT 开头」的判定失败。
    报表模板普遍带首行注释，这不是边角情况。
    """
    out = list(sql)
    n = len(sql)
    i = 0
    while i < n:
        ch = sql[i]

        # 行注释 -- ：按 MySQL 规则要求 -- 后是空白或行尾。
        # 这样 `a--b` 不会被误判成注释（若误判，注释里的关键字会被跳过，形成漏检）。
        if ch == "-" and i + 1 < n and sql[i + 1] == "-" and (i + 2 >= n or sql[i + 2] in " \t\r\n"):
            j = i
            while j < n and sql[j] not in "\r\n":
                out[j] = " "
                j += 1
            i = j
            continue

        # 行注释 # ：只在行首或空白之后才当注释（PG 里 # 可能是标识符的一部分）
        if ch == "#":
            prev = sql[i - 1] if i > 0 else "\n"
            if prev in " \t\r\n":
                j = i
                while j < n and sql[j] not in "\r\n":
                    out[j] = " "
                    j += 1
                i = j
                continue
            i += 1
            continue

        # 块注释 /* ... */（含标记一起抹）
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = i + 2
            while j + 1 < n and not (sql[j] == "*" and sql[j + 1] == "/"):
                j += 1
            end = min(j + 2, n)
            for k in range(i, end):
                out[k] = " "
            i = end
            continue

        # 字符串 / 标识符：'单引号' "双引号" `反引号`
        if ch in ("'", '"', "`"):
            quote = ch
            j = i + 1
            while j < n:
                c = sql[j]
                if c == "\\" and quote != "`":       # 反斜杠转义（MySQL）
                    out[j] = " "
                    if j + 1 < n:
                        out[j + 1] = " "
                    j += 2
                    continue
                if c == quote:
                    if j + 1 < n and sql[j + 1] == quote:   # 双写转义 ''
                        out[j] = " "
                        out[j + 1] = " "
                        j += 2
                        continue
                    break
                out[j] = " "
                j += 1
            i = j + 1
            continue

        i += 1
    return "".join(out)


def validate_sql(sql: str) -> str:
    """校验并返回骨架串。不通过就抛 ReportError(EXIT_SQL_REJECTED)。"""
    skeleton = skeletonize(sql)
    stripped = skeleton.strip()

    if not stripped:
        raise ReportError(EXIT_SQL_REJECTED, "SQL 是空的。")

    # 只允许单条语句：去掉一个结尾分号后，不允许再出现分号
    probe = stripped
    if probe.endswith(";"):
        probe = probe[:-1]
    if ";" in probe:
        raise ReportError(
            EXIT_SQL_REJECTED,
            "只允许单条 SQL 语句。检测到语句分隔符 ';'，多语句（含第二条 SELECT）一律拒绝。",
        )

    # 必须以 SELECT / WITH 开头
    head = probe.lstrip("( \t\r\n")
    first = re.match(r"([A-Za-z_]+)", head)
    if not first or first.group(1).lower() not in ("select", "with"):
        raise ReportError(
            EXIT_SQL_REJECTED,
            "只允许 SELECT 查询。语句必须以 SELECT 或 WITH 开头，"
            f"实际以 {first.group(1) if first else '(无法识别)'!r} 开头。",
        )

    lowered = probe.lower()

    found = [kw for kw in _FORBIDDEN_KEYWORDS if re.search(r"\b" + kw + r"\b", lowered)]
    if found:
        raise ReportError(
            EXIT_SQL_REJECTED,
            "SQL 含被禁止的关键字：" + ", ".join(sorted(set(found))) +
            "。本工具只跑只读报表查询。",
        )

    for fn in _FORBIDDEN_FUNCS:
        if re.search(r"\b" + fn + r"\s*\(", lowered):
            raise ReportError(EXIT_SQL_REJECTED, f"SQL 调用了被禁止的函数 {fn}()。")

    if re.search(r"\bfor\s+update\b", lowered) or re.search(r"\block\s+in\s+share\b", lowered):
        raise ReportError(EXIT_SQL_REJECTED, "SQL 含加锁读取（FOR UPDATE / LOCK IN SHARE MODE），报表查询不允许加锁。")

    return skeleton


def extract_tables(skeleton: str) -> list:
    """从骨架串里粗略提取被引用的表名（含 schema. 前缀时返回两段拼接形式）。"""
    tables = []
    for m in _TABLE_RE.finditer(skeleton):
        first, second = m.group(1), m.group(2)
        name = f"{first}.{second}" if second else first
        tables.append(name.lower())
    return tables


def enforce_table_whitelist(skeleton: str, whitelist: list) -> None:
    """白名单非空时，拒绝引用名单外的表。"""
    if not whitelist:
        return
    allowed = {t.lower() for t in whitelist}
    bare_allowed = {t.split(".")[-1] for t in allowed}
    for table in extract_tables(skeleton):
        if table in allowed or table.split(".")[-1] in bare_allowed:
            continue
        raise ReportError(
            EXIT_SQL_REJECTED,
            f"SQL 引用了白名单外的表 {table!r}。允许的表：{', '.join(sorted(allowed))}。",
        )


def enforce_row_cap(sql: str, skeleton: str, max_rows: int) -> tuple:
    """压制行数：无 LIMIT 就外包一层 LIMIT，有且过大就改小。

    外包一层（SELECT * FROM ( ... ) AS _limited LIMIT n）而不是往原句尾巴追加，
    是为了不破坏 GROUP BY / ORDER BY / UNION 的语义。返回值 (新 SQL, 新骨架, 说明)。
    """
    match = _TRAILING_LIMIT_RE.search(skeleton)
    if match:
        current = int(match.group(1))
        if current <= max_rows:
            return sql, skeleton, f"保留原有 LIMIT {current}"
        # 只替换数字本身，偏移量在骨架与原串上一致
        start, end = match.start(1), match.end(1)
        new_sql = sql[:start] + str(max_rows) + sql[end:]
        new_skel = skeleton[:start] + " " * len(str(max_rows)) + skeleton[end:]
        # 骨架里数字被空格替代不便于复检，直接把长度对齐重新生成
        new_skel = skeletonize(new_sql)
        return new_sql, new_skel, f"原 LIMIT {current} 超过上限，已压到 {max_rows}"

    wrapped = f"SELECT * FROM (\n{sql.rstrip().rstrip(';')}\n) AS _weknora_limited LIMIT {max_rows}"
    return wrapped, skeletonize(wrapped), f"原语句无 LIMIT，已外包 LIMIT {max_rows}"


# --------------------------------------------------------------------------
# 第二层：参数绑定
#
# 绝不字符串拼接。所有值都先做类型校验，再用驱动自带的转义函数生成字面量。
# --------------------------------------------------------------------------

class ParamBinder:
    """按驱动绑定 :name 占位符。子类实现 quote_value。"""

    def __init__(self, params: dict):
        self.params = params
        self.used = set()

    def quote_value(self, value):  # pragma: no cover - 由子类覆盖
        raise NotImplementedError

    def render(self, sql: str, skeleton: str) -> str:
        matches = list(_PARAM_RE.finditer(skeleton))
        if not matches:
            if self.params:
                log("提示：SQL 里没有 :参数 占位符，--params 里的值被忽略。")
            return sql

        missing = []
        for m in matches:
            name = m.group(1)
            if name not in self.params:
                missing.append(name)
        if missing:
            raise ReportError(
                EXIT_CONFIG,
                "缺少参数：" + ", ".join(sorted(set(missing))) +
                "。请先向用户确认这些条件，不要自行假设默认值。",
                missing=sorted(set(missing)),
            )

        # 从后往前替换，避免前一次替换改变后面匹配的偏移量
        out = sql
        for m in reversed(matches):
            name = m.group(1)
            value = self.params[name]
            self.used.add(name)
            out = out[:m.start()] + self.quote_value(value) + out[m.end():]
        unused = set(self.params) - self.used
        if unused:
            log("提示：参数 " + ", ".join(sorted(unused)) + " 未被 SQL 使用（可能是模板没用上，或名字拼错）。")
        return out

    # ---- 值校验与类型约定（两个驱动共用）----

    @staticmethod
    def validate_value(name: str, value):
        """在转义之前先收紧取值范围，把明显不对劲的输入挡在库外。"""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float, decimal.Decimal)):
            return value
        if isinstance(value, (list, tuple)):
            return [ParamBinder.validate_value(name, v) for v in value]
        if isinstance(value, str):
            s = value.strip()
            if len(s) > 512:
                raise ReportError(EXIT_CONFIG, f"参数 {name} 过长（{len(s)} 字符，上限 512）。")
            return s
        raise ReportError(EXIT_CONFIG, f"参数 {name} 的类型 {type(value).__name__} 不受支持。")


def _render_literal(value, quote_scalar) -> str:
    """把一个已校验的值渲染成 SQL 字面量（与驱动无关的骨架）。

    字符串一律交给驱动的转义函数生成字面量，绝不做字符串拼接。
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    if isinstance(value, (list, tuple)):
        if not value:
            # 空列表在 IN (...) 里是语法错误，渲染成恒假，语义上最接近「无匹配」
            return "(NULL)"
        return "(" + ", ".join(_render_literal(v, quote_scalar) for v in value) + ")"
    return quote_scalar(str(value))


def load_params(raw: str) -> dict:
    """--params 接受内联 JSON，或 @文件路径（长参数走文件，避开命令长度上限）。"""
    if not raw:
        return {}
    text = raw
    if raw.startswith("@"):
        path = raw[1:]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            raise ReportError(EXIT_CONFIG, f"读取参数文件失败：{exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportError(EXIT_CONFIG, f"--params 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ReportError(EXIT_CONFIG, "--params 必须是 JSON 对象，例如 '{\"start_date\": \"2026-08-01\"}'。")
    return data


def load_json_arg(raw: str, what: str) -> dict:
    if not raw:
        return {}
    text = raw
    if raw.startswith("@"):
        try:
            with open(raw[1:], "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            raise ReportError(EXIT_CONFIG, f"读取 {what} 文件失败：{exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportError(EXIT_CONFIG, f"{what} 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ReportError(EXIT_CONFIG, f"{what} 必须是 JSON 对象（列名 → 表头）。")
    return data


# --------------------------------------------------------------------------
# 第三层：数据库后端
# --------------------------------------------------------------------------

class MysqlBackend:
    name = "mysql"

    def __init__(self, cfg: dict):
        try:
            import pymysql  # noqa: F401
        except ImportError as exc:
            raise ReportError(EXIT_CONFIG, "缺少 pymysql，请确认技能依赖已装进技能自己的 .venv。") from exc
        self._pymysql = pymysql
        self.cfg = cfg

    def _connect(self, timeout_ms: int):
        pymysql = self._pymysql
        kwargs = dict(
            host=self.cfg["host"],
            port=int(self.cfg["port"] or 3306),
            user=self.cfg["user"],
            password=self.cfg["password"],
            database=self.cfg["database"],
            charset=self.cfg.get("charset") or "utf8mb4",
            connect_timeout=max(3, timeout_ms // 1000),
            autocommit=True,
        )
        if self.cfg.get("ssl"):
            kwargs["ssl"] = {"ssl": {}}
        return pymysql.connect(**kwargs)

    def open(self, timeout_ms: int):
        try:
            conn = self._connect(timeout_ms)
        except Exception as exc:
            raise ReportError(EXIT_DB_ERROR, f"连接 MySQL 失败：{exc}") from exc
        cursor = conn.cursor()
        self._soft(cursor, "SET SESSION TRANSACTION READ ONLY")
        self._soft(cursor, f"SET SESSION MAX_EXECUTION_TIME = {timeout_ms}")
        return MysqlConn(conn, cursor)

    @staticmethod
    def _soft(cursor, stmt: str) -> None:
        """尽力执行；旧版本 MySQL / 代理不支持时降级并告警，不阻断查询。"""
        try:
            cursor.execute(stmt)
        except Exception as exc:  # noqa: BLE001 - 降级路径，故意吞掉
            log(f"警告：{stmt!r} 未生效（{exc}）。只读与超时依赖数据库侧账号权限兜底。")

    def quote_scalar(self, conn, value: str) -> str:
        return conn.escape(value)


class PostgresBackend:
    name = "postgres"

    def __init__(self, cfg: dict):
        try:
            import psycopg2  # noqa: F401
        except ImportError as exc:
            raise ReportError(
                EXIT_CONFIG, "缺少 psycopg2-binary，请确认技能依赖已装进技能自己的 .venv。"
            ) from exc
        self._psycopg2 = psycopg2
        self.cfg = cfg

    def open(self, timeout_ms: int):
        psycopg2 = self._psycopg2
        try:
            conn = psycopg2.connect(
                host=self.cfg["host"],
                port=int(self.cfg["port"] or 5432),
                user=self.cfg["user"],
                password=self.cfg["password"],
                dbname=self.cfg["database"],
                connect_timeout=max(3, timeout_ms // 1000),
                sslmode=self.cfg.get("sslmode") or "prefer",
                application_name="weknora-order-report",
            )
        except Exception as exc:
            raise ReportError(EXIT_DB_ERROR, f"连接 PostgreSQL 失败：{exc}") from exc
        conn.set_session(readonly=True, autocommit=True)
        cursor = conn.cursor()
        try:
            cursor.execute(f"SET statement_timeout = {timeout_ms}")
        except Exception as exc:  # noqa: BLE001
            log(f"警告：SET statement_timeout 未生效（{exc}）。")
        return PgConn(conn, cursor)

    def quote_scalar(self, conn, value: str) -> str:
        return conn.escape_literal(value)


class SqliteBackend:
    """本地自检 / 离线数据用。sqlite3 是标准库，因此不依赖任何外部服务，
    用来在验证脚本本身时把「查询 → xlsx」整条链路跑通。"""

    name = "sqlite"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def open(self, timeout_ms: int):
        import sqlite3

        path = self.cfg.get("database") or ":memory:"
        try:
            if path != ":memory:":
                # 以只读模式打开，物理上杜绝写操作
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            else:
                conn = sqlite3.connect(path)
            conn.execute("PRAGMA query_only = ON")
        except Exception as exc:
            raise ReportError(EXIT_DB_ERROR, f"打开 SQLite 失败：{exc}") from exc
        return SqliteConn(conn, conn.cursor())

    def quote_scalar(self, conn, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"


class _BaseConn:
    """把驱动差异收敛到 execute / fetch_many / escape 三个方法。"""

    def __init__(self, conn, cursor):
        self.conn = conn
        self.cursor = cursor

    def execute(self, sql: str) -> None:
        self.cursor.execute(sql)

    def fetch_many(self, limit: int):
        rows = self.cursor.fetchmany(limit)
        columns = [d[0] for d in (self.cursor.description or [])]
        return columns, rows

    def escape(self, value: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        try:
            self.cursor.close()
        finally:
            self.conn.close()


class MysqlConn(_BaseConn):
    def escape(self, value: str) -> str:
        return self.conn.escape(value)


class PgConn(_BaseConn):
    def escape(self, value: str) -> str:
        return self.conn.escape_literal(value)


class SqliteConn(_BaseConn):
    def escape(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"


BACKENDS = {
    "mysql": MysqlBackend,
    "mariadb": MysqlBackend,
    "postgres": PostgresBackend,
    "postgresql": PostgresBackend,
    "pg": PostgresBackend,
    "sqlite": SqliteBackend,
}


# --------------------------------------------------------------------------
# 第四层：xlsx 生成
# --------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    cleaned = _UNSAFE_FILENAME_RE.sub("_", (name or "").strip())
    cleaned = cleaned.strip("._")
    if not cleaned:
        cleaned = "report"
    if not cleaned.lower().endswith(".xlsx"):
        cleaned += ".xlsx"
    return cleaned


def _display_width(text: str) -> int:
    """估算列宽：CJK 字符按 2 个字符宽计。"""
    width = 0
    for ch in text:
        width += 2 if ord(ch) > 0x2E80 else 1
    return width


def normalize_cell(value):
    """把驱动返回的值转成 openpyxl 能写的类型，并保护长数字。

    订单号这类字段在库里是 BIGINT，pymysql 会返回 int，直接写进 Excel 会变成
    科学计数法（1.23457E+18）且丢失精度。因此超过 double 精确整数范围的值
    一律转字符串。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        if abs(value) > _INT_SAFE_LIMIT or len(str(abs(value))) > 15:
            return str(value)
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def write_sheet(workbook, title: str, columns, rows, headers: dict) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    sheet = workbook.create_sheet(title=title[:31] or "Sheet")

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="4F6BED")
    header_align = Alignment(horizontal="center", vertical="center")

    # 表头：优先用 --headers 给的中文表头，缺省回落 SQL 别名
    for idx, col in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=idx, value=headers.get(col, col))
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align

    widths = [_display_width(str(headers.get(c, c))) for c in columns]
    text_columns = set()

    for r_idx, row in enumerate(rows, start=2):
        for c_idx, value in enumerate(row, start=1):
            norm = normalize_cell(value)
            cell = sheet.cell(row=r_idx, column=c_idx, value=norm)
            if isinstance(norm, str) and norm.isdigit() and len(norm) > 15:
                # 长数字串按文本写，锁死显示格式，避免 Excel 自作主张
                cell.number_format = "@"
                text_columns.add(c_idx)
            if isinstance(norm, _dt.datetime):
                cell.number_format = "yyyy-mm-dd hh:mm:ss"
            elif isinstance(norm, _dt.date):
                cell.number_format = "yyyy-mm-dd"
            text = "" if norm is None else str(norm)
            widths[c_idx - 1] = max(widths[c_idx - 1], min(_display_width(text), 60))

    if rows:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"
    else:
        sheet.freeze_panes = "A2"

    for idx, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(idx)].width = min(max(width + 2, 8), 62)

    for c_idx in text_columns:
        letter = get_column_letter(c_idx)
        for r_idx in range(2, len(rows) + 2):
            sheet[f"{letter}{r_idx}"].number_format = "@"


def write_meta_sheet(workbook, info: dict) -> None:
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    sheet = workbook.create_sheet(title="查询说明")
    sheet.cell(row=1, column=1, value="项").font = Font(bold=True)
    sheet.cell(row=1, column=2, value="值").font = Font(bold=True)

    row = 2
    for key, value in info.items():
        sheet.cell(row=row, column=1, value=key)
        cell = sheet.cell(row=row, column=2, value=value)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        row += 1

    sheet.column_dimensions["A"].width = 18
    sheet.column_dimensions[get_column_letter(2)].width = 100


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def read_sql_file(path: str, what: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise ReportError(EXIT_CONFIG, f"读取 {what} 失败（{path}）：{exc}") from exc


def resolve_config(args) -> dict:
    """连接配置只来自环境变量 —— 凭证永不写进报表文档。"""
    driver = (args.driver or os.getenv("REPORT_DB_DRIVER") or "mysql").strip().lower()
    if driver not in BACKENDS:
        raise ReportError(
            EXIT_CONFIG,
            f"不支持的数据源驱动 {driver!r}。可选：{', '.join(sorted(BACKENDS))}。",
        )

    if driver == "sqlite":
        return {"driver": driver, "database": args.sqlite_path or os.getenv("REPORT_DB_NAME") or ":memory:"}

    missing = [
        name for name in ("REPORT_DB_HOST", "REPORT_DB_USER", "REPORT_DB_PASSWORD", "REPORT_DB_NAME")
        if not os.getenv(name)
    ]
    if missing:
        raise ReportError(
            EXIT_CONFIG,
            "缺少数据库环境变量：" + ", ".join(missing) +
            "。请在「设置 → 沙箱密钥」里填入，或在 shell_exec 的 env 参数里就地传入。",
            missing=missing,
        )
    return {
        "driver": driver,
        "host": os.getenv("REPORT_DB_HOST"),
        "port": os.getenv("REPORT_DB_PORT"),
        "user": os.getenv("REPORT_DB_USER"),
        "password": os.getenv("REPORT_DB_PASSWORD"),
        "database": os.getenv("REPORT_DB_NAME"),
        "charset": os.getenv("REPORT_DB_CHARSET"),
        "ssl": (os.getenv("REPORT_DB_SSL") or "").lower() in ("1", "true", "yes", "required"),
        "sslmode": os.getenv("REPORT_DB_SSLMODE"),
    }


def prepare_statement(raw_sql: str, params: dict, whitelist: list, max_rows: int, binder_factory):
    """校验 → 白名单 → 绑定参数 → 压制行数。返回 (最终 SQL, 说明列表, 实际用到的参数)。"""
    notes = []
    skeleton = validate_sql(raw_sql)

    if whitelist:
        enforce_table_whitelist(skeleton, whitelist)
        notes.append(f"表名白名单校验通过（{len(whitelist)} 张表）")
    else:
        notes.append("未配置表名白名单（允许任意表）；建议用 --allowed-tables 或 REPORT_DB_ALLOWED_TABLES 收紧")

    binder = binder_factory(params)
    rendered = binder.render(raw_sql, skeleton)
    if binder.used:
        notes.append("已绑定参数：" + ", ".join(sorted(binder.used)))
    notes.append("实际执行 SQL：\n" + rendered)

    skeleton2 = skeletonize(rendered)
    final_sql, _, cap_note = enforce_row_cap(rendered, skeleton2, max_rows)
    notes.append(cap_note)
    return final_sql, notes, sorted(binder.used)


def resolve_output(args) -> str:
    out_dir = os.getenv("WEKNORA_SKILL_OUTPUT_DIR") or args.out_dir or os.getcwd()
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise ReportError(EXIT_WRITE_ERROR, f"创建输出目录失败（{out_dir}）：{exc}") from exc
    return os.path.join(out_dir, sanitize_filename(args.out or args.sheet or "report"))


def parse_whitelist(args) -> list:
    raw = args.allowed_tables or os.getenv("REPORT_DB_ALLOWED_TABLES") or ""
    return [t.strip() for t in raw.split(",") if t.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="按报表文档的 SQL 模板查库并生成 xlsx（WeKnora order-report 技能）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--sql-file", required=True, help="渲染好参数的 SELECT 语句文件（放 /workspace）")
    p.add_argument("--params", default="{}", help="参数 JSON，或 @文件路径")
    p.add_argument("--out", help="输出 xlsx 文件名（会自动清洗空格与括号）")
    p.add_argument("--out-dir", help="输出目录；默认用 $WEKNORA_SKILL_OUTPUT_DIR")
    p.add_argument("--sheet", default=DEFAULT_SHEET, help=f"明细表工作表名，默认 {DEFAULT_SHEET}")
    p.add_argument("--headers", default="", help='中文表头 JSON：{"order_id": "订单号"}，或 @文件路径')
    p.add_argument("--summary-sql-file", help="可选的汇总查询，结果写入「汇总」工作表")
    p.add_argument("--allowed-tables", help="逗号分隔的表名白名单；未给出时读 REPORT_DB_ALLOWED_TABLES")
    p.add_argument("--max-rows", type=int, default=int(os.getenv("REPORT_DB_MAX_ROWS") or DEFAULT_MAX_ROWS))
    p.add_argument("--timeout-ms", type=int, default=int(os.getenv("REPORT_DB_TIMEOUT_MS") or DEFAULT_TIMEOUT_MS))
    p.add_argument("--driver", help="覆盖 REPORT_DB_DRIVER（mysql/postgres/sqlite）")
    p.add_argument("--sqlite-path", help="sqlite 驱动下的数据库文件路径（本地自检用）")
    p.add_argument("--dry-run", action="store_true", help="只做校验与参数绑定，不连库、不写文件")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    started = time.time()

    try:
        cfg = resolve_config(args)
        params = load_params(args.params)
        headers = load_json_arg(args.headers, "--headers")
        whitelist = parse_whitelist(args)

        raw_sql = read_sql_file(args.sql_file, "--sql-file")

        backend = BACKENDS[cfg["driver"]](cfg)
        conn = None if args.dry_run else backend.open(args.timeout_ms)
        try:
            binder_factory = lambda p: (  # noqa: E731
                _make_binder(backend, conn, p)
            )
            final_sql, notes, used_params = prepare_statement(
                raw_sql, params, whitelist, args.max_rows, binder_factory
            )

            if args.dry_run:
                emit({
                    "ok": True,
                    "dry_run": True,
                    "driver": cfg["driver"],
                    "rendered_sql": final_sql,
                    "used_params": used_params,
                    "notes": notes,
                })
                return EXIT_OK

            conn.execute(final_sql)
            columns, rows = conn.fetch_many(args.max_rows)
            log(f"查询返回 {len(rows)} 行 / {len(columns)} 列")

            summary = None
            if args.summary_sql_file:
                summary = _run_summary(args, conn, backend, params, whitelist, headers)
        finally:
            if conn is not None:
                conn.close()

        path = resolve_output(args)

        from openpyxl import Workbook

        workbook = Workbook()
        workbook.remove(workbook.active)          # 去掉默认 Sheet
        write_sheet(workbook, args.sheet, columns, rows, headers)
        if summary is not None:
            write_sheet(workbook, "汇总", summary[0], summary[1], headers)

        write_meta_sheet(workbook, {
            "报表文件": os.path.basename(path),
            "生成时间": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "数据源": f"{cfg['driver']}://{cfg.get('host', '(local)')}/{cfg.get('database', '')}",
            "明细行数": len(rows),
            "列数": len(columns),
            "参数": json.dumps(params, ensure_ascii=False, sort_keys=True),
            "校验与执行说明": "\n".join(notes),
        })

        try:
            workbook.save(path)
        except OSError as exc:
            raise ReportError(EXIT_WRITE_ERROR, f"写入 xlsx 失败（{path}）：{exc}") from exc

        emit({
            "ok": True,
            "file": os.path.basename(path),
            "path": path,
            "rows": len(rows),
            "columns": columns,
            "sheets": [args.sheet] + (["汇总"] if summary is not None else []) + ["查询说明"],
            "used_params": used_params,
            "elapsed_ms": int((time.time() - started) * 1000),
        })
        return EXIT_OK

    except ReportError as exc:
        log(f"失败：{exc.message}")
        emit({"ok": False, "error": exc.message, "code": exc.code, **exc.extra})
        return exc.code
    except Exception as exc:  # noqa: BLE001 - 兜底：把异常变成可读 JSON，别让 agent 只看到堆栈
        message = f"{type(exc).__name__}: {exc}"
        log(f"未预期错误：{message}")
        emit({"ok": False, "error": message, "code": EXIT_DB_ERROR})
        return EXIT_DB_ERROR


def _make_binder(backend, conn, params: dict) -> ParamBinder:
    """生产绑定器：用驱动的转义函数生成字面量；dry-run 时用标准单引号转义。"""
    if conn is None:
        class _Dry(ParamBinder):
            def quote_value(self, value):
                value = ParamBinder.validate_value("<param>", value)
                return _render_literal(value, lambda s: "'" + s.replace("'", "''") + "'")
        return _Dry(params)

    class _Live(ParamBinder):
        def quote_value(self, value):
            value = ParamBinder.validate_value("<param>", value)
            return _render_literal(value, conn.escape)

    return _Live(params)


def _run_summary(args, conn, backend, params, whitelist, headers):
    raw = read_sql_file(args.summary_sql_file, "--summary-sql-file")
    final_sql, _, _ = prepare_statement(
        raw, params, whitelist, args.max_rows, lambda p: _make_binder(backend, conn, p)
    )
    conn.execute(final_sql)
    columns, rows = conn.fetch_many(args.max_rows)
    log(f"汇总查询返回 {len(rows)} 行 / {len(columns)} 列")
    return columns, rows


if __name__ == "__main__":
    sys.exit(main())
