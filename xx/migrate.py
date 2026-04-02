from __future__ import annotations

from datetime import datetime, timezone

from xx.storage import connect


def migrate_timestamps_to_local(database_path) -> tuple[int, int]:
    conn = connect(database_path)
    updated = 0
    scanned = 0
    try:
        rows = conn.execute("SELECT id, invoked_at FROM execution_logs ORDER BY id ASC").fetchall()
        for row in rows:
            scanned += 1
            original = row["invoked_at"]
            converted = _utc_naive_to_local_naive(original)
            if converted == original:
                continue
            conn.execute(
                "UPDATE execution_logs SET invoked_at = ? WHERE id = ?",
                (converted, row["id"]),
            )
            updated += 1
        conn.commit()
    finally:
        conn.close()
    return scanned, updated


def _utc_naive_to_local_naive(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    utc_dt = parsed.replace(tzinfo=timezone.utc)
    local_dt = utc_dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M:%S")
