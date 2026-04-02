from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import ceil
from urllib.parse import parse_qs, urlencode, urlparse

from xx.storage import (
    count_executions,
    connect,
    fetch_executions,
    fetch_token_summary_by_model,
    fetch_token_summary_by_provider,
    prune_old_records,
)
from xx.types import ReportingConfig


def serve_report(config: ReportingConfig) -> int:
    server = ThreadingHTTPServer((config.host, config.port), _make_handler(config))
    print(f"Serving report on http://{config.host}:{config.port}/report")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _make_handler(config: ReportingConfig):
    class ReportHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            filters = _read_filters(parsed.query, config.default_report_days)
            conn = connect(config.database_path)
            try:
                prune_old_records(conn, config.retention_days)
                if parsed.path == "/report":
                    total = count_executions(
                        conn,
                        days=filters["days"],
                        date_from=filters["from"],
                        date_to=filters["to"],
                    )
                    pagination = _pagination_payload(total, filters["page"], filters["page_size"])
                    rows = fetch_executions(
                        conn,
                        days=filters["days"],
                        date_from=filters["from"],
                        date_to=filters["to"],
                        page=pagination["page"],
                        page_size=filters["page_size"],
                    )
                    by_model = fetch_token_summary_by_model(
                        conn,
                        days=filters["days"],
                        date_from=filters["from"],
                        date_to=filters["to"],
                    )
                    by_provider = fetch_token_summary_by_provider(
                        conn,
                        days=filters["days"],
                        date_from=filters["from"],
                        date_to=filters["to"],
                    )
                    body = _render_html({**filters, "page": pagination["page"]}, total, rows, by_model, by_provider)
                    self._write_response(200, body, "text/html; charset=utf-8")
                    return
                if parsed.path == "/api/executions":
                    total = count_executions(
                        conn,
                        days=filters["days"],
                        date_from=filters["from"],
                        date_to=filters["to"],
                    )
                    pagination = _pagination_payload(total, filters["page"], filters["page_size"])
                    rows = [
                        dict(row)
                        for row in fetch_executions(
                            conn,
                            days=filters["days"],
                            date_from=filters["from"],
                            date_to=filters["to"],
                            page=pagination["page"],
                            page_size=filters["page_size"],
                        )
                    ]
                    self._write_json(
                        {
                            "filters": filters,
                            "pagination": pagination,
                            "rows": rows,
                        }
                    )
                    return
                if parsed.path == "/api/tokens/by-model":
                    rows = [
                        dict(row)
                        for row in fetch_token_summary_by_model(
                            conn,
                            days=filters["days"],
                            date_from=filters["from"],
                            date_to=filters["to"],
                        )
                    ]
                    self._write_json({"filters": filters, "rows": rows})
                    return
                if parsed.path == "/api/tokens/by-provider":
                    rows = [
                        dict(row)
                        for row in fetch_token_summary_by_provider(
                            conn,
                            days=filters["days"],
                            date_from=filters["from"],
                            date_to=filters["to"],
                        )
                    ]
                    self._write_json({"filters": filters, "rows": rows})
                    return
                self._write_json({"error": "Not found"}, status=404)
            finally:
                conn.close()

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _write_json(self, payload: dict, status: int = 200) -> None:
            self._write_response(status, json.dumps(payload, indent=2), "application/json")

        def _write_response(self, status: int, body: str, content_type: str) -> None:
            encoded = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return ReportHandler


def _read_filters(query: str, default_days: int) -> dict:
    parsed = parse_qs(query)
    days_value = parsed.get("days", [str(default_days)])[0]
    date_from = _normalize_datetime_input(parsed.get("from", [""])[0])
    date_to = _normalize_datetime_input(parsed.get("to", [""])[0])
    page = _read_positive_int(parsed.get("page", ["1"])[0], 1)
    page_size = _read_positive_int(parsed.get("page_size", ["50"])[0], 50)
    page_size = min(page_size, 200)
    try:
        days = int(days_value)
    except ValueError:
        days = default_days
    return {
        "days": max(1, days),
        "from": date_from,
        "to": date_to,
        "page": page,
        "page_size": page_size,
    }


def _read_positive_int(raw: str, default: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


def _normalize_datetime_input(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    return value.replace(" ", "T")


def _pagination_payload(total: int, page: int, page_size: int) -> dict:
    total_pages = max(1, ceil(total / page_size)) if total else 1
    safe_page = min(page, total_pages)
    return {
        "page": safe_page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_prev": safe_page > 1,
        "has_next": safe_page < total_pages,
    }


def _render_html(filters: dict, total: int, executions: list[dict], by_model: list[dict], by_provider: list[dict]) -> str:
    pagination = _pagination_payload(total, filters["page"], filters["page_size"])
    execution_rows = "".join(
        (
            "<tr>"
            f"<td>{html.escape(str(row['invoked_at']))}</td>"
            f"<td>{html.escape(str(row['user_input']))}</td>"
            f"<td><code>{html.escape(str(row['generated_command']))}</code></td>"
            f"<td>{'yes' if row['executed'] else 'no'}</td>"
            f"<td>{html.escape(str(row['provider']))}</td>"
            f"<td>{html.escape(str(row['model']))}</td>"
            f"<td>{html.escape(str(row['total_tokens'] or 0))}</td>"
            "</tr>"
        )
        for row in executions
    )
    by_model_rows = "".join(
        (
            "<tr>"
            f"<td>{html.escape(str(row['provider']))}</td>"
            f"<td>{html.escape(str(row['model']))}</td>"
            f"<td>{html.escape(str(row['invocations']))}</td>"
            f"<td>{html.escape(str(row['total_tokens']))}</td>"
            "</tr>"
        )
        for row in by_model
    )
    by_provider_rows = "".join(
        (
            "<tr>"
            f"<td>{html.escape(str(row['provider']))}</td>"
            f"<td>{html.escape(str(row['invocations']))}</td>"
            f"<td>{html.escape(str(row['total_tokens']))}</td>"
            "</tr>"
        )
        for row in by_provider
    )
    query_base = {
        "from": filters["from"] or "",
        "to": filters["to"] or "",
        "days": filters["days"],
        "page_size": filters["page_size"],
    }
    prev_link = ""
    if pagination["has_prev"]:
        prev_query = urlencode({**query_base, "page": pagination["page"] - 1})
        prev_link = f'<a href="/report?{prev_query}">Previous</a>'
    next_link = ""
    if pagination["has_next"]:
        next_query = urlencode({**query_base, "page": pagination["page"] + 1})
        next_link = f'<a href="/report?{next_query}">Next</a>'
    active_window = (
        f"{html.escape(filters['from'])} to {html.escape(filters['to'])}"
        if filters["from"] or filters["to"]
        else f"last {filters['days']} day(s)"
    )
    page_size_form_query = {
        "from": filters["from"] or "",
        "to": filters["to"] or "",
        "days": filters["days"],
        "page": 1,
    }
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>xx report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4efe6;
      --panel: #fffdf8;
      --ink: #1f2a30;
      --accent: #8a3b12;
      --line: #d9ccb6;
    }}
    body {{
      margin: 0;
      font-family: Georgia, "Iowan Old Style", serif;
      background: linear-gradient(180deg, #efe4d3 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    main {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    h1, h2 {{ margin-bottom: 8px; }}
    p {{ margin-top: 0; }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 18px;
      margin-top: 18px;
      box-shadow: 0 8px 30px rgba(31, 42, 48, 0.06);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    form {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: end;
    }}
    .field {{
      min-width: 180px;
      max-width: 240px;
      flex: 0 1 240px;
    }}
    label {{
      font-size: 13px;
      color: var(--accent);
      display: block;
      margin-bottom: 6px;
    }}
    input {{
      width: 100%;
      box-sizing: border-box;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      color: var(--ink);
    }}
    .actions {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex: 0 0 auto;
    }}
    .button {{
      width: auto;
      min-width: 140px;
      padding: 10px 18px;
      border-radius: 999px;
      border: 1px solid #9d4a1d;
      background: linear-gradient(180deg, #b95a22 0%, #8a3b12 100%);
      color: #fff;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 10px 24px rgba(138, 59, 18, 0.18);
    }}
    .button.secondary {{
      min-width: 96px;
      border-color: var(--line);
      background: #fff;
      color: var(--ink);
      box-shadow: none;
    }}
    .table-controls {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-top: 14px;
      flex-wrap: wrap;
    }}
    .page-size-form {{
      display: flex;
      align-items: end;
      gap: 10px;
    }}
    .page-size-form .field {{
      min-width: 96px;
      max-width: 120px;
    }}
    .pager {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 14px;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .pager a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 600;
    }}
    th, td {{
      border-top: 1px solid var(--line);
      padding: 10px 8px;
      text-align: left;
      vertical-align: top;
    }}
    th {{ color: var(--accent); }}
    code {{
      font-family: "SFMono-Regular", Menlo, monospace;
      white-space: pre-wrap;
      word-break: break-word;
    }}
  </style>
</head>
<body>
  <main>
    <h1>xx execution report</h1>
    <p>Showing {active_window}.</p>
    <section>
      <h2>Filters</h2>
      <form method="get" action="/report">
        <div class="field">
          <label for="from">From (24h)</label>
          <input id="from" name="from" type="text" placeholder="YYYY-MM-DD HH:MM" value="{html.escape((filters['from'] or '').replace('T', ' '))}">
        </div>
        <div class="field">
          <label for="to">To (24h)</label>
          <input id="to" name="to" type="text" placeholder="YYYY-MM-DD HH:MM" value="{html.escape((filters['to'] or '').replace('T', ' '))}">
        </div>
        <div class="actions">
          <input type="hidden" name="page" value="1">
          <input type="hidden" name="page_size" value="{filters['page_size']}">
          <input type="hidden" name="days" value="{filters['days']}">
          <input class="button" type="submit" value="Apply">
        </div>
      </form>
    </section>
    <section>
      <h2>Executions</h2>
      <table>
        <thead>
          <tr><th>When</th><th>User input</th><th>Command</th><th>Executed</th><th>Provider</th><th>Model</th><th>Total tokens</th></tr>
        </thead>
        <tbody>{execution_rows}</tbody>
      </table>
      <div class="table-controls">
        <form class="page-size-form" method="get" action="/report">
          <div class="field">
            <label for="page_size">Page size</label>
            <input id="page_size" name="page_size" type="number" min="1" max="200" value="{filters['page_size']}">
          </div>
          <input type="hidden" name="page" value="1">
          <input type="hidden" name="from" value="{html.escape(filters['from'] or '')}">
          <input type="hidden" name="to" value="{html.escape(filters['to'] or '')}">
          <input type="hidden" name="days" value="{filters['days']}">
          <input class="button secondary" type="submit" value="Update">
        </form>
        <div class="pager">
          <div>Page {pagination['page']} of {pagination['total_pages']} · {pagination['total']} total row(s)</div>
          <div>{prev_link} {" " if prev_link and next_link else ""}{next_link}</div>
        </div>
      </div>
    </section>
    <section>
      <h2>Tokens by model</h2>
      <table>
        <thead>
          <tr><th>Provider</th><th>Model</th><th>Invocations</th><th>Total tokens</th></tr>
        </thead>
        <tbody>{by_model_rows}</tbody>
      </table>
    </section>
    <section>
      <h2>Tokens by provider</h2>
      <table>
        <thead>
          <tr><th>Provider</th><th>Invocations</th><th>Total tokens</th></tr>
        </thead>
        <tbody>{by_provider_rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>"""
