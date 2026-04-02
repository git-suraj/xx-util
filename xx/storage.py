from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

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
        "DELETE FROM execution_logs WHERE invoked_at < datetime('now', 'localtime', ?)",
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


def fetch_executions(
    conn: sqlite3.Connection,
    *,
    days: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> list[sqlite3.Row]:
    where_sql, params = _build_filters(days=days, date_from=date_from, date_to=date_to)
    offset = max(0, page - 1) * page_size
    cur = conn.execute(
        f"""
        SELECT invoked_at, user_input, generated_command, executed, approved,
               provider, model, prompt_tokens, completion_tokens, total_tokens,
               risk_level, exit_code, cwd
        FROM execution_logs
        {where_sql}
        ORDER BY invoked_at DESC
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    )
    return list(cur.fetchall())


def count_executions(
    conn: sqlite3.Connection,
    *,
    days: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    where_sql, params = _build_filters(days=days, date_from=date_from, date_to=date_to)
    cur = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM execution_logs
        {where_sql}
        """,
        params,
    )
    row = cur.fetchone()
    return int(row["total"]) if row else 0


def fetch_token_summary_by_model(
    conn: sqlite3.Connection,
    *,
    days: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[sqlite3.Row]:
    where_sql, params = _build_filters(days=days, date_from=date_from, date_to=date_to)
    cur = conn.execute(
        f"""
        SELECT model, provider,
               COUNT(*) AS invocations,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM execution_logs
        {where_sql}
        GROUP BY provider, model
        ORDER BY total_tokens DESC, provider ASC, model ASC
        """,
        params,
    )
    return list(cur.fetchall())


def fetch_token_summary_by_provider(
    conn: sqlite3.Connection,
    *,
    days: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[sqlite3.Row]:
    where_sql, params = _build_filters(days=days, date_from=date_from, date_to=date_to)
    cur = conn.execute(
        f"""
        SELECT provider,
               COUNT(*) AS invocations,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM execution_logs
        {where_sql}
        GROUP BY provider
        ORDER BY total_tokens DESC, provider ASC
        """,
        params,
    )
    return list(cur.fetchall())


def _build_filters(
    *,
    days: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []
    if date_from:
        clauses.append("invoked_at >= datetime(?)")
        params.append(_normalize_lower_bound(date_from))
    if date_to:
        clauses.append("invoked_at <= datetime(?)")
        params.append(_normalize_upper_bound(date_to))
    if not date_from and not date_to and days is not None:
        clauses.append("invoked_at >= datetime('now', 'localtime', ?)")
        params.append(f"-{days} days")
    if not clauses:
        return "", tuple()
    return "WHERE " + " AND ".join(clauses), tuple(params)


def _normalize_lower_bound(value: str) -> str:
    normalized = value.strip().replace("T", " ")
    if len(normalized) == 10:
        return f"{normalized} 00:00:00"
    if len(normalized) == 16:
        return f"{normalized}:00"
    return normalized


def _normalize_upper_bound(value: str) -> str:
    normalized = value.strip().replace("T", " ")
    if len(normalized) == 10:
        return f"{normalized} 23:59:59"
    if len(normalized) == 16:
        return f"{normalized}:59"
    return normalized
