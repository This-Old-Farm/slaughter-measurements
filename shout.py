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

    GET /matches[?date=YYYY-MM-DD]
        JSON of live animals matched to hang tags for a day
        (default today), with QA flags. See matching.py.

    GET /live, /live/a, /live/b, /live/c
        Candidate live-station pages (#62), all fed by
        GET /live_status: the animal being scanned now and today's
        live animals. See live_status.py.

    GET, POST /practice
        The fake scales' settings while practice.py is running, for
        the practice panel on /live. Not found otherwise.

Environment:
    DB_PATH
        SQLite database.
        Default: /var/lib/slaughter/measurements.db

    PORT
        TCP port to listen on.
        Default: 80

    PRACTICE_CONTROL
        Set by practice.py: the JSON file its fake scales read their
        settings from. Unset outside practice.
"""

import datetime
import html
import json
import math
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import live_status
import local_time
import matching
import schema

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

PRACTICE_CONTROL = os.environ.get("PRACTICE_CONTROL")

# Requests are handled on threads; one practice update at a time.
practice_lock = threading.Lock()

# Static pages, read on each request so they can be edited in place.
PAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pages")

# The candidate live-station pages, by URL. Once the operator picks
# one, the others are deleted (#62).
LIVE_PAGES = {
    "/live": "live.html",
    "/live/a": "live-a.html",
    "/live/b": "live-b.html",
    "/live/c": "live-c.html",
}


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

schema.init(db_init)
db_init.close()


# ============================================================
# Database queries
# ============================================================


def get_next_order_of_slaughter(station_name, conn):
    """
    Get the next order number for a given station for the current
    Indiana day.
    """
    start, end = local_time.utc_bounds(local_time.local_today())
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT MAX(order_of_slaughter)
        FROM measurements
        WHERE station = ? AND recorded_at >= ? AND recorded_at < ?
        """,
        (station_name.lower(), start, end),
    )
    max_order = cursor.fetchone()[0]
    if max_order is None:
        return 1
    return max_order + 1


def format_indiana_time(date_obj):
    """
    Format a naive UTC datetime read from recorded_at as an Indiana
    time string.
    """
    if not date_obj:
        return ""
    return local_time.to_local(date_obj).strftime("%Y-%m-%d %I:%M:%S %p")


def fetch_recent(seconds=None, date=None):
    """
    Fetch measurements from the last N seconds, or from one Indiana
    date (a date or YYYY-MM-DD string; raises ValueError if invalid).
    """

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    if date:
        query = """
            SELECT id, order_of_slaughter, scan_code, station, weight, recorded_at
            FROM measurements
            WHERE recorded_at >= ? AND recorded_at < ?
            ORDER BY recorded_at DESC, id DESC
        """
        params = local_time.utc_bounds(date)
    else:
        query = """
            SELECT id, order_of_slaughter, scan_code, station, weight, recorded_at
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
    # Practice controls
    # --------------------------------------------------------

    def read_practice(self):
        with open(PRACTICE_CONTROL, encoding="utf-8") as control:
            return json.load(control)

    def do_POST(self):

        if urlparse(self.path).path != "/practice" or not PRACTICE_CONTROL:
            self._send(404, "not found", "text/plain")
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            change = json.loads(self.rfile.read(length))

            with practice_lock:
                state = self.read_practice()

                if "auto" in change:
                    state["auto"] = bool(change["auto"])

                # Each override gets a new number, so practice.py
                # applies it exactly once.
                if "station" in change:
                    if change["station"] not in ("live", "hang"):
                        raise ValueError("station must be live or hang")

                    state["override"] = {
                        "station": change["station"],
                        "weight": float(change["weight"]),
                    }
                    state["seq"] += 1

                temporary = PRACTICE_CONTROL + ".tmp"

                with open(temporary, "w", encoding="utf-8") as control:
                    json.dump(state, control)

                os.replace(temporary, PRACTICE_CONTROL)

        except (KeyError, TypeError, ValueError) as error:
            self._send(400, json.dumps({"error": str(error)}), "application/json")
            return

        self._send(200, json.dumps(state), "application/json")

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
            rows = fetch_recent(seconds=DEFAULT_SECONDS)

            self._send(
                200,
                self.render_html(rows, page="test"),
                "text/html; charset=utf-8",
            )

            return

        # ====================================================
        # HTML display page (today's scans)
        # ====================================================

        if parsed.path == "/display":
            rows = fetch_recent(date=local_time.local_today())

            self._send(
                200,
                self.render_html(rows, page="display"),
                "text/html; charset=utf-8",
            )

            return

        # ====================================================
        # Live-station pages
        # ====================================================

        if parsed.path in LIVE_PAGES:
            with open(os.path.join(PAGES_DIR, LIVE_PAGES[parsed.path]), encoding="utf-8") as page:
                self._send(
                    200,
                    page.read(),
                    "text/html; charset=utf-8",
                )

            return

        if parsed.path == "/practice":
            if not PRACTICE_CONTROL:
                self._send(404, "not found", "text/plain")
                return

            with practice_lock:
                state = self.read_practice()

            self._send(200, json.dumps(state), "application/json")

            return

        if parsed.path == "/live_status":
            conn = sqlite3.connect(DB_PATH)

            try:
                status = live_status.live_status(conn, local_time.local_today().isoformat())

            finally:
                conn.close()

            self._send(
                200,
                json.dumps(status),
                "application/json",
            )

            return

        # ====================================================
        # HTML report page
        # ====================================================

        if parsed.path == "/report":
            rows = fetch_recent(date=local_time.local_today())

            self._send(
                200,
                self.render_html(rows, page="report"),
                "text/html; charset=utf-8",
            )

            return

        # ====================================================
        # Test endpoint to clear database
        # ====================================================

        if parsed.path == "/clear_db":
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM animal_details")
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
            scan_code = qs.get("scan_code", [""])[0]

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
                        (order_of_slaughter, scan_code, station, weight, recorded_at)
                    VALUES
                        (?, ?, ?, ?, ?)
                    """,
                    (
                        order_of_slaughter,
                        scan_code,
                        station,
                        float(weight),
                        recorded_at,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO measurements
                        (order_of_slaughter, scan_code, station, weight)
                    VALUES
                        (?, ?, ?, ?)
                    """,
                    (
                        order_of_slaughter,
                        scan_code,
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
        # Matched animals
        # ====================================================

        if parsed.path == "/matches":
            date_str = qs.get("date", [local_time.local_today().isoformat()])[0]

            conn = sqlite3.connect(DB_PATH)

            try:
                result = matching.match_day(conn, date_str)

            except ValueError:
                self._send(
                    400,
                    json.dumps({"error": "date must be YYYY-MM-DD"}),
                    "application/json",
                )

                return

            finally:
                conn.close()

            self._send(
                200,
                json.dumps(
                    result,
                    indent=2,
                ),
                "application/json",
            )

            return

        # ====================================================
        # JSON API
        # ====================================================

        if parsed.path == "/get":
            if date_str := qs.get("date", [None])[0]:
                try:
                    rows = fetch_recent(date=date_str)

                except ValueError:
                    self._send(
                        400,
                        json.dumps({"error": "date must be YYYY-MM-DD"}),
                        "application/json",
                    )

                    return
            elif qs.get("today"):
                rows = fetch_recent(date=local_time.local_today())
            else:
                seconds = qs.get(
                    "seconds",
                    [str(DEFAULT_SECONDS)],
                )[0]

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

                rows = fetch_recent(seconds=seconds_int)

            payload = [
                {
                    "id": r["id"],
                    "order_of_slaughter": r["order_of_slaughter"],
                    "scan_code": r["scan_code"],
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
    def render_html(rows, page="test"):

        live_rows = [r for r in rows if r["station"] == "live"]
        hang_rows = [r for r in rows if r["station"] == "hang"]

        def parse_db_date(date_str):
            try:
                # SQLite datetime('now') returns YYYY-MM-DD HH:MM:SS
                # But handle possible 'T' separator just in case.
                clean_str = date_str.replace("T", " ")
                return datetime.datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None

        live_rows_list = []
        last_live_time = None
        for r in live_rows:
            current_time = parse_db_date(r["recorded_at"])
            if last_live_time and current_time:
                # In descending order, last_live_time is newer than current_time.
                if (last_live_time - current_time).total_seconds() > 180:
                    live_rows_list.append("<tr class='time-gap'><td colspan='4'></td></tr>")

            row_class = "even-row" if r["order_of_slaughter"] % 2 == 0 else ""
            live_rows_list.append(
                f'<tr class="{row_class}">'
                f"<td>{r['order_of_slaughter']}</td>"
                f"<td>{int(round(r['weight']))}</td>"
                f"<td>{html.escape(format_indiana_time(current_time))}</td>"
                "<td></td>"
                "</tr>"
            )
            if current_time:
                last_live_time = current_time
        live_rows_html = "".join(live_rows_list)

        hang_rows_list = []
        last_hang_time = None
        for r in hang_rows:
            current_time = parse_db_date(r["recorded_at"])
            if last_hang_time and current_time:
                if (last_hang_time - current_time).total_seconds() > 180:
                    hang_rows_list.append("<tr class='time-gap'><td colspan='3'></td></tr>")

            row_class = (
                "row-pair-colored" if math.ceil(r["order_of_slaughter"] / 2) % 2 == 0 else ""
            )
            hang_rows_list.append(
                f'<tr class="{row_class}">'
                f"<td>{r['order_of_slaughter']}</td>"
                f"<td>{int(round(r['weight']))}</td>"
                f"<td>{html.escape(format_indiana_time(current_time))}</td>"
                "</tr>"
            )
            if current_time:
                last_hang_time = current_time
        hang_rows_html = "".join(hang_rows_list)

        report_controls_html = ""
        if page == "report":
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
            '<script src="https://cdn.jsdelivr.net/npm/xlsx/dist/xlsx.full.min.js"></script>'
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
            ".scan-flag td {"
            "background-color: #fff3cd;"
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
            "<th>Scans</th>"
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
            "        timeZone: 'America/Indiana/Indianapolis',"
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
            "function scanStatus(animalId, seq, count) {"
            "    const parts = [];"
            "    if (count > 1) parts.push(`entry ${seq.entry} of ${count}`);"
            "    if (!seq.in_use) parts.push('not used');"
            "    if (seq.measurement_id === null) parts.push('no weight');"
            "    parts.push(seq.missing.length ? `missing ${seq.missing.join(', ')}` : 'complete');"
            "    return `${animalId}: ${parts.join(', ')}`;"
            "}"
            "function liveScanRows(data, matches) {"
            "    const statuses = {};"
            "    const unweighed = [];"
            "    matches.animals.forEach(animal => {"
            "        const sequences = animal.live.sequences;"
            "        sequences.forEach(seq => {"
            "            const scans = {"
            "                text: scanStatus(animal.animal_id, seq, sequences.length),"
            "                flagged: !seq.in_use || seq.missing.length > 0,"
            "            };"
            "            if (seq.measurement_id === null) {"
            "                unweighed.push({order_of_slaughter: '—', weight: '—',"
            "                    recorded_at: seq.recorded_at, scans: scans});"
            "            } else {"
            "                statuses[seq.measurement_id] = scans;"
            "            }"
            "        });"
            "    });"
            "    return data"
            "        .filter(r => r.station === 'live')"
            "        .map(r => ({...r, scans: statuses[r.id] || null}))"
            "        .concat(unweighed)"
            "        .sort((a, b) => b.recorded_at.localeCompare(a.recorded_at));"
            "}"
            "function updateTables(url, matchesUrl) {"
            "    return Promise.all(["
            "        fetch(url).then(response => response.json()),"
            "        fetch(matchesUrl)"
            "            .then(response => response.json())"
            "            .catch(() => ({animals: []})),"
            "    ])"
            "        .then(([data, matches]) => {"
            "            const liveTableBody = document.getElementById('live-table-body');"
            "            const hangTableBody = document.getElementById('hang-table-body');"
            "            liveTableBody.innerHTML = '';"
            "            hangTableBody.innerHTML = '';"
            "            const liveRows = liveScanRows(data, matches);"
            "            const hangRows = data.filter(r => r.station === 'hang');"
            "            let lastLiveTime = null;"
            "            liveRows.forEach(r => {"
            "                const currentTime = new Date(r.recorded_at + 'Z');"
            "                if (lastLiveTime && (lastLiveTime - currentTime) > 180000) {"
            "                    const gapRow = document.createElement('tr');"
            "                    gapRow.className = 'time-gap';"
            "                    gapRow.innerHTML = `<td colspan='4'></td>`;"
            "                    liveTableBody.appendChild(gapRow);"
            "                }"
            "                const row = document.createElement('tr');"
            "                if (r.scans && r.scans.flagged) {"
            "                    row.className = 'scan-flag';"
            "                } else if (r.order_of_slaughter % 2 === 0) {"
            "                    row.className = 'even-row';"
            "                }"
            "                const scans = r.scans ? escapeHtml(r.scans.text) : '';"
            "                row.innerHTML = `<td>${r.order_of_slaughter}</td><td>${r.weight}</td>`"
            "                    + `<td>${formatIndianaTime(r.recorded_at)}</td><td>${scans}</td>`;"
            "                liveTableBody.appendChild(row);"
            "                lastLiveTime = currentTime;"
            "            });"
            "            let lastHangTime = null;"
            "            hangRows.forEach(r => {"
            "                const currentTime = new Date(r.recorded_at + 'Z');"
            "                if (lastHangTime && (lastHangTime - currentTime) > 180000) {"
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
            "            return {data: data, liveRows: liveRows};"
            "        })"
            "        .catch(error => console.error('Error fetching measurements:', error));"
            "}"
            "const page = '" + page + "';"
            "if (page === 'report') {"
            "    const dateInput = document.getElementById('report-date');"
            "    const exportBtn = document.getElementById('export-btn');"
            "    dateInput.value = new Intl.DateTimeFormat('en-CA', {"
            "        timeZone: 'America/Indiana/Indianapolis'"
            "    }).format(new Date());"
            "    dateInput.addEventListener('change', () => {"
            "        const date = dateInput.value;"
            "        updateTables(`/get?date=${date}`, `/matches?date=${date}`);"
            "    });"
            "    exportBtn.addEventListener('click', () => {"
            "        const date = dateInput.value;"
            "        updateTables(`/get?date=${date}`, `/matches?date=${date}`).then(result => {"
            "            if (!result) return;"
            "            const liveRows = result.liveRows;"
            "            const hangRows = result.data.filter(r => r.station === 'hang');"
            "            const liveSheet = [['Order', 'Weight', 'Recorded At', 'Scans']];"
            "            liveRows.forEach(r => liveSheet.push(["
            "                r.order_of_slaughter, r.weight, formatIndianaTime(r.recorded_at),"
            "                r.scans ? r.scans.text : '',"
            "            ]));"
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
            "    updateTables(`/get?date=${dateInput.value}`, `/matches?date=${dateInput.value}`);"
            "} else {"
            "    const initialUrl = page === 'display' ? '/get?today=true' : '/get';"
            "    updateTables(initialUrl, '/matches');"
            "    setInterval(() => updateTables(initialUrl, '/matches'), 2000);"
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
        f"shout: serving on :{PORT} (db={DB_PATH})",
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
