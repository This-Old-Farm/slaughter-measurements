#!/usr/bin/python3
"""
Share collected measurements over HTTP.

Endpoints:
    GET /test                HTML table of all measurements.
    GET /get[?seconds=N]     JSON array of measurements recorded in the
                             last N seconds. N defaults to 43200 (12h)
                             and must be a non-negative integer.

Environment:
    DB_PATH   SQLite database (default /var/lib/slaughter/measurements.db)
    PORT      TCP port to listen on (default 80)
"""

import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DB_PATH = os.environ.get("DB_PATH", "/var/lib/slaughter/measurements.db")
PORT = int(os.environ.get("PORT", "80"))
DEFAULT_SECONDS = 43200

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
db_init = sqlite3.connect(DB_PATH)
db_init.execute(
    """
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        animal_id TEXT NOT NULL,
        weight REAL NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)
db_init.commit()
db_init.close()


def fetch_recent(seconds):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT animal_id, weight, recorded_at
        FROM measurements
        WHERE recorded_at >= datetime('now', ?)
        ORDER BY recorded_at DESC, id DESC
        """,
        (f"-{int(seconds)} seconds",),
    ).fetchall()
    conn.close()
    return rows


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # keep the console quiet; listen.py already logs captures
        pass

    def _send(self, status, body, content_type):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/test":
            rows = fetch_recent(DEFAULT_SECONDS)
            self._send(200, self.render_html(rows), "text/html; charset=utf-8")
            return

        if parsed.path == "/get":
            qs = parse_qs(parsed.query)
            seconds = qs.get("seconds", [str(DEFAULT_SECONDS)])[0]
            try:
                seconds_int = int(seconds)
                if seconds_int < 0:
                    raise ValueError
            except (TypeError, ValueError):
                self._send(
                    400,
                    json.dumps({"error": "seconds must be a non-negative integer"}),
                    "application/json",
                )
                return
            rows = fetch_recent(seconds_int)
            payload = [
                {"animal_id": r["animal_id"], "weight": r["weight"],
                 "recorded_at": r["recorded_at"]}
                for r in rows
            ]
            self._send(200, json.dumps(payload), "application/json")
            return

        self._send(404, "not found", "text/plain")

    @staticmethod
    def render_html(rows):
        rows_html = "".join(
            f"<tr><td>{r['animal_id']}</td><td>{r['weight']}</td>"
            f"<td>{r['recorded_at']}</td></tr>"
            for r in rows
        )
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>measurements</title>"
            "<style>body{font:14px monospace}td{padding:0 1em}</style>"
            "</head><body>"
            "<table><tr><th>animal</th><th>weight</th><th>at</th></tr>"
            f"{rows_html}"
            "</table></body></html>"
        )


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"shout: serving on :{PORT} (db={DB_PATH})", flush=True)
    server.serve_forever()
