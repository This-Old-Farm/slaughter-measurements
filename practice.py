#!/usr/bin/python3
"""
Practice the scan workflow with the real scanners and fake scales.

Run on the slaughter collection PC, as root so it can stop the listen
service and use the scanners:

    sudo python3 practice.py [--fresh] [--port 8080]

It stops the listen service, then runs listen.py and shout.py against
a separate practice database and plays both scales:

- live: a plausible weight for the species about 2 seconds after each
  animal ID scan
- hang: a steady half-carcass reading sized to the next live animal,
  so yields stay in matching's bands. It is captured as soon as a
  hang tag is scanned, as if the carcass were already on the scale.

Type a weight and Enter to override the next one, e.g. to practice a
bad reading and a rescan:

    0          next live weight is 0
    l 1250     next live weight is 1250
    h -2       next hang weight is -2

A live override is sent right away if an animal is waiting for its
weight. The practice panel on /live does the same, and can turn the
automatic weights off so every weight is sent by hand.

Ctrl-C ends practice and restarts the listen service. The real
database and the shout service on port 80 are never touched.
"""

import argparse
import json
import os
import pty
import random
import select
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time

import local_time
import matching

HERE = os.path.dirname(os.path.abspath(__file__))

LISTEN_SERVICE = "listen-slaughter-measurements"

# Plausible live weights (lb) by species.
LIVE_WEIGHTS = {
    "BEEF": (900, 1500),
    "PORK": (230, 320),
    "LAMB": (90, 140),
}

# Seconds between an animal ID scan and its fake live weight.
LIVE_DELAY = 2.0

# The IQ355+ streams readings continuously; this is how often.
HANG_INTERVAL = 0.2


def log(message):
    print(f"[practice] {message}", flush=True)


# ============================================================
# Fake scales
# ============================================================


class Scales:
    def __init__(self, db_path, live_fd, hang_fd):
        self.db_path = db_path
        self.live_fd = live_fd
        self.hang_fd = hang_fd
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.live_override = None
        self.hang_override = None

        # Off means weights are only sent by hand.
        self.auto = True

    def override(self, station, weight):
        with self.lock:
            if station == "live":
                self.live_override = weight
            else:
                self.hang_override = weight

        log(f"next {station} weight: {weight:g}")

    def write(self, fd, text):
        # Never block: if listen stops reading, drop readings like a
        # scale with nobody listening would.
        try:
            os.write(fd, text.encode("ascii"))
        except BlockingIOError:
            pass

    # --------------------------------------------------------
    # Live: MSI-8000HD, one line per weight
    # --------------------------------------------------------

    def run_live(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        waiting_since = {}
        weighed = set()

        while not self.stopping.wait(0.25):
            # The newest sequence is the one listen is waiting on.
            row = conn.execute(
                "SELECT id, species, measurement_id FROM animal_details ORDER BY id DESC LIMIT 1"
            ).fetchone()

            if row is None or row[2] is not None or row[0] in weighed:
                continue

            sequence_id, species = row[0], row[1]
            since = waiting_since.setdefault(sequence_id, time.monotonic())

            with self.lock:
                weight = self.live_override
                self.live_override = None

            if weight is None:
                if not self.auto or time.monotonic() - since < LIVE_DELAY:
                    continue

                weight = random.randint(*LIVE_WEIGHTS.get(species, (500, 1000)))

            self.write(self.live_fd, f"{weight:g}\r\n")
            weighed.add(sequence_id)
            log(f"live scale sent {weight:g} lb")

    # --------------------------------------------------------
    # Hang: IQ355+, a continuous stream of fixed-width frames
    # --------------------------------------------------------

    def next_hang_carcass(self, conn):
        """
        The half the next new hang tag pairs with, as (key, weight):
        the key names the animal and half, and the weight is sized to
        its live weight. Before any animal is weighed, (None, a guess).
        """
        animals = matching.match_day(conn, local_time.local_today().isoformat())["animals"]

        for animal in animals:
            species = animal["species"]
            live_weight = animal["live"]["weight"]

            if animal["complete"] or live_weight is None or species not in matching.YIELD_BANDS:
                continue

            low, high = matching.YIELD_BANDS[species]
            share = (low + high) / 2 / 100 / matching.HANG_TAGS_PER_SPECIES[species]
            key = (animal["order"], len(animal["hang"]))

            return key, round(live_weight * share * random.uniform(0.97, 1.03))

        return None, random.randint(300, 450)

    def iq355_frame(self, weight, status=" "):
        """A reading; status " " is valid and "M" is motion, which listen ignores."""
        polarity = "-" if weight < 0 else " "
        return f"\x02{polarity}{abs(weight):>7g}LG{status}\r\n"

    def run_hang(self):
        conn = sqlite3.connect(self.db_path, timeout=5)

        def last_hang_id():
            query = "SELECT MAX(id) FROM measurements WHERE station = 'hang'"
            return conn.execute(query).fetchone()[0]

        last_id = last_hang_id()
        key, weight = self.next_hang_carcass(conn)
        checked = time.monotonic()

        while not self.stopping.wait(HANG_INTERVAL):
            with self.lock:
                override = self.hang_override

            streaming = override if override is not None else weight

            # With automatic weights off, the scale never settles until
            # a weight is sent by hand.
            status = " " if override is not None or self.auto else "M"

            self.write(self.hang_fd, self.iq355_frame(streaming, status))

            if time.monotonic() - checked < 1:
                continue

            checked = time.monotonic()
            captured = last_hang_id()

            # A tag was weighed: move on to the next carcass.
            if captured != last_id:
                last_id = captured
                log(f"hang scale captured {streaming:g} lb")

                with self.lock:
                    self.hang_override = None

                key, weight = self.next_hang_carcass(conn)
                continue

            # A live animal was weighed since this carcass was sized,
            # so the next tag now pairs with a different half.
            next_key, next_weight = self.next_hang_carcass(conn)

            if next_key != key:
                key, weight = next_key, next_weight

    # --------------------------------------------------------
    # Practice panel on /live, through shout's /practice
    # --------------------------------------------------------

    def run_control(self, path):
        applied = 0

        while not self.stopping.wait(0.25):
            try:
                with open(path, encoding="utf-8") as control:
                    state = json.load(control)
            except (OSError, ValueError):
                continue

            if state["auto"] != self.auto:
                self.auto = state["auto"]
                log(f"automatic weights {'on' if self.auto else 'off'}")

            if state["seq"] != applied:
                applied = state["seq"]

                if state["override"]:
                    self.override(state["override"]["station"], state["override"]["weight"])


def write_control(path, state):
    temporary = path + ".tmp"

    with open(temporary, "w", encoding="utf-8") as control:
        json.dump(state, control)

    os.replace(temporary, path)


# ============================================================
# The listen service
# ============================================================


def service_active():
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", LISTEN_SERVICE]).returncode == 0
    except FileNotFoundError:
        return False


def systemctl(action):
    log(f"{action} {LISTEN_SERVICE}...")
    subprocess.run(["systemctl", action, LISTEN_SERVICE], check=True)


def open_scale():
    """A pty pair: the fake scale writes to the master, listen opens the slave."""
    master, slave = pty.openpty()
    os.set_blocking(master, False)
    return master, slave, os.ttyname(slave)


def parse_override(line):
    """'0', 'l 1250', or 'h -2' as (station, weight), or None."""
    parts = line.split()

    if len(parts) == 1:
        parts = ["l", parts[0]]

    if len(parts) != 2 or parts[0].lower() not in ("l", "live", "h", "hang"):
        return None

    try:
        weight = float(parts[1])
    except ValueError:
        return None

    return ("live" if parts[0].lower().startswith("l") else "hang"), weight


def handle_sigterm(signum, frame):
    # Unwind like Ctrl-C so the listen service is always restarted.
    raise KeyboardInterrupt


def stop(process):
    if process is None or process.poll() is not None:
        return

    process.send_signal(signal.SIGTERM)

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default="/var/lib/slaughter/practice.db")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="start from an empty practice database",
    )
    args = parser.parse_args()

    if args.fresh:
        for suffix in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(args.db + suffix):
                os.remove(args.db + suffix)

    signal.signal(signal.SIGTERM, handle_sigterm)

    restart_service = service_active()
    listener = shout = None
    scales = None

    try:
        if restart_service:
            systemctl("stop")

        live_master, _live_slave, live_path = open_scale()
        hang_master, _hang_slave, hang_path = open_scale()

        control_path = args.db + ".control.json"
        write_control(control_path, {"auto": True, "seq": 0, "override": None})

        env = dict(
            os.environ,
            DB_PATH=args.db,
            LIVE_SCALE_DEVICE=live_path,
            HANG_SCALE_DEVICE=hang_path,
        )

        listener = subprocess.Popen([sys.executable, os.path.join(HERE, "listen.py")], env=env)

        shout = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "shout.py")],
            env=dict(env, PORT=str(args.port), PRACTICE_CONTROL=control_path),
            stdout=subprocess.DEVNULL,
        )

        # Let listen create the practice database before the scales read it.
        time.sleep(2)

        if listener.poll() is not None:
            log("listen.py stopped - see its error above. Is this running as root?")
            return 1

        scales = Scales(args.db, live_master, hang_master)

        for run in (scales.run_live, scales.run_hang):
            threading.Thread(target=run, daemon=True).start()

        threading.Thread(target=scales.run_control, args=(control_path,), daemon=True).start()

        host = socket.gethostname()
        log(f"practice database: {args.db}")
        log(f"pages: http://{host}:{args.port}/live  /display  /matches")
        log("the practice panel on /live can also send weights and turn automatic ones off.")
        log("scan away. Type a weight to override the next one ('0', 'l 1250', 'h -2').")
        log("Ctrl-C to stop.")

        # Stops watching for typed weights if stdin closes, but keeps
        # running until Ctrl-C.
        inputs = [sys.stdin]

        while True:
            if listener.poll() is not None:
                log("listen.py stopped unexpectedly - see its error above.")
                return 1

            readable, _, _ = select.select(inputs, [], [], 1)

            if not readable:
                continue

            line = sys.stdin.readline()

            if not line:
                inputs = []
                continue

            if not line.strip():
                continue

            parsed = parse_override(line)

            if parsed is None:
                log("type a weight like '0', 'l 1250', or 'h -2'")
            else:
                scales.override(*parsed)

    except KeyboardInterrupt:
        pass

    finally:
        if scales is not None:
            scales.stopping.set()

        stop(listener)
        stop(shout)

        if os.path.exists(args.db + ".control.json"):
            os.remove(args.db + ".control.json")

        if restart_service:
            systemctl("start")

        log("practice stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
