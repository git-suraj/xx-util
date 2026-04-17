from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from xx.types import ExecutionRecord


SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoked_at TEXT NOT NULL,
  interaction_id TEXT,
  execution_group_id TEXT,
  attempt_index INTEGER,
  execution_type TEXT NOT NULL DEFAULT 'standalone',
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
    _ensure_column(conn, "interaction_id", "TEXT")
    _ensure_column(conn, "execution_group_id", "TEXT")
    _ensure_column(conn, "attempt_index", "INTEGER")
    _ensure_column(conn, "execution_type", "TEXT NOT NULL DEFAULT 'standalone'")
    conn.execute(
        "UPDATE execution_logs SET interaction_id = execution_group_id WHERE interaction_id IS NULL OR interaction_id = ''"
    )
    conn.execute(
        "UPDATE execution_logs SET execution_group_id = printf('legacy-%d', id) WHERE execution_group_id IS NULL OR execution_group_id = ''"
    )
    conn.execute(
        "UPDATE execution_logs SET interaction_id = execution_group_id WHERE interaction_id IS NULL OR interaction_id = ''"
    )
    conn.execute(
        "UPDATE execution_logs SET attempt_index = 1 WHERE attempt_index IS NULL OR attempt_index < 1"
    )
    conn.execute(
        "UPDATE execution_logs SET execution_type = 'standalone' WHERE execution_type IS NULL OR execution_type = ''"
    )
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
          invoked_at, interaction_id, execution_group_id, attempt_index, execution_type, user_input, generated_command, executed, approved,
          provider, model, prompt_tokens, completion_tokens, total_tokens,
          risk_level, exit_code, cwd
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.invoked_at,
            record.interaction_id,
            record.execution_group_id,
            record.attempt_index,
            record.execution_type,
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
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> list[dict[str, Any]]:
    sessions = _build_execution_sessions(
        conn,
        days=days,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    offset = max(0, page - 1) * page_size
    return sessions[offset : offset + page_size]


def count_executions(
    conn: sqlite3.Connection,
    *,
    days: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str | None = None,
) -> int:
    return len(
        _build_execution_sessions(
            conn,
            days=days,
            date_from=date_from,
            date_to=date_to,
            search=search,
        )
    )


def _build_execution_sessions(
    conn: sqlite3.Connection,
    *,
    days: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    where_sql, params = _build_filters(days=days, date_from=date_from, date_to=date_to, search=search)
    cur = conn.execute(
        f"""
        SELECT id, invoked_at, interaction_id, execution_group_id, attempt_index, execution_type, user_input, generated_command, executed, approved,
               provider, model, prompt_tokens, completion_tokens, total_tokens,
               risk_level, exit_code, cwd
        FROM execution_logs
        {where_sql}
        ORDER BY invoked_at ASC, id ASC
        """,
        params,
    )
    rows = list(cur.fetchall())
    sessions: list[dict[str, Any]] = []
    current_key: str | None = None
    current_session: dict[str, Any] | None = None
    for row in rows:
        key = str(row["execution_group_id"] or f"legacy-{row['id']}")
        if key != current_key:
            if current_session is not None:
                sessions.append(current_session)
            current_session = _start_session(row, key)
            current_key = key
        else:
            _append_attempt(current_session, row)
    if current_session is not None:
        sessions.append(current_session)
    sessions.reverse()
    return sessions


def _start_session(row: sqlite3.Row, session_key: str) -> dict[str, Any]:
    prompt_tokens = _safe_int(row["prompt_tokens"])
    completion_tokens = _safe_int(row["completion_tokens"])
    total_tokens = _safe_int(row["total_tokens"])
    session = {
        "session_key": session_key,
        "invoked_at": row["invoked_at"],
        "interaction_id": str(row["interaction_id"] or session_key),
        "type": str(row["execution_type"] or "standalone"),
        "user_input": row["user_input"],
        "generated_command": row["generated_command"],
        "final_command": row["generated_command"],
        "executed": int(row["executed"]),
        "approved": int(row["approved"]),
        "provider": row["provider"],
        "model": row["model"],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "risk_level": row["risk_level"],
        "exit_code": row["exit_code"],
        "cwd": row["cwd"],
        "tries": 1,
        "attempts": [
            {
                "attempt_index": _safe_int(row["attempt_index"]),
                "generated_command": row["generated_command"],
                "executed": int(row["executed"]),
                "approved": int(row["approved"]),
                "exit_code": row["exit_code"],
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
        ],
    }
    session["attempts_summary"] = _attempts_summary(session["attempts"])
    return session


def _append_attempt(session: dict[str, Any] | None, row: sqlite3.Row) -> None:
    if session is None:
        return
    prompt_tokens = _safe_int(row["prompt_tokens"])
    completion_tokens = _safe_int(row["completion_tokens"])
    total_tokens = _safe_int(row["total_tokens"])
    attempt = {
        "attempt_index": _safe_int(row["attempt_index"]),
        "generated_command": row["generated_command"],
        "executed": int(row["executed"]),
        "approved": int(row["approved"]),
        "exit_code": row["exit_code"],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    session["generated_command"] = row["generated_command"]
    session["final_command"] = row["generated_command"]
    session["interaction_id"] = str(row["interaction_id"] or session.get("interaction_id") or session["session_key"])
    session["type"] = str(row["execution_type"] or session.get("type") or "standalone")
    session["executed"] = int(row["executed"])
    session["approved"] = int(row["approved"])
    session["provider"] = row["provider"]
    session["model"] = row["model"]
    session["prompt_tokens"] = int(session["prompt_tokens"]) + prompt_tokens
    session["completion_tokens"] = int(session["completion_tokens"]) + completion_tokens
    session["total_tokens"] = int(session["total_tokens"]) + total_tokens
    session["risk_level"] = row["risk_level"]
    session["exit_code"] = row["exit_code"]
    session["tries"] = int(session["tries"]) + 1
    session["attempts"].append(attempt)
    session["attempts_summary"] = _attempts_summary(session["attempts"])


def fetch_token_summary_by_model(
    conn: sqlite3.Connection,
    *,
    days: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str | None = None,
) -> list[sqlite3.Row]:
    where_sql, params = _build_filters(days=days, date_from=date_from, date_to=date_to, search=search)
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
    search: str | None = None,
) -> list[sqlite3.Row]:
    where_sql, params = _build_filters(days=days, date_from=date_from, date_to=date_to, search=search)
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


def _attempts_summary(attempts: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for position, attempt in enumerate(attempts, start=1):
        attempt_index = _safe_int(attempt.get("attempt_index")) or position
        executed = _safe_int(attempt.get("executed"))
        approved = _safe_int(attempt.get("approved"))
        exit_code = attempt.get("exit_code")
        if executed and exit_code == 0:
            status = "succeeded"
        elif executed:
            status = f"failed (exit {exit_code})"
        elif approved:
            status = "failed"
        else:
            status = "cancelled"
        parts.append(f"{attempt_index} {status}")
    return " · ".join(parts)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ensure_column(conn: sqlite3.Connection, column: str, declaration: str) -> None:
    cur = conn.execute("PRAGMA table_info(execution_logs)")
    columns = {str(row["name"]) for row in cur.fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE execution_logs ADD COLUMN {column} {declaration}")


def _build_filters(
    *,
    days: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str | None = None,
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
    if search and search.strip():
        clauses.append("(lower(user_input) LIKE ? OR lower(generated_command) LIKE ?)")
        search_term = f"%{search.strip().lower()}%"
        params.extend([search_term, search_term])
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
