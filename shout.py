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
import datetime

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
        order_of_slaughter INTEGER NOT NULL,
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
        SELECT id, order_of_slaughter, station, weight, recorded_at
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
        # HTML display page (today's scans)
        # ====================================================

        if parsed.path == "/display":

            now = datetime.datetime.now()
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            seconds_since_midnight = (now - midnight).total_seconds()

            rows = fetch_recent(
                seconds_since_midnight
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

            if qs.get("today"):
                now = datetime.datetime.now()
                midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
                seconds_int = (now - midnight).total_seconds()
            else:
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
                    "order_of_slaughter": r["order_of_slaughter"],
                    "station": r["station"],
                    "weight": int(round(r["weight"])),
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

        live_rows = [r for r in rows if r['station'] == 'live']
        hang_rows = [r for r in rows if r['station'] == 'hang']

        live_rows_html = "".join(
            (
                "<tr>"
                f"<td>{r['order_of_slaughter']}</td>"
                f"<td>{int(round(r['weight']))}</td>"
                f"<td>{html.escape(str(r['recorded_at']))}</td>"
                "</tr>"
            )
            for r in live_rows
        )

        hang_rows_html = "".join(
            (
                "<tr>"
                f"<td>{r['order_of_slaughter']}</td>"
                f"<td>{int(round(r['weight']))}</td>"
                f"<td>{html.escape(str(r['recorded_at']))}</td>"
                "</tr>"
            )
            for r in hang_rows
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
            "margin-bottom:2em;"
            "width:100%;"
            "max-width:600px;"
            "}"

            "th,td{"
            "padding:0.5em 1em;"
            "border:1px solid #ccc;"
            "}"

            "th{"
            "text-align:left;"
            "background-color:#f5f5f5;"
            "}"

            ".tables-container{"
            "display:flex;"
            "gap:3em;"
            "flex-wrap:wrap;"
            "width:100%;"
            "}"

            ".table-column{"
            "flex:1;"
            "min-width:300px;"
            "max-width:600px;"
            "}"
            "</style>"

            "</head>"

            "<body>"

            "<h1>Slaughter Measurements</h1>"

            "<div class='tables-container'>"

            "<div class='table-column'>"
            "<h2>Live Station Scans</h2>"
            "<table>"
            "<tr>"
            "<th>Order</th>"
            "<th>Weight</th>"
            "<th>Recorded At</th>"
            "</tr>"
            "<tbody id='live-table-body'>"
            f"{live_rows_html}"
            "</tbody>"
            "</table>"
            "</div>"

            "<div class='table-column'>"
            "<h2>Hang Station Scans</h2>"
            "<table>"
            "<tr>"
            "<th>Order</th>"
            "<th>Weight</th>"
            "<th>Recorded At</th>"
            "</tr>"
            "<tbody id='hang-table-body'>"
            f"{hang_rows_html}"
            "</tbody>"
            "</table>"
            "</div>"

            "</div>"

            "<script>"
            "function escapeHtml(unsafe) {"
            "    return unsafe"
            "         .replace(/&/g, '&amp;')"
            "         .replace(/</g, '&lt;')"
            "         .replace(/>/g, '&gt;')"
            "         .replace(/\"/g, '&quot;')"
            "         .replace(/'/g, '&#039;');"
            "}"

            "function updateTables() {"
            "    const url = window.location.pathname === '/display' ? '/get?today=true' : '/get';"
            "    fetch(url)"
            "        .then(response => response.json())"
            "        .then(data => {"
            "            const liveTableBody = document.getElementById('live-table-body');"
            "            const hangTableBody = document.getElementById('hang-table-body');"
            "            liveTableBody.innerHTML = '';"
            "            hangTableBody.innerHTML = '';"
            "            const liveRows = data.filter(r => r.station === 'live');"
            "            const hangRows = data.filter(r => r.station === 'hang');"
            "            liveRows.forEach(r => {"
            "                const row = document.createElement('tr');"
            "                row.innerHTML = `<td>${r.order_of_slaughter}</td><td>${r.weight}</td><td>${escapeHtml(r.recorded_at)}</td>`;"
            "                liveTableBody.appendChild(row);"
            "            });"
            "            hangRows.forEach(r => {"
            "                const row = document.createElement('tr');"
            "                row.innerHTML = `<td>${r.order_of_slaughter}</td><td>${r.weight}</td><td>${escapeHtml(r.recorded_at)}</td>`;"
            "                hangTableBody.appendChild(row);"
            "            });"
            "        })"
            "        .catch(error => console.error('Error fetching measurements:', error));"
            "}"

            "setInterval(updateTables, 2000);"
            "</script>"

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