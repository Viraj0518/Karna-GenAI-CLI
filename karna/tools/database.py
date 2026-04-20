"""Database connector tool — connect to SQLite, PostgreSQL, or MySQL databases.

Supports schema inspection, table listing, and SQL query execution
with read-only mode enforcement and result formatting as markdown tables.

SQLite uses the built-in ``sqlite3`` module. PostgreSQL and MySQL
require optional dependencies (``psycopg2-binary`` and ``pymysql``
respectively), installed via ``pip install karna[db]``.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any
from urllib.parse import urlparse

from karna.tools.base import BaseTool

_MAX_ROWS = 100

# Statements allowed in read-only mode (case-insensitive first keyword).
_READ_ONLY_PREFIXES = frozenset({"select", "describe", "show", "explain", "pragma"})

# Statements that mutate data — blocked in read-only mode.
_MUTATING_RE = re.compile(
    r"^\s*(insert|update|delete|drop|alter|create|truncate|replace|merge|grant|revoke)\b",
    re.IGNORECASE,
)


def _is_read_only_sql(sql: str) -> bool:
    """Return True if *sql* appears to be a read-only statement."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return True
    first_word = stripped.split()[0].lower()
    if first_word in _READ_ONLY_PREFIXES:
        return True
    if _MUTATING_RE.match(stripped):
        return False
    # Unknown statement — treat as potentially mutating.
    return False


def _format_markdown_table(columns: list[str], rows: list[tuple[Any, ...]]) -> str:
    """Format query results as a markdown table."""
    if not columns:
        return "(no columns)"
    if not rows:
        return _make_header(columns) + "\n\n(0 rows)"

    # Convert all values to strings
    str_rows = [[str(v) if v is not None else "NULL" for v in row] for row in rows]

    # Compute column widths
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, val in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(val))

    # Build header
    header = "| " + " | ".join(c.ljust(w) for c, w in zip(columns, widths)) + " |"
    separator = "| " + " | ".join("-" * w for w in widths) + " |"

    # Build rows
    lines = [header, separator]
    for row in str_rows:
        padded = []
        for i, val in enumerate(row):
            w = widths[i] if i < len(widths) else len(val)
            padded.append(val.ljust(w))
        lines.append("| " + " | ".join(padded) + " |")

    return "\n".join(lines)


def _make_header(columns: list[str]) -> str:
    """Build just the header + separator of a markdown table."""
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("-" * len(c) for c in columns) + " |"
    return f"{header}\n{separator}"


# ======================================================================= #
#  Connection wrappers
# ======================================================================= #


class _SQLiteConn:
    """Wrapper around sqlite3 to normalise the interface."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(self.path)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Not connected")
        return self._conn

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> tuple[list[str], list[tuple[Any, ...]]]:
        cur = self.conn.execute(sql, params)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = cur.fetchmany(_MAX_ROWS)
        # Commit after mutating statements so changes are persisted.
        if not _is_read_only_sql(sql):
            self._conn.commit()  # type: ignore[union-attr]
        return columns, rows

    def tables(self) -> list[tuple[str, int]]:
        cur = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        table_names = [row[0] for row in cur.fetchall()]
        result = []
        for tname in table_names:
            # Use parameterised identifier escaping — sqlite_master names
            # are trusted, but we quote them defensively.
            safe_name = tname.replace('"', '""')
            count_cur = self.conn.execute(f'SELECT COUNT(*) FROM "{safe_name}"')
            count = count_cur.fetchone()[0]
            result.append((tname, count))
        return result

    def schema(self, table: str) -> str:
        cur = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
        # Fallback: PRAGMA table_info
        safe_name = table.replace('"', '""')
        cur = self.conn.execute(f'PRAGMA table_info("{safe_name}")')
        cols = cur.fetchall()
        if not cols:
            return f"Table '{table}' not found."
        lines = [f"-- Columns for {table}:"]
        for col in cols:
            # col: (cid, name, type, notnull, dflt_value, pk)
            nullable = "" if col[3] else " NULL"
            pk = " PRIMARY KEY" if col[5] else ""
            lines.append(f"  {col[1]} {col[2]}{nullable}{pk}")
        return "\n".join(lines)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def db_type(self) -> str:
        return "sqlite"


class _PostgreSQLConn:
    """Wrapper around psycopg2 for PostgreSQL."""

    def __init__(self, connection_string: str) -> None:
        self._dsn = connection_string
        self._conn: Any = None

    def connect(self) -> None:
        try:
            import psycopg2
        except ImportError:
            try:
                import asyncpg as _  # noqa: F401

                raise ImportError(
                    "asyncpg is installed but this tool uses synchronous psycopg2. "
                    "Install psycopg2: pip install psycopg2-binary"
                ) from None
            except ImportError:
                raise ImportError(
                    "PostgreSQL support requires psycopg2. Install it: pip install psycopg2-binary"
                ) from None
        self._conn = psycopg2.connect(self._dsn)
        self._conn.autocommit = True

    @property
    def conn(self) -> Any:
        if self._conn is None:
            raise RuntimeError("Not connected")
        return self._conn

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> tuple[list[str], list[tuple[Any, ...]]]:
        cur = self.conn.cursor()
        cur.execute(sql, params if params else None)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = cur.fetchmany(_MAX_ROWS) if cur.description else []
        cur.close()
        return columns, rows

    def tables(self) -> list[tuple[str, int]]:
        cur = self.conn.cursor()
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        table_names = [row[0] for row in cur.fetchall()]
        result = []
        for tname in table_names:
            cur.execute(
                "SELECT COUNT(*) FROM %s" % _pg_quote_ident(tname)  # noqa: S608
            )
            count = cur.fetchone()[0]
            result.append((tname, count))
        cur.close()
        return result

    def schema(self, table: str) -> str:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "ORDER BY ordinal_position",
            (table,),
        )
        cols = cur.fetchall()
        cur.close()
        if not cols:
            return f"Table '{table}' not found."
        lines = [f"-- Columns for {table}:"]
        for col_name, data_type, nullable, default in cols:
            parts = [f"  {col_name} {data_type}"]
            if nullable == "NO":
                parts.append("NOT NULL")
            if default:
                parts.append(f"DEFAULT {default}")
            lines.append(" ".join(parts))
        return "\n".join(lines)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def db_type(self) -> str:
        return "postgresql"


class _MySQLConn:
    """Wrapper around pymysql/MySQLdb for MySQL."""

    def __init__(self, connection_string: str) -> None:
        self._dsn = connection_string
        self._conn: Any = None

    def connect(self) -> None:
        parsed = urlparse(self._dsn)
        try:
            import pymysql

            self._conn = pymysql.connect(
                host=parsed.hostname or "localhost",
                port=parsed.port or 3306,
                user=parsed.username or "root",
                password=parsed.password or "",
                database=parsed.path.lstrip("/") if parsed.path else None,
                autocommit=True,
            )
        except ImportError:
            try:
                import aiomysql as _  # noqa: F401

                raise ImportError(
                    "aiomysql is installed but this tool uses synchronous pymysql. Install pymysql: pip install pymysql"
                ) from None
            except ImportError:
                raise ImportError("MySQL support requires pymysql. Install it: pip install pymysql") from None

    @property
    def conn(self) -> Any:
        if self._conn is None:
            raise RuntimeError("Not connected")
        return self._conn

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> tuple[list[str], list[tuple[Any, ...]]]:
        cur = self.conn.cursor()
        cur.execute(sql, params if params else None)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = cur.fetchmany(_MAX_ROWS) if cur.description else []
        cur.close()
        return columns, rows

    def tables(self) -> list[tuple[str, int]]:
        cur = self.conn.cursor()
        cur.execute("SHOW TABLES")
        table_names = [row[0] for row in cur.fetchall()]
        result = []
        for tname in table_names:
            safe_name = tname.replace("`", "``")
            cur.execute(f"SELECT COUNT(*) FROM `{safe_name}`")
            count = cur.fetchone()[0]
            result.append((tname, count))
        cur.close()
        return result

    def schema(self, table: str) -> str:
        cur = self.conn.cursor()
        safe_name = table.replace("`", "``")
        cur.execute(f"SHOW CREATE TABLE `{safe_name}`")
        row = cur.fetchone()
        cur.close()
        if row:
            return row[1] if len(row) > 1 else str(row[0])
        return f"Table '{table}' not found."

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def db_type(self) -> str:
        return "mysql"


def _pg_quote_ident(name: str) -> str:
    """Quote a PostgreSQL identifier to prevent injection."""
    return '"' + name.replace('"', '""') + '"'


def _parse_connection_string(conn_str: str) -> _SQLiteConn | _PostgreSQLConn | _MySQLConn:
    """Detect database type from connection string and return wrapper."""
    stripped = conn_str.strip()

    # PostgreSQL: postgresql://, postgres://, or libpq-style key=value
    if stripped.startswith(("postgresql://", "postgres://")):
        return _PostgreSQLConn(stripped)
    if "dbname=" in stripped and ("host=" in stripped or "user=" in stripped):
        return _PostgreSQLConn(stripped)

    # MySQL: mysql://
    if stripped.startswith("mysql://"):
        return _MySQLConn(stripped)

    # Default: treat as SQLite file path
    return _SQLiteConn(stripped)


# ======================================================================= #
#  DatabaseTool
# ======================================================================= #


class DatabaseTool(BaseTool):
    """Connect to databases, run SQL queries, inspect schemas.

    Supports SQLite (built-in), PostgreSQL (requires psycopg2), and
    MySQL (requires pymysql). Use ``action=connect`` first, then
    ``action=tables``, ``action=schema``, or ``action=query``.
    """

    name = "db"
    description = "Connect to databases, run SQL queries, inspect schemas"
    sequential = True  # mutations must be serialized
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["query", "schema", "tables", "connect"],
                "description": "Action to perform",
            },
            "sql": {
                "type": "string",
                "description": "SQL query to execute (for action=query)",
            },
            "connection_string": {
                "type": "string",
                "description": "Database connection string (for action=connect)",
            },
            "table": {
                "type": "string",
                "description": "Table name (for action=schema)",
            },
            "read_only": {
                "type": "boolean",
                "default": True,
                "description": "If true, only SELECT queries allowed",
            },
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        super().__init__()
        self._connection: _SQLiteConn | _PostgreSQLConn | _MySQLConn | None = None
        self._read_only: bool = True

    async def execute(self, **kwargs: Any) -> str:
        action: str = kwargs.get("action", "")
        if not action:
            return "[error] Missing required parameter: action"

        try:
            if action == "connect":
                return self._do_connect(kwargs)
            if action == "tables":
                return self._do_tables()
            if action == "schema":
                return self._do_schema(kwargs)
            if action == "query":
                return self._do_query(kwargs)
            return f"[error] Unknown action: {action!r}. Must be one of: connect, tables, schema, query."
        except Exception as exc:
            return f"[error] {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------ #
    #  Actions
    # ------------------------------------------------------------------ #

    def _do_connect(self, kwargs: dict[str, Any]) -> str:
        conn_str: str | None = kwargs.get("connection_string")
        if not conn_str:
            return "[error] action=connect requires a connection_string parameter."

        # Store read_only preference
        self._read_only = kwargs.get("read_only", True)

        # Close any existing connection
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass

        wrapper = _parse_connection_string(conn_str)
        try:
            wrapper.connect()
        except ImportError as exc:
            return f"[error] {exc}"
        except Exception as exc:
            return f"[error] Failed to connect: {exc}"

        self._connection = wrapper
        mode = "read-only" if self._read_only else "read-write"
        return f"Connected to {wrapper.db_type} database ({mode} mode)."

    def _do_tables(self) -> str:
        if self._connection is None:
            return "[error] No active connection. Use action=connect first."

        tables = self._connection.tables()
        if not tables:
            return "(no tables found)"

        columns = ["Table", "Rows"]
        rows = [(name, count) for name, count in tables]
        return _format_markdown_table(columns, rows)

    def _do_schema(self, kwargs: dict[str, Any]) -> str:
        if self._connection is None:
            return "[error] No active connection. Use action=connect first."

        table: str | None = kwargs.get("table")
        if not table:
            return "[error] action=schema requires a table parameter."

        return self._connection.schema(table)

    def _do_query(self, kwargs: dict[str, Any]) -> str:
        if self._connection is None:
            return "[error] No active connection. Use action=connect first."

        sql: str | None = kwargs.get("sql")
        if not sql or not sql.strip():
            return "[error] action=query requires a sql parameter."

        # Read-only enforcement
        read_only = kwargs.get("read_only", self._read_only)
        if read_only and not _is_read_only_sql(sql):
            return (
                "[error] Read-only mode is enabled. Only SELECT, DESCRIBE, SHOW, "
                "EXPLAIN, and PRAGMA statements are allowed. "
                "Set read_only=false in the connect action to allow mutations."
            )

        columns, rows = self._connection.execute(sql)
        if not columns:
            return "(query executed, no results returned)"

        total_note = ""
        if len(rows) >= _MAX_ROWS:
            total_note = f"\n\n(showing first {_MAX_ROWS} rows; results may be truncated)"

        return _format_markdown_table(columns, rows) + total_note
