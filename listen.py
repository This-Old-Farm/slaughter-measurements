#!/usr/bin/python3

import logging
import os
import select
import sqlite3

import serial
from evdev import InputDevice, ecodes


# ============================================================
# Configuration
# ============================================================

LIVE_SCANNER_DEVICE = os.environ.get(
    "LIVE_SCANNER_DEVICE",
    "/dev/input/by-path/pci-0000:00:14.0-usb-0:1:1.0-event-kbd",
)

LIVE_SCALE_DEVICE = os.environ.get(
    "LIVE_SCALE_DEVICE",
    "/dev/ttyS0",
)

HANG_SCANNER_DEVICE = os.environ.get(
    "HANG_SCANNER_DEVICE",
    "/dev/input/by-path/pci-0000:00:14.0-usb-0:2:1.0-event-kbd",
)

# CHANGE THIS if the hanging scale uses a different serial port.
HANG_SCALE_DEVICE = os.environ.get(
    "HANG_SCALE_DEVICE",
    "/dev/ttyUSB0",
)

DB_PATH = os.environ.get(
    "DB_PATH",
    "/var/lib/slaughter/measurements.db",
)


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

log = logging.getLogger("slaughter")


# ============================================================
# Database
# ============================================================

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
db = sqlite3.connect(DB_PATH)
db.execute(
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

db.commit()


# ============================================================
# Barcode character mapping
# ============================================================

KEY_MAP = {
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


# ============================================================
# Station
# ============================================================

class Station:

    def __init__(
        self,
        name,
        scanner_device,
        scale_device,
    ):

        self.name = name
        self.scanner_device = scanner_device
        self.scale_device = scale_device

        # Each station gets its own independent state.
        self.barcode_buffer = ""
        self.animal_id = None

        # ----------------------------------------------------
        # Scanner
        # ----------------------------------------------------

        self.scanner = InputDevice(
            self.scanner_device
        )

        # Prevent scanner keystrokes from being typed
        # into the terminal or other applications.
        self.scanner.grab()

        # ----------------------------------------------------
        # Scale
        # ----------------------------------------------------

        self.scale = serial.Serial(
            port=self.scale_device,
            baudrate=9600,
            bytesize=8,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,
        )

        log.info(
            "%s: Scanner: %s",
            self.name,
            self.scanner.name,
        )

        log.info(
            "%s: Scanner device: %s",
            self.name,
            self.scanner_device,
        )

        log.info(
            "%s: Scale device: %s",
            self.name,
            self.scale_device,
        )

        log.info(
            "%s: READY - Scan an animal.",
            self.name,
        )


    # ========================================================
    # Scanner handling
    # ========================================================

    def handle_scanner(self):

        for event in self.scanner.read():

            if event.type != ecodes.EV_KEY:
                continue

            # Only process key-down events.
            if event.value != 1:
                continue

            key = ecodes.KEY[event.code]

            # ------------------------------------------------
            # Barcode complete
            # ------------------------------------------------

            if key == "KEY_ENTER":

                if self.barcode_buffer:

                    self.animal_id = (
                        self.barcode_buffer
                    )

                    log.info(
                        "%s: SCANNED: %s",
                        self.name,
                        self.animal_id,
                    )

                    log.info(
                        "%s: Waiting for weight...",
                        self.name,
                    )

                    self.barcode_buffer = ""

                continue

            # ------------------------------------------------
            # Barcode character
            # ------------------------------------------------

            character = KEY_MAP.get(key)

            if character:
                self.barcode_buffer += character


    # ========================================================
    # Scale handling
    # ========================================================

    def handle_scale(self):

        raw = self.scale.readline()

        if not raw:
            return

        try:

            weight_text = (
                raw.decode("ascii").strip()
            )

            weight = float(weight_text)

        except (UnicodeDecodeError, ValueError):

            log.warning(
                "%s: Invalid scale data: %r",
                self.name,
                raw,
            )

            return

        # ----------------------------------------------------
        # Weight received without an ID
        # ----------------------------------------------------

        if self.animal_id is None:

            log.warning(
                "%s: Weight %g received "
                "without an animal ID.",
                self.name,
                weight,
            )

            return

        # ----------------------------------------------------
        # Save measurement
        # ----------------------------------------------------

        db.execute(
            """
            INSERT INTO measurements
                (animal_id, station, weight)
            VALUES
                (?, ?, ?)
            """,
            (
                self.animal_id,
                self.name.lower(),
                weight,
            ),
        )

        db.commit()

        # ----------------------------------------------------
        # Successful capture
        # ----------------------------------------------------

        log.info(
            "%s: CAPTURED: %s,%g",
            self.name,
            self.animal_id,
            weight,
        )

        # Require another barcode before another
        # weight can be accepted.
        self.animal_id = None

        log.info(
            "%s: READY - Scan next animal.",
            self.name,
        )


    # ========================================================
    # Cleanup
    # ========================================================

    def close(self):

        try:
            self.scanner.ungrab()
        except Exception:
            pass

        try:
            self.scale.close()
        except Exception:
            pass


# ============================================================
# Create stations
# ============================================================

live = None
hang = None

try:

    log.info(
        "Starting slaughter measurement capture..."
    )

    # --------------------------------------------------------
    # LIVE station
    # --------------------------------------------------------

    live = Station(
        name="LIVE",
        scanner_device=LIVE_SCANNER_DEVICE,
        scale_device=LIVE_SCALE_DEVICE,
    )

    # --------------------------------------------------------
    # HANG station
    # --------------------------------------------------------

    hang = Station(
        name="HANG",
        scanner_device=HANG_SCANNER_DEVICE,
        scale_device=HANG_SCALE_DEVICE,
    )

    stations = [
        live,
        hang,
    ]

    # ========================================================
    # Main loop
    # ========================================================

    while True:

        # Build a list containing all scanner and scale
        # file descriptors.
        inputs = []

        for station in stations:

            inputs.append(
                station.scanner.fd
            )

            inputs.append(
                station.scale.fileno()
            )

        readable, _, _ = select.select(
            inputs,
            [],
            [],
            1,
        )

        # ----------------------------------------------------
        # Determine which station/device has data
        # ----------------------------------------------------

        for station in stations:

            if station.scanner.fd in readable:

                station.handle_scanner()

            if station.scale.fileno() in readable:

                station.handle_scale()


# ============================================================
# Shutdown
# ============================================================

except KeyboardInterrupt:

    log.info(
        "Stopping slaughter measurement capture..."
    )


finally:

    if live is not None:
        live.close()

    if hang is not None:
        hang.close()

    db.close()

    log.info(
        "Slaughter measurement capture stopped."
    )