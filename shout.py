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
import math

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

db_dir = os.path.dirname(DB_PATH)
if db_dir:
    os.makedirs(
        db_dir,
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

def get_next_order_of_slaughter(station_name, conn):
    """
    Get the next order number for a given station for the current day.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT MAX(order_of_slaughter)
        FROM measurements
        WHERE station = ? AND DATE(recorded_at) = DATE('now', 'localtime')
        """,
        (station_name.lower(),),
    )
    max_order = cursor.fetchone()[0]
    if max_order is None:
        return 1
    return max_order + 1

def format_indiana_time(date_obj):
    """
    Format a datetime object to Indiana time string.
    """
    if not date_obj:
        return ''
    # This is a simplistic way to handle timezone, for more complex scenarios
    # a library like pytz would be better. Assuming the server is in UTC.
    return (date_obj - datetime.timedelta(hours=5)).strftime('%Y-%m-%d %I:%M:%S %p')

def fetch_recent(seconds=None, date=None):

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    if date:
        query = """
            SELECT id, order_of_slaughter, station, weight, recorded_at
            FROM measurements
            WHERE DATE(recorded_at) = ?
            ORDER BY recorded_at DESC, id DESC
        """
        params = (date,)
    else:
        query = """
            SELECT id, order_of_slaughter, station, weight, recorded_at
            FROM measurements
            WHERE recorded_at >= datetime('now', ?)
            ORDER BY recorded_at DESC, id DESC
        """
        params = (f"-{int(seconds)} seconds",)


    rows = conn.execute(query, params).fetchall()

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
        qs = parse_qs(parsed.query)

        # ====================================================
        # HTML test page
        # ====================================================

        if parsed.path == "/test":

            rows = fetch_recent(
                seconds=DEFAULT_SECONDS
            )

            self._send(
                200,
                self.render_html(rows, page='test'),
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
                seconds=seconds_since_midnight
            )

            self._send(
                200,
                self.render_html(rows, page='display'),
                "text/html; charset=utf-8",
            )

            return

        # ====================================================
        # HTML report page
        # ====================================================

        if parsed.path == "/report":
            
            today_str = datetime.date.today().isoformat()
            
            rows = fetch_recent(
                date=today_str
            )

            self._send(
                200,
                self.render_html(rows, page='report'),
                "text/html; charset=utf-8",
            )

            return

        # ====================================================
        # Test endpoint to clear database
        # ====================================================

        if parsed.path == "/clear_db":
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM measurements")
            conn.commit()
            conn.close()
            self._send(
                200,
                "Database cleared.",
                "text/plain",
            )
            return

        # ====================================================
        # Test endpoint to insert a measurement
        # ====================================================

        if parsed.path == "/test_insert":
            station = qs.get("station", [None])[0]
            weight = qs.get("weight", [None])[0]
            recorded_at = qs.get("recorded_at", [None])[0]

            if not station or not weight:
                self._send(
                    400,
                    "station and weight are required",
                    "text/plain",
                )
                return

            conn = sqlite3.connect(DB_PATH)
            order_of_slaughter = get_next_order_of_slaughter(station, conn)

            if recorded_at:
                conn.execute(
                    """
                    INSERT INTO measurements
                        (order_of_slaughter, station, weight, recorded_at)
                    VALUES
                        (?, ?, ?, ?)
                    """,
                    (
                        order_of_slaughter,
                        station,
                        float(weight),
                        recorded_at,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO measurements
                        (order_of_slaughter, station, weight)
                    VALUES
                        (?, ?, ?)
                    """,
                    (
                        order_of_slaughter,
                        station,
                        float(weight),
                    ),
                )
            
            conn.commit()
            conn.close()

            self._send(
                200,
                "Measurement inserted.",
                "text/plain",
            )

            return
            
        # ====================================================
        # JSON API
        # ====================================================

        if parsed.path == "/get":

            if date_str := qs.get("date", [None])[0]:
                 rows = fetch_recent(date=date_str)
            elif qs.get("today"):
                now = datetime.datetime.now()
                midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
                seconds_int = (now - midnight).total_seconds()
                rows = fetch_recent(seconds=seconds_int)
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
                    seconds=seconds_int
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
    def render_html(rows, page='test'):

        live_rows = [r for r in rows if r['station'] == 'live']
        hang_rows = [r for r in rows if r['station'] == 'hang']

        def parse_db_date(date_str):
            try:
                # SQLite datetime('now') returns YYYY-MM-DD HH:MM:SS
                # But handle possible 'T' separator just in case.
                clean_str = date_str.replace('T', ' ')
                return datetime.datetime.strptime(clean_str, '%Y-%m-%d %H:%M:%S')
            except Exception:
                return None

        live_rows_list = []
        last_live_time = None
        for r in live_rows:
            current_time = parse_db_date(r['recorded_at'])
            if last_live_time and current_time:
                # In descending order, last_live_time is newer than current_time.
                if (last_live_time - current_time).total_seconds() > 60:
                    live_rows_list.append("<tr class='time-gap'><td colspan='3'></td></tr>")
            
            row_class = 'even-row' if r['order_of_slaughter'] % 2 == 0 else ''
            live_rows_list.append(
                f"<tr class=\"{row_class}\">"
                f"<td>{r['order_of_slaughter']}</td>"
                f"<td>{int(round(r['weight']))}</td>"
                f"<td>{html.escape(format_indiana_time(current_time))}</td>"
                "</tr>"
            )
            if current_time:
                last_live_time = current_time
        live_rows_html = "".join(live_rows_list)

        hang_rows_list = []
        last_hang_time = None
        for r in hang_rows:
            current_time = parse_db_date(r['recorded_at'])
            if last_hang_time and current_time:
                if (last_hang_time - current_time).total_seconds() > 60:
                    hang_rows_list.append("<tr class='time-gap'><td colspan='3'></td></tr>")
            
            row_class = 'row-pair-colored' if math.ceil(r['order_of_slaughter'] / 2) % 2 == 0 else ''
            hang_rows_list.append(
                f"<tr class=\"{row_class}\">"
                f"<td>{r['order_of_slaughter']}</td>"
                f"<td>{int(round(r['weight']))}</td>"
                f"<td>{html.escape(format_indiana_time(current_time))}</td>"
                "</tr>"
            )
            if current_time:
                last_hang_time = current_time
        hang_rows_html = "".join(hang_rows_list)

        report_controls_html = ""
        if page == 'report':
            report_controls_html = """
                <div class="controls">
                    <label for="report-date">Select Date:</label>
                    <input type="date" id="report-date">
                    <button id="export-btn">Export to Excel</button>
                </div>
            """

        return (
            "<!doctype html>"
            "<html>"
            "<head>"
            "<meta charset='utf-8'>"
            "<title>Slaughter Measurements</title>"
            "<script src=\"https://cdn.jsdelivr.net/npm/xlsx/dist/xlsx.full.min.js\"></script>"

            "<style>"
            "body{"
            "font:14px monospace;"
            "margin:2em;"
            "}"

            "table{"
            "border-collapse:collapse;"
            "margin-bottom:2em;"
            "width:100%;"
            "}"

            "th,td{"
            "padding:0.5em 1em;"
            "border:1px solid #ccc;"
            "}"

            "th{"
            "text-align:left;"
            "background-color:#343a40;"
            "color:#ffffff;"
            "border-bottom:3px solid #1a1a1a;"
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

            ".time-gap td {"
            "    background-color: #000;"
            "    padding: 4px 0;"
            "    border: none;"
            "}"

            ".even-row, .row-pair-colored {"
            "background-color: #f9f9f9;"
            "}"

            ".controls {"
            "margin-bottom: 2em;"
            "display: flex;"
            "gap: 1em;"
            "align-items: center;"
            "}"
            "</style>"

            "</head>"

            "<body>"

            "<h1>Slaughter Measurements</h1>"
            
            f"{report_controls_html}"

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

            "function formatIndianaTime(utcString) {"
            "    if (!utcString) return '';"
            "    const date = new Date(utcString + 'Z');"
            "    const options = {"
            "        timeZone: 'America/New_York',"
            "        year: 'numeric',"
            "        month: '2-digit',"
            "        day: '2-digit',"
            "        hour: '2-digit',"
            "        minute: '2-digit',"
            "        second: '2-digit',"
            "        hour12: true"
            "    };"
            "    return new Intl.DateTimeFormat('en-US', options).format(date);"
            "}"

            "function updateTables(url) {"
            "    return fetch(url)"
            "        .then(response => response.json())"
            "        .then(data => {"
            "            const liveTableBody = document.getElementById('live-table-body');"
            "            const hangTableBody = document.getElementById('hang-table-body');"
            "            liveTableBody.innerHTML = '';"
            "            hangTableBody.innerHTML = '';"
            "            const liveRows = data.filter(r => r.station === 'live');"
            "            const hangRows = data.filter(r => r.station === 'hang');"
            "            let lastLiveTime = null;"
            "            liveRows.forEach(r => {"
            "                const currentTime = new Date(r.recorded_at + 'Z');"
            "                if (lastLiveTime && (lastLiveTime - currentTime) > 60000) {"
            "                    const gapRow = document.createElement('tr');"
            "                    gapRow.className = 'time-gap';"
            "                    gapRow.innerHTML = `<td colspan='3'></td>`;"
            "                    liveTableBody.appendChild(gapRow);"
            "                }"
            "                const row = document.createElement('tr');"
            "                if (r.order_of_slaughter % 2 === 0) {"
            "                    row.className = 'even-row';"
            "                }"
            "                row.innerHTML = `<td>${r.order_of_slaughter}</td><td>${r.weight}</td><td>${formatIndianaTime(r.recorded_at)}</td>`;"
            "                liveTableBody.appendChild(row);"
            "                lastLiveTime = currentTime;"
            "            });"
            "            let lastHangTime = null;"
            "            hangRows.forEach(r => {"
            "                const currentTime = new Date(r.recorded_at + 'Z');"
            "                if (lastHangTime && (lastHangTime - currentTime) > 60000) {"
            "                    const gapRow = document.createElement('tr');"
            "                    gapRow.className = 'time-gap';"
            "                    gapRow.innerHTML = `<td colspan='3'></td>`;"
            "                    hangTableBody.appendChild(gapRow);"
            "                }"
            "                const row = document.createElement('tr');"
            "                const group = Math.ceil(r.order_of_slaughter / 2);"
            "                if (group % 2 === 0) {"
            "                    row.className = 'row-pair-colored';"
            "                }"
            "                row.innerHTML = `<td>${r.order_of_slaughter}</td><td>${r.weight}</td><td>${formatIndianaTime(r.recorded_at)}</td>`;"
            "                hangTableBody.appendChild(row);"
            "                lastHangTime = currentTime;"
            "            });"
            "            return data;"
            "        })"
            "        .catch(error => console.error('Error fetching measurements:', error));"
            "}"
            
            "const page = '" + page + "';"

            "if (page === 'report') {"
            "    const dateInput = document.getElementById('report-date');"
            "    const exportBtn = document.getElementById('export-btn');"
            "    const today = new Date();"
            "    const yyyy = today.getFullYear();"
            "    const mm = String(today.getMonth() + 1).padStart(2, '0');"
            "    const dd = String(today.getDate()).padStart(2, '0');"
            "    dateInput.value = `${yyyy}-${mm}-${dd}`;"

            "    dateInput.addEventListener('change', () => {"
            "        updateTables(`/get?date=${dateInput.value}`);"
            "    });"

            "    exportBtn.addEventListener('click', () => {"
            "        const date = dateInput.value;"
            "        updateTables(`/get?date=${date}`).then(data => {"
            "            if (!data) return;"
            "            const liveRows = data.filter(r => r.station === 'live');"
            "            const hangRows = data.filter(r => r.station === 'hang');"
            "            const liveSheet = [['Order', 'Weight', 'Recorded At']];"
            "            liveRows.forEach(r => liveSheet.push([r.order_of_slaughter, r.weight, formatIndianaTime(r.recorded_at)]));"
            "            const hangSheet = [['Order', 'Weight', 'Recorded At']];"
            "            hangRows.forEach(r => hangSheet.push([r.order_of_slaughter, r.weight, formatIndianaTime(r.recorded_at)]));"
            "            const wb = XLSX.utils.book_new();"
            "            const wsLive = XLSX.utils.aoa_to_sheet(liveSheet);"
            "            const wsHang = XLSX.utils.aoa_to_sheet(hangSheet);"
            "            XLSX.utils.book_append_sheet(wb, wsLive, 'Live Scans');"
            "            XLSX.utils.book_append_sheet(wb, wsHang, 'Hang Scans');"
            "            XLSX.writeFile(wb, `slaughter_report_${date}.xlsx`);"
            "        });"
            "    });"

            "    updateTables(`/get?date=${dateInput.value}`);"
            "} else {"
            "    const initialUrl = page === 'display' ? '/get?today=true' : '/get';"
            "    updateTables(initialUrl);"
            "    setInterval(() => updateTables(initialUrl), 2000);"
            "}"

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