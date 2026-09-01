#!/usr/bin/python3
"""
Share collected slaughter measurements over HTTP.

Endpoints:
    GET /test
        HTML table of recent measurements.

    GET /get[?seconds=N]
        JSON array of measurements recorded in the last N seconds.

        N defaults to 43200 seconds (12 hours)
        and must be a non-negative integer.

Environment:
    DB_PATH
        SQLite database.
        Default: /var/lib/slaughter/measurements.db

    PORT
        TCP port to listen on.
        Default: 80
"""

import html
import json
import os
import sqlite3

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


# ============================================================
# Configuration
# ============================================================

DB_PATH = os.environ.get(
    "DB_PATH",
    "/var/lib/slaughter/measurements.db",
)

PORT = int(
    os.environ.get(
        "PORT",
        "80",
    )
)

DEFAULT_SECONDS = 43200


# ============================================================
# Database initialization
# ============================================================

os.makedirs(
    os.path.dirname(DB_PATH),
    exist_ok=True,
)

db_init = sqlite3.connect(DB_PATH)

db_init.execute(
    """
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        animal_id TEXT NOT NULL,
        station TEXT NOT NULL,
        weight REAL NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)

db_init.commit()
db_init.close()


# ============================================================
# Database queries
# ============================================================

def fetch_recent(seconds):

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT id, animal_id, station, weight, recorded_at
        FROM measurements
        WHERE recorded_at >= datetime('now', ?)
        ORDER BY recorded_at DESC, id DESC
        """,
        (
            f"-{int(seconds)} seconds",
        ),
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# HTTP Handler
# ============================================================

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Keep normal HTTP request logging quiet.
        # listen.py already logs measurement captures.
        pass

    # --------------------------------------------------------
    # Send HTTP response
    # --------------------------------------------------------

    def _send(
        self,
        status,
        body,
        content_type,
    ):

        data = body.encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            content_type,
        )

        self.send_header(
            "Content-Length",
            str(len(data)),
        )

        self.end_headers()

        self.wfile.write(data)

    # --------------------------------------------------------
    # GET requests
    # --------------------------------------------------------

    def do_GET(self):

        parsed = urlparse(self.path)

        # ====================================================
        # HTML test page
        # ====================================================

        if parsed.path == "/test":

            rows = fetch_recent(
                DEFAULT_SECONDS
            )

            self._send(
                200,
                self.render_html(rows),
                "text/html; charset=utf-8",
            )

            return

        # ====================================================
        # JSON API
        # ====================================================

        if parsed.path == "/get":

            qs = parse_qs(
                parsed.query
            )

            seconds = qs.get(
                "seconds",
                [str(DEFAULT_SECONDS)],
            )[0]

            try:

                seconds_int = int(
                    seconds
                )

                if seconds_int < 0:
                    raise ValueError

            except (TypeError, ValueError):

                self._send(
                    400,
                    json.dumps(
                        {
                            "error":
                            "seconds must be a non-negative integer"
                        }
                    ),
                    "application/json",
                )

                return

            rows = fetch_recent(
                seconds_int
            )

            payload = [
                {
                    "id": r["id"],
                    "animal_id": r["animal_id"],
                    "station": r["station"],
                    "weight": r["weight"],
                    "recorded_at": r["recorded_at"],
                }
                for r in rows
            ]

            self._send(
                200,
                json.dumps(
                    payload,
                    indent=2,
                ),
                "application/json",
            )

            return

        # ====================================================
        # Not found
        # ====================================================

        self._send(
            404,
            "not found",
            "text/plain",
        )

    # --------------------------------------------------------
    # HTML page
    # --------------------------------------------------------

    @staticmethod
    def render_html(rows):

        rows_html = "".join(
            (
                "<tr>"
                f"<td>{r['id']}</td>"
                f"<td>{html.escape(str(r['animal_id']))}</td>"
                f"<td>{html.escape(str(r['station']))}</td>"
                f"<td>{r['weight']}</td>"
                f"<td>{html.escape(str(r['recorded_at']))}</td>"
                "</tr>"
            )
            for r in rows
        )

        return (
            "<!doctype html>"
            "<html>"
            "<head>"
            "<meta charset='utf-8'>"
            "<title>Slaughter Measurements</title>"

            "<style>"
            "body{"
            "font:14px monospace;"
            "margin:2em;"
            "}"

            "table{"
            "border-collapse:collapse;"
            "}"

            "th,td{"
            "padding:0.5em 1em;"
            "border:1px solid #ccc;"
            "}"

            "th{"
            "text-align:left;"
            "}"
            "</style>"

            "</head>"

            "<body>"

            "<h1>Slaughter Measurements</h1>"

            "<table>"

            "<tr>"
            "<th>ID</th>"
            "<th>Animal</th>"
            "<th>Station</th>"
            "<th>Weight</th>"
            "<th>Recorded At</th>"
            "</tr>"

            f"{rows_html}"

            "</table>"

            "</body>"
            "</html>"
        )


# ============================================================
# Start server
# ============================================================

if __name__ == "__main__":

    server = ThreadingHTTPServer(
        (
            "0.0.0.0",
            PORT,
        ),
        Handler,
    )

    print(
        f"shout: serving on :{PORT} "
        f"(db={DB_PATH})",
        flush=True,
    )

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        print(
            "\nshout: stopping",
            flush=True,
        )

    finally:

        server.server_close()