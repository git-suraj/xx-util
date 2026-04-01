from __future__ import annotations

import sqlite3
from pathlib import Path

from xx.types import ExecutionRecord


SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoked_at TEXT NOT NULL,
  user_input TEXT NOT NULL,
  generated_command TEXT NOT NULL,
  executed INTEGER NOT NULL,
  approved INTEGER NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  total_tokens INTEGER,
  risk_level TEXT NOT NULL,
  exit_code INTEGER,
  cwd TEXT NOT NULL
);
"""


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def prune_old_records(conn: sqlite3.Connection, retention_days: int) -> None:
    conn.execute(
        "DELETE FROM execution_logs WHERE invoked_at < datetime('now', ?)",
        (f"-{retention_days} days",),
    )
    conn.commit()


def insert_execution(conn: sqlite3.Connection, record: ExecutionRecord) -> int:
    cur = conn.execute(
        """
        INSERT INTO execution_logs (
          invoked_at, user_input, generated_command, executed, approved,
          provider, model, prompt_tokens, completion_tokens, total_tokens,
          risk_level, exit_code, cwd
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.invoked_at,
            record.user_input,
            record.generated_command,
            int(record.executed),
            int(record.approved),
            record.provider,
            record.model,
            record.prompt_tokens,
            record.completion_tokens,
            record.total_tokens,
            record.risk_level,
            record.exit_code,
            record.cwd,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_execution_outcome(
    conn: sqlite3.Connection, record_id: int, *, executed: bool, approved: bool, exit_code: int | None
) -> None:
    conn.execute(
        """
        UPDATE execution_logs
        SET executed = ?, approved = ?, exit_code = ?
        WHERE id = ?
        """,
        (int(executed), int(approved), exit_code, record_id),
    )
    conn.commit()


def fetch_executions(conn: sqlite3.Connection, days: int) -> list[sqlite3.Row]:
    cur = conn.execute(
        """
        SELECT invoked_at, user_input, generated_command, executed, approved,
               provider, model, prompt_tokens, completion_tokens, total_tokens,
               risk_level, exit_code, cwd
        FROM execution_logs
        WHERE invoked_at >= datetime('now', ?)
        ORDER BY invoked_at DESC
        """,
        (f"-{days} days",),
    )
    return list(cur.fetchall())


def fetch_token_summary_by_model(conn: sqlite3.Connection, days: int) -> list[sqlite3.Row]:
    cur = conn.execute(
        """
        SELECT model, provider,
               COUNT(*) AS invocations,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM execution_logs
        WHERE invoked_at >= datetime('now', ?)
        GROUP BY provider, model
        ORDER BY total_tokens DESC, provider ASC, model ASC
        """,
        (f"-{days} days",),
    )
    return list(cur.fetchall())


def fetch_token_summary_by_provider(conn: sqlite3.Connection, days: int) -> list[sqlite3.Row]:
    cur = conn.execute(
        """
        SELECT provider,
               COUNT(*) AS invocations,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM execution_logs
        WHERE invoked_at >= datetime('now', ?)
        GROUP BY provider
        ORDER BY total_tokens DESC, provider ASC
        """,
        (f"-{days} days",),
    )
    return list(cur.fetchall())
