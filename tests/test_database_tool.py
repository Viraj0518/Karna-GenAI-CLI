"""Tests for the database connector tool (db).

Covers SQLite connect, query, schema, tables, read-only enforcement,
SQL injection prevention, result formatting, connection string
validation, and graceful handling of missing optional dependencies.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from karna.tools import get_all_tools, get_tool
from karna.tools.database import (
    DatabaseTool,
    _format_markdown_table,
    _is_read_only_sql,
    _parse_connection_string,
    _SQLiteConn,
)

# ======================================================================= #
#  Fixtures
# ======================================================================= #


@pytest.fixture()
def tmp_db(tmp_path: Path) -> str:
    """Create a temporary SQLite database with sample data."""
    import sqlite3

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT)")
    conn.execute("INSERT INTO users (name, email) VALUES ('Alice', 'alice@example.com')")
    conn.execute("INSERT INTO users (name, email) VALUES ('Bob', 'bob@example.com')")
    conn.execute("INSERT INTO users (name, email) VALUES ('Charlie', 'charlie@example.com')")
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, total REAL)")
    conn.execute("INSERT INTO orders (user_id, total) VALUES (1, 99.99)")
    conn.execute("INSERT INTO orders (user_id, total) VALUES (2, 149.50)")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def tool() -> DatabaseTool:
    """Return a fresh DatabaseTool instance."""
    return DatabaseTool()


# ======================================================================= #
#  SQLite connect + query + schema + tables
# ======================================================================= #


class TestSQLiteConnect:
    @pytest.mark.asyncio
    async def test_connect_sqlite(self, tool: DatabaseTool, tmp_db: str):
        result = await tool.execute(action="connect", connection_string=tmp_db)
        assert "Connected" in result
        assert "sqlite" in result.lower()

    @pytest.mark.asyncio
    async def test_connect_invalid_path(self, tool: DatabaseTool, tmp_path: Path):
        # SQLite will fail on a path whose parent dir doesn't exist
        db_path = str(tmp_path / "nonexistent" / "dir" / "test.db")
        result = await tool.execute(action="connect", connection_string=db_path)
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_connect_missing_connection_string(self, tool: DatabaseTool):
        result = await tool.execute(action="connect")
        assert "[error]" in result
        assert "connection_string" in result.lower()


class TestSQLiteQuery:
    @pytest.mark.asyncio
    async def test_select_all(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="query", sql="SELECT * FROM users")
        assert "Alice" in result
        assert "Bob" in result
        assert "Charlie" in result

    @pytest.mark.asyncio
    async def test_select_with_where(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="query", sql="SELECT name FROM users WHERE id = 1")
        assert "Alice" in result
        assert "Bob" not in result

    @pytest.mark.asyncio
    async def test_query_no_connection(self, tool: DatabaseTool):
        result = await tool.execute(action="query", sql="SELECT 1")
        assert "[error]" in result
        assert "connection" in result.lower()

    @pytest.mark.asyncio
    async def test_query_empty_sql(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="query", sql="")
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_query_missing_sql(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="query")
        assert "[error]" in result


class TestSQLiteSchema:
    @pytest.mark.asyncio
    async def test_schema_users(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="schema", table="users")
        assert "users" in result.lower()
        # Should show column info (CREATE TABLE or column listing)
        assert "name" in result.lower()
        assert "email" in result.lower()

    @pytest.mark.asyncio
    async def test_schema_missing_table_param(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="schema")
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_schema_nonexistent_table(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="schema", table="nonexistent")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_schema_no_connection(self, tool: DatabaseTool):
        result = await tool.execute(action="schema", table="users")
        assert "[error]" in result


class TestSQLiteTables:
    @pytest.mark.asyncio
    async def test_list_tables(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="tables")
        assert "users" in result
        assert "orders" in result

    @pytest.mark.asyncio
    async def test_tables_show_row_counts(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="tables")
        # users has 3 rows, orders has 2
        assert "3" in result
        assert "2" in result

    @pytest.mark.asyncio
    async def test_tables_empty_db(self, tool: DatabaseTool, tmp_path: Path):
        import sqlite3

        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.close()
        await tool.execute(action="connect", connection_string=db_path)
        result = await tool.execute(action="tables")
        assert "no tables" in result.lower()

    @pytest.mark.asyncio
    async def test_tables_no_connection(self, tool: DatabaseTool):
        result = await tool.execute(action="tables")
        assert "[error]" in result


# ======================================================================= #
#  Read-only mode
# ======================================================================= #


class TestReadOnlyMode:
    @pytest.mark.asyncio
    async def test_blocks_insert(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        result = await tool.execute(action="query", sql="INSERT INTO users (name) VALUES ('Evil')")
        assert "[error]" in result
        assert "read-only" in result.lower()

    @pytest.mark.asyncio
    async def test_blocks_update(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        result = await tool.execute(action="query", sql="UPDATE users SET name = 'Evil' WHERE id = 1")
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_blocks_delete(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        result = await tool.execute(action="query", sql="DELETE FROM users WHERE id = 1")
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_blocks_drop(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        result = await tool.execute(action="query", sql="DROP TABLE users")
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_blocks_alter(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        result = await tool.execute(action="query", sql="ALTER TABLE users ADD COLUMN age INTEGER")
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_allows_select(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        result = await tool.execute(action="query", sql="SELECT * FROM users")
        assert "[error]" not in result
        assert "Alice" in result

    @pytest.mark.asyncio
    async def test_allows_explain(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        result = await tool.execute(action="query", sql="EXPLAIN QUERY PLAN SELECT * FROM users")
        assert "[error]" not in result

    @pytest.mark.asyncio
    async def test_allows_pragma(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        result = await tool.execute(action="query", sql="PRAGMA table_info(users)")
        assert "[error]" not in result

    @pytest.mark.asyncio
    async def test_default_is_read_only(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="query", sql="INSERT INTO users (name) VALUES ('Blocked')")
        assert "[error]" in result
        assert "read-only" in result.lower()

    @pytest.mark.asyncio
    async def test_read_write_allows_mutations(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db, read_only=False)
        result = await tool.execute(action="query", sql="INSERT INTO users (name) VALUES ('Dave')")
        assert "[error]" not in result

    @pytest.mark.asyncio
    async def test_blocks_create(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        result = await tool.execute(action="query", sql="CREATE TABLE evil (id INTEGER)")
        assert "[error]" in result

    @pytest.mark.asyncio
    async def test_blocks_truncate(self, tool: DatabaseTool, tmp_db: str):
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        result = await tool.execute(action="query", sql="TRUNCATE TABLE users")
        assert "[error]" in result


# ======================================================================= #
#  SQL injection prevention
# ======================================================================= #


class TestSQLInjection:
    @pytest.mark.asyncio
    async def test_table_name_with_quotes(self, tool: DatabaseTool, tmp_db: str):
        """Schema lookup with a malicious table name should not crash or execute arbitrary SQL."""
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="schema", table="users; DROP TABLE users; --")
        # Should either not find the table or handle gracefully
        assert "not found" in result.lower() or "[error]" not in result

    @pytest.mark.asyncio
    async def test_semicolon_in_query_read_only(self, tool: DatabaseTool, tmp_db: str):
        """Multiple statements separated by semicolons."""
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        await tool.execute(
            action="query",
            sql="SELECT 1; DROP TABLE users; --",
        )
        # SQLite's execute() only runs the first statement, so DROP should not happen.
        # Verify users table still exists.
        tables_result = await tool.execute(action="tables")
        assert "users" in tables_result

    @pytest.mark.asyncio
    async def test_union_injection_in_read_only(self, tool: DatabaseTool, tmp_db: str):
        """UNION-based injection is a SELECT, so allowed in read-only, but should not crash."""
        await tool.execute(action="connect", connection_string=tmp_db, read_only=True)
        result = await tool.execute(
            action="query",
            sql="SELECT name FROM users UNION SELECT sql FROM sqlite_master",
        )
        # This is technically a valid read-only query — it should succeed
        assert "[error]" not in result


# ======================================================================= #
#  Result formatting
# ======================================================================= #


class TestResultFormatting:
    def test_markdown_table_basic(self):
        columns = ["id", "name"]
        rows = [(1, "Alice"), (2, "Bob")]
        result = _format_markdown_table(columns, rows)
        assert "| id" in result
        assert "| name" in result
        assert "Alice" in result
        assert "Bob" in result
        # Check separator line
        assert "| --" in result

    def test_markdown_table_empty_rows(self):
        columns = ["id", "name"]
        result = _format_markdown_table(columns, [])
        assert "0 rows" in result

    def test_markdown_table_null_values(self):
        columns = ["id", "value"]
        rows = [(1, None), (2, "data")]
        result = _format_markdown_table(columns, rows)
        assert "NULL" in result
        assert "data" in result

    def test_markdown_table_no_columns(self):
        result = _format_markdown_table([], [])
        assert "no columns" in result

    @pytest.mark.asyncio
    async def test_query_result_is_markdown(self, tmp_db: str):
        tool = DatabaseTool()
        await tool.execute(action="connect", connection_string=tmp_db)
        result = await tool.execute(action="query", sql="SELECT id, name FROM users ORDER BY id")
        # Should be a markdown table with | separators
        lines = result.strip().split("\n")
        assert len(lines) >= 3  # header + separator + at least 1 row
        assert lines[0].startswith("|")
        assert "---" in lines[1]


# ======================================================================= #
#  Connection string validation
# ======================================================================= #


class TestConnectionStringValidation:
    def test_sqlite_path(self):
        wrapper = _parse_connection_string("/tmp/test.db")
        assert isinstance(wrapper, _SQLiteConn)

    def test_postgresql_url(self):
        wrapper = _parse_connection_string("postgresql://user:pass@localhost/mydb")
        assert wrapper.db_type == "postgresql"

    def test_postgres_url(self):
        wrapper = _parse_connection_string("postgres://user:pass@localhost/mydb")
        assert wrapper.db_type == "postgresql"

    def test_mysql_url(self):
        wrapper = _parse_connection_string("mysql://user:pass@localhost/mydb")
        assert wrapper.db_type == "mysql"

    def test_libpq_style(self):
        wrapper = _parse_connection_string("host=localhost dbname=mydb user=postgres")
        assert wrapper.db_type == "postgresql"

    def test_plain_path_is_sqlite(self):
        wrapper = _parse_connection_string("./my_data.db")
        assert wrapper.db_type == "sqlite"

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        tool = DatabaseTool()
        result = await tool.execute(action="invalid")
        assert "[error]" in result
        assert "Unknown action" in result


# ======================================================================= #
#  Missing optional dependencies (graceful error)
# ======================================================================= #


class TestMissingDeps:
    @pytest.mark.asyncio
    async def test_postgres_missing_dep(self):
        tool = DatabaseTool()
        with patch.dict("sys.modules", {"psycopg2": None, "asyncpg": None}):
            result = await tool.execute(
                action="connect",
                connection_string="postgresql://user:pass@localhost/mydb",
            )
            # Should get an error about missing dependency, not a crash
            assert "[error]" in result

    @pytest.mark.asyncio
    async def test_mysql_missing_dep(self):
        tool = DatabaseTool()
        with patch.dict("sys.modules", {"pymysql": None, "aiomysql": None}):
            result = await tool.execute(
                action="connect",
                connection_string="mysql://user:pass@localhost/mydb",
            )
            assert "[error]" in result


# ======================================================================= #
#  Read-only SQL detection
# ======================================================================= #


class TestReadOnlySqlDetection:
    def test_select(self):
        assert _is_read_only_sql("SELECT * FROM users") is True

    def test_select_lowercase(self):
        assert _is_read_only_sql("select count(*) from users") is True

    def test_explain(self):
        assert _is_read_only_sql("EXPLAIN SELECT * FROM users") is True

    def test_describe(self):
        assert _is_read_only_sql("DESCRIBE users") is True

    def test_show(self):
        assert _is_read_only_sql("SHOW TABLES") is True

    def test_pragma(self):
        assert _is_read_only_sql("PRAGMA table_info(users)") is True

    def test_insert(self):
        assert _is_read_only_sql("INSERT INTO users VALUES (1, 'a')") is False

    def test_update(self):
        assert _is_read_only_sql("UPDATE users SET name = 'x'") is False

    def test_delete(self):
        assert _is_read_only_sql("DELETE FROM users") is False

    def test_drop(self):
        assert _is_read_only_sql("DROP TABLE users") is False

    def test_alter(self):
        assert _is_read_only_sql("ALTER TABLE users ADD col INT") is False

    def test_create(self):
        assert _is_read_only_sql("CREATE TABLE evil (id INT)") is False

    def test_truncate(self):
        assert _is_read_only_sql("TRUNCATE TABLE users") is False

    def test_empty_string(self):
        assert _is_read_only_sql("") is True

    def test_whitespace_select(self):
        assert _is_read_only_sql("  SELECT 1  ") is True

    def test_leading_whitespace_insert(self):
        assert _is_read_only_sql("  INSERT INTO x VALUES (1)") is False


# ======================================================================= #
#  Registry integration
# ======================================================================= #


class TestDatabaseToolRegistry:
    def test_get_db_tool(self):
        tool = get_tool("db")
        assert tool.name == "db"

    def test_all_tools_includes_db(self):
        tools = get_all_tools()
        names = {t.name for t in tools}
        assert "db" in names

    def test_tool_properties(self):
        tool = DatabaseTool()
        assert tool.name == "db"
        assert tool.sequential is True
        assert "action" in tool.parameters["properties"]

    def test_anthropic_tool_format(self):
        tool = DatabaseTool()
        fmt = tool.to_anthropic_tool()
        assert fmt["name"] == "db"
        assert "input_schema" in fmt

    def test_openai_tool_format(self):
        tool = DatabaseTool()
        fmt = tool.to_openai_tool()
        assert fmt["type"] == "function"
        assert fmt["function"]["name"] == "db"
