#!/usr/bin/python3
import logging
import os
import select
import sqlite3

import serial
from evdev import InputDevice, ecodes

SCANNER_DEVICE = os.environ.get("SCANNER_DEVICE", "/dev/input/event14")
SCALE_DEVICE = os.environ.get("SCALE_DEVICE", "/dev/ttyS0")
DB_PATH = os.environ.get("DB_PATH", "/var/lib/slaughter/measurements.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("slaughter")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
db = sqlite3.connect(DB_PATH)
db.execute(
    """
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        animal_id TEXT NOT NULL,
        weight REAL NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)
db.commit()

# -------------------------
# Connect to devices
# -------------------------

scanner = InputDevice(SCANNER_DEVICE)

scale = serial.Serial(
    port=SCALE_DEVICE,
    baudrate=9600,
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0
)

# Prevent scanner keystrokes from also being typed
# into the terminal / other applications.
scanner.grab()

log.info("Scanner: %s", scanner.name)
log.info("Scale:   %s", SCALE_DEVICE)
log.info("READY - Scan an animal.")

# -------------------------
# Barcode character mapping
# -------------------------

key_map = {
    "KEY_0": "0",
    "KEY_1": "1",
    "KEY_2": "2",
    "KEY_3": "3",
    "KEY_4": "4",
    "KEY_5": "5",
    "KEY_6": "6",
    "KEY_7": "7",
    "KEY_8": "8",
    "KEY_9": "9",

    "KEY_A": "A",
    "KEY_B": "B",
    "KEY_C": "C",
    "KEY_D": "D",
    "KEY_E": "E",
    "KEY_F": "F",
    "KEY_G": "G",
    "KEY_H": "H",
    "KEY_I": "I",
    "KEY_J": "J",
    "KEY_K": "K",
    "KEY_L": "L",
    "KEY_M": "M",
    "KEY_N": "N",
    "KEY_O": "O",
    "KEY_P": "P",
    "KEY_Q": "Q",
    "KEY_R": "R",
    "KEY_S": "S",
    "KEY_T": "T",
    "KEY_U": "U",
    "KEY_V": "V",
    "KEY_W": "W",
    "KEY_X": "X",
    "KEY_Y": "Y",
    "KEY_Z": "Z",

    "KEY_MINUS": "-",
}

barcode_buffer = ""
current_animal_id = None


# -------------------------
# Main loop
# -------------------------

try:

    while True:

        readable, _, _ = select.select(
            [scanner.fd, scale.fileno()],
            [],
            [],
            1
        )

        # -------------------------
        # Scanner input
        # -------------------------

        if scanner.fd in readable:

            for event in scanner.read():

                if event.type != ecodes.EV_KEY:
                    continue

                if event.value != 1:
                    continue

                key = ecodes.KEY[event.code]

                if key == "KEY_ENTER":

                    if barcode_buffer:

                        current_animal_id = barcode_buffer

                        log.info("SCANNED: %s", current_animal_id)
                        log.info("Waiting for weight...")

                        barcode_buffer = ""

                    continue

                character = key_map.get(key)

                if character:
                    barcode_buffer += character

        # -------------------------
        # Scale input
        # -------------------------

        if scale.fileno() in readable:

            raw = scale.readline()

            if not raw:
                continue

            try:
                weight_text = raw.decode("ascii").strip()
                weight = float(weight_text)

            except (UnicodeDecodeError, ValueError):
                log.warning("Invalid scale data: %r", raw)
                continue

            if current_animal_id is None:

                log.warning(
                    "Weight %g received without an animal ID.",
                    weight,
                )

                continue

            # -------------------------
            # Successful capture!
            # -------------------------

            db.execute(
                "INSERT INTO measurements (animal_id, weight) VALUES (?, ?)",
                (current_animal_id, weight),
            )
            db.commit()

            log.info("CAPTURED: %s,%g", current_animal_id, weight)

            # Require another scan before another
            # weight can be accepted.
            current_animal_id = None

            log.info("READY - Scan next animal.")


except KeyboardInterrupt:

    log.info("Stopping...")


finally:

    scanner.ungrab()
    scale.close()
    db.close()
