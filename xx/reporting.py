from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from xx.storage import (
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
            days = _read_days(parsed.query, config.default_report_days)
            conn = connect(config.database_path)
            try:
                prune_old_records(conn, config.retention_days)
                if parsed.path == "/report":
                    rows = fetch_executions(conn, days)
                    by_model = fetch_token_summary_by_model(conn, days)
                    by_provider = fetch_token_summary_by_provider(conn, days)
                    body = _render_html(days, rows, by_model, by_provider)
                    self._write_response(200, body, "text/html; charset=utf-8")
                    return
                if parsed.path == "/api/executions":
                    rows = [dict(row) for row in fetch_executions(conn, days)]
                    self._write_json({"days": days, "rows": rows})
                    return
                if parsed.path == "/api/tokens/by-model":
                    rows = [dict(row) for row in fetch_token_summary_by_model(conn, days)]
                    self._write_json({"days": days, "rows": rows})
                    return
                if parsed.path == "/api/tokens/by-provider":
                    rows = [dict(row) for row in fetch_token_summary_by_provider(conn, days)]
                    self._write_json({"days": days, "rows": rows})
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


def _read_days(query: str, default_days: int) -> int:
    parsed = parse_qs(query)
    value = parsed.get("days", [str(default_days)])[0]
    try:
        days = int(value)
    except ValueError:
        return default_days
    return max(1, days)


def _render_html(days: int, executions: list[dict], by_model: list[dict], by_provider: list[dict]) -> str:
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
    <p>Showing the last {days} day(s).</p>
    <section>
      <h2>Executions</h2>
      <table>
        <thead>
          <tr><th>When</th><th>User input</th><th>Command</th><th>Executed</th><th>Provider</th><th>Model</th><th>Total tokens</th></tr>
        </thead>
        <tbody>{execution_rows}</tbody>
      </table>
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
