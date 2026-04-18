"""SQLite FTS5 session database.

PRIVACY: All session data stored locally at ~/.karna/sessions/sessions.db
No session data is ever sent to any external service.
Sessions can be deleted: nellie history delete <id>
Full wipe: rm -rf ~/.karna/sessions/
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from karna.models import Conversation, Message, ToolCall, ToolResult
from karna.security.guards import scrub_secrets

# Default database location
_DEFAULT_DB_DIR = Path.home() / ".karna" / "sessions"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "sessions.db"


class SessionDB:
    """SQLite FTS5 session database.

    Stores conversations, messages, and supports full-text search across
    all historical sessions.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Tighten permissions on the session directory (POSIX only — Windows
        # will raise OSError/NotImplementedError and be skipped).
        try:
            self.db_path.parent.chmod(0o700)
        except (OSError, NotImplementedError):
            pass
        self._conn: sqlite3.Connection | None = None
        self._init_db()
        # Tighten permissions on the sessions.db file itself once created.
        if self.db_path.exists():
            try:
                self.db_path.chmod(0o600)
            except (OSError, NotImplementedError):
                pass

    # ------------------------------------------------------------------ #
    #  Connection management
    # ------------------------------------------------------------------ #

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ #
    #  Schema initialisation
    # ------------------------------------------------------------------ #

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                model TEXT,
                provider TEXT,
                cwd TEXT,
                git_branch TEXT,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                total_cost_usd REAL DEFAULT 0.0,
                summary TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                role TEXT NOT NULL,
                content TEXT,
                tool_calls TEXT,
                tool_results TEXT,
                tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id);
        """)

        # FTS5 virtual table — create only if it doesn't exist.
        # We check the sqlite_master table to avoid errors on re-init.
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'").fetchone()
        if row is None:
            conn.executescript("""
                CREATE VIRTUAL TABLE messages_fts USING fts5(
                    content, tool_results,
                    content='messages', content_rowid='id'
                );

                -- Triggers to keep FTS in sync with the messages table
                CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content, tool_results)
                    VALUES (new.id, new.content, new.tool_results);
                END;

                CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content, tool_results)
                    VALUES ('delete', old.id, old.content, old.tool_results);
                END;

                CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content, tool_results)
                    VALUES ('delete', old.id, old.content, old.tool_results);
                    INSERT INTO messages_fts(rowid, content, tool_results)
                    VALUES (new.id, new.content, new.tool_results);
                END;
            """)

        conn.commit()

    # ------------------------------------------------------------------ #
    #  Session CRUD
    # ------------------------------------------------------------------ #

    def create_session(
        self,
        model: str,
        provider: str,
        cwd: str,
        git_branch: str | None = None,
    ) -> str:
        """Create a new session and return its id."""
        session_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO sessions (id, started_at, model, provider, cwd, git_branch)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, now, model, provider, cwd, git_branch),
        )
        conn.commit()
        return session_id

    def end_session(self, session_id: str, summary: str | None = None) -> None:
        """Mark a session as ended."""
        now = datetime.now(timezone.utc).isoformat()
        scrubbed_summary = scrub_secrets(summary) if summary else summary
        conn = self._get_conn()
        conn.execute(
            "UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?",
            (now, scrubbed_summary, session_id),
        )
        conn.commit()

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and its messages. Returns True if found."""
        conn = self._get_conn()
        # Delete messages first (triggers will clean up FTS)
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount > 0

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Get a single session by id."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent sessions, newest first.

        Ties on ``started_at`` are broken by ``rowid`` DESC so sessions
        created in the same microsecond still order deterministically.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    #  Message CRUD
    # ------------------------------------------------------------------ #

    def add_message(
        self,
        session_id: str,
        message: Message,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> int:
        """Persist a message and return its row id.

        All message content and tool-result payloads are passed through
        ``scrub_secrets`` before being written to disk. This prevents
        leaked API keys, bearer tokens, or private-key material from
        persisting in ``~/.karna/sessions/sessions.db`` forever.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Scrub message content
        scrubbed_content = scrub_secrets(message.content or "") if message.content else message.content

        # tool_calls are the model's own call arguments — not tool output —
        # but scrub them too in case the model echoed a secret into its args.
        tool_calls_json: str | None = None
        if message.tool_calls:
            tool_calls_json = scrub_secrets(json.dumps([tc.model_dump() for tc in message.tool_calls]))

        # tool_results are the high-risk surface (.env reads, creds dumps, etc.)
        tool_results_json: str | None = None
        if message.tool_results:
            scrubbed_results = []
            for tr in message.tool_results:
                tr_dict = tr.model_dump()
                tr_dict["content"] = scrub_secrets(tr_dict.get("content") or "")
                scrubbed_results.append(tr_dict)
            tool_results_json = json.dumps(scrubbed_results)

        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO messages
               (session_id, role, content, tool_calls, tool_results,
                tokens, cost_usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                message.role,
                scrubbed_content,
                tool_calls_json,
                tool_results_json,
                tokens,
                cost_usd,
                now,
            ),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Return all messages for a session, ordered chronologically."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_session_cost(
        self,
        session_id: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        """Increment the aggregate cost counters on the session row."""
        conn = self._get_conn()
        conn.execute(
            """UPDATE sessions SET
                total_input_tokens  = total_input_tokens  + ?,
                total_output_tokens = total_output_tokens + ?,
                total_cost_usd      = total_cost_usd      + ?
               WHERE id = ?""",
            (input_tokens, output_tokens, cost_usd, session_id),
        )
        conn.commit()

    # ------------------------------------------------------------------ #
    #  Full-text search
    # ------------------------------------------------------------------ #

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """FTS5 search across message content and tool results.

        Returns matching messages joined with their session metadata.
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT m.*, s.model, s.provider, s.started_at AS session_started
               FROM messages_fts f
               JOIN messages m ON m.id = f.rowid
               JOIN sessions s ON s.id = m.session_id
               WHERE messages_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    #  Resume
    # ------------------------------------------------------------------ #

    def resume_session(self, session_id: str) -> Conversation | None:
        """Reconstruct a Conversation from a stored session.

        Returns None if the session does not exist.
        """
        session = self.get_session(session_id)
        if session is None:
            return None

        rows = self.get_session_messages(session_id)
        messages: list[Message] = []

        for row in rows:
            tool_calls: list[ToolCall] = []
            tool_results: list[ToolResult] = []

            if row["tool_calls"]:
                for tc in json.loads(row["tool_calls"]):
                    tool_calls.append(ToolCall(**tc))
            if row["tool_results"]:
                for tr in json.loads(row["tool_results"]):
                    tool_results.append(ToolResult(**tr))

            messages.append(
                Message(
                    role=row["role"],
                    content=row["content"] or "",
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                )
            )

        return Conversation(
            messages=messages,
            model=session.get("model", ""),
            provider=session.get("provider", ""),
        )

    # ------------------------------------------------------------------ #
    #  Fork
    # ------------------------------------------------------------------ #

    def fork_session(
        self,
        source_id: str,
        new_name: str | None = None,
        *,
        up_to_message_id: int | None = None,
    ) -> str:
        """Duplicate a session and its messages, return the new id.

        If *up_to_message_id* is provided, only messages whose ``id`` is
        ``<=`` that value are copied. Otherwise all messages through now
        are included (the "fork from current state" case).

        Raises ``KeyError`` if the source session does not exist.
        """
        source = self.get_session(source_id)
        if source is None:
            raise KeyError(f"source session not found: {source_id!r}")

        new_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()

        # Optional name lives in the session summary (there's no first-class
        # name column). Keep the source summary if no new name was given so
        # history still reads sensibly.
        summary = new_name if new_name is not None else source.get("summary")

        conn = self._get_conn()
        conn.execute(
            """INSERT INTO sessions
                 (id, started_at, ended_at, model, provider, cwd, git_branch,
                  total_input_tokens, total_output_tokens, total_cost_usd, summary)
               VALUES (?, ?, NULL, ?, ?, ?, ?, 0, 0, 0.0, ?)""",
            (
                new_id,
                now,
                source.get("model"),
                source.get("provider"),
                source.get("cwd"),
                source.get("git_branch"),
                summary,
            ),
        )

        # Copy messages. We rewrite ``created_at`` to the original value
        # so the replay preserves ordering, but assign fresh rowids.
        if up_to_message_id is None:
            rows = conn.execute(
                "SELECT role, content, tool_calls, tool_results, tokens, cost_usd, created_at "
                "FROM messages WHERE session_id = ? ORDER BY id",
                (source_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content, tool_calls, tool_results, tokens, cost_usd, created_at "
                "FROM messages WHERE session_id = ? AND id <= ? ORDER BY id",
                (source_id, up_to_message_id),
            ).fetchall()

        for row in rows:
            conn.execute(
                """INSERT INTO messages
                     (session_id, role, content, tool_calls, tool_results,
                      tokens, cost_usd, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    row["role"],
                    row["content"],
                    row["tool_calls"],
                    row["tool_results"],
                    row["tokens"],
                    row["cost_usd"],
                    row["created_at"],
                ),
            )

        conn.commit()
        return new_id

    def get_latest_session_id(self) -> str | None:
        """Return the id of the most recent session, or None.

        Tie-breaks by ``rowid`` DESC so two sessions inserted in the same
        microsecond still return the later one.
        """
        conn = self._get_conn()
        row = conn.execute("SELECT id FROM sessions ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
        return row["id"] if row else None

    # ------------------------------------------------------------------ #
    #  Cost aggregation queries
    # ------------------------------------------------------------------ #

    def get_cost_since(self, since_iso: str) -> dict[str, Any]:
        """Aggregate cost across sessions started at or after *since_iso*."""
        conn = self._get_conn()
        row = conn.execute(
            """SELECT
                COALESCE(SUM(total_input_tokens),  0) AS input_tokens,
                COALESCE(SUM(total_output_tokens), 0) AS output_tokens,
                COALESCE(SUM(total_cost_usd),      0.0) AS cost_usd,
                COUNT(*) AS session_count
               FROM sessions WHERE started_at >= ?""",
            (since_iso,),
        ).fetchone()
        return dict(row)  # type: ignore[arg-type]

    def get_total_cost(self) -> dict[str, Any]:
        """Aggregate cost across all sessions."""
        conn = self._get_conn()
        row = conn.execute(
            """SELECT
                COALESCE(SUM(total_input_tokens),  0) AS input_tokens,
                COALESCE(SUM(total_output_tokens), 0) AS output_tokens,
                COALESCE(SUM(total_cost_usd),      0.0) AS cost_usd,
                COUNT(*) AS session_count
               FROM sessions"""
        ).fetchone()
        return dict(row)  # type: ignore[arg-type]

    def get_cost_by_model(self, days: int = 30) -> dict[str, dict[str, Any]]:
        """Cost breakdown by model over the last *days* days."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT
                model,
                COALESCE(SUM(total_input_tokens),  0) AS input_tokens,
                COALESCE(SUM(total_output_tokens), 0) AS output_tokens,
                COALESCE(SUM(total_cost_usd),      0.0) AS cost_usd,
                COUNT(*) AS session_count
               FROM sessions
               WHERE started_at >= datetime('now', ?)
               GROUP BY model
               ORDER BY cost_usd DESC""",
            (f"-{days} days",),
        ).fetchall()
        return {r["model"]: dict(r) for r in rows}
