"""Security regression tests for the database tool.

Covers the P0/P1 findings from
``research/karna/NEW_TOOLS_AUDIT_20260420.md``: parameterised queries,
private-host guard, and credential scrubbing on connect failures.
"""

from __future__ import annotations

import sqlite3

import pytest

from karna.tools.database import DatabaseTool, _ssrf_reject_private_host


class TestDatabaseParameterizedQueries:
    """P0: SQL injection. Calls must bind values via `params`, not
    interpolate them into the SQL string."""

    @pytest.mark.asyncio
    async def test_params_field_is_bound_not_interpolated(self, tmp_path):
        db_path = tmp_path / "t.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        conn.executemany(
            "INSERT INTO users VALUES (?, ?)",
            [(1, "alice"), (2, "bob")],
        )
        conn.commit()
        conn.close()

        tool = DatabaseTool()
        await tool.execute(action="connect", connection_string=str(db_path))

        result = await tool.execute(
            action="query",
            sql="SELECT name FROM users WHERE id = ?",
            params=[1],
        )
        assert "alice" in result
        assert "bob" not in result

        # Classic injection payload passed as a *value* must be bound as a
        # literal. With parameterised binding, the row count is zero.
        injected = await tool.execute(
            action="query",
            sql="SELECT name FROM users WHERE name = ?",
            params=["alice' OR '1'='1"],
        )
        assert "alice" not in injected
        assert "bob" not in injected

    @pytest.mark.asyncio
    async def test_params_must_be_iterable(self, tmp_path):
        db_path = tmp_path / "t.db"
        sqlite3.connect(db_path).close()
        tool = DatabaseTool()
        await tool.execute(action="connect", connection_string=str(db_path))
        result = await tool.execute(
            action="query",
            sql="SELECT 1",
            params="not-a-list",
        )
        assert "[error]" in result
        assert "params" in result.lower()


class TestDatabaseSSRFGuard:
    """P1: connection_string must not reach private/metadata hosts."""

    @pytest.mark.parametrize(
        "dsn",
        [
            "postgresql://u:p@169.254.169.254:5432/m",
            "postgres://u:p@127.0.0.1:5432/m",
            "postgresql://u:p@10.0.0.1:5432/m",
            "mysql://root:p@localhost:3306/m",
        ],
    )
    def test_private_hosts_blocked(self, dsn):
        assert _ssrf_reject_private_host(dsn) is not None

    @pytest.mark.parametrize(
        "dsn",
        [
            "postgresql://u:p@db.example.com:5432/m",
            "mysql://u:p@db.example.com:3306/m",
            "/tmp/a.db",
        ],
    )
    def test_public_and_local_file_allowed(self, dsn):
        assert _ssrf_reject_private_host(dsn) is None


class TestDatabaseCredentialScrubbing:
    """P1: connect failure messages must not echo plaintext credentials."""

    @pytest.mark.asyncio
    async def test_bearer_token_scrubbed_from_error(self, tmp_path):
        tool = DatabaseTool()
        secret_dsn = "postgresql://u:Bearer abcdefghijklmnopqrstuvwxyz0123@public.example.com/db"
        result = await tool.execute(action="connect", connection_string=secret_dsn)
        assert "abcdefghijklmnopqrstuvwxyz0123" not in result
