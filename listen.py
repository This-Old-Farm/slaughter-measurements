#!/usr/bin/python3

import logging
import os
import select
import sqlite3
import signal

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

# CHANGE THIS if the IQ355+ uses a different serial port.
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

db.execute("""
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_of_slaughter INTEGER NOT NULL,
        station TEXT NOT NULL,
        weight REAL NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)

db.commit()


def get_next_order_of_slaughter(station_name):
    """
    Get the next order number for a given station for the current day.
    """
    cursor = db.cursor()
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
        scale_timeout=0,
    ):

        self.name = name
        self.scanner_device = scanner_device
        self.scale_device = scale_device

        # Each station gets its own independent state.
        self.barcode_buffer = ""
        self.go_scanned = False

        # ----------------------------------------------------
        # Scanner
        # ----------------------------------------------------

        self.scanner = InputDevice(self.scanner_device)

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
            timeout=scale_timeout,
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
            "%s: READY - Scan 'GO' to capture weight.",
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

                if self.barcode_buffer == "GO":

                    log.info(
                        "%s: SCANNED: %s",
                        self.name,
                        self.barcode_buffer,
                    )

                    log.info(
                        "%s: Waiting for weight...",
                        self.name,
                    )
                    self.go_scanned = True

                self.barcode_buffer = ""

                continue

            # ------------------------------------------------
            # Barcode character
            # ------------------------------------------------

            character = KEY_MAP.get(key)

            if character:
                self.barcode_buffer += character

    # ========================================================
    # Save measurement
    # ========================================================

    def save_measurement(self, weight):

        if not self.go_scanned:
            return

        # Round to the nearest integer
        weight = round(weight)

        order_of_slaughter = get_next_order_of_slaughter(self.name)

        db.execute(
            """
            INSERT INTO measurements
                (order_of_slaughter, station, weight)
            VALUES
                (?, ?, ?)
            """,
            (
                order_of_slaughter,
                self.name.lower(),
                weight,
            ),
        )

        db.commit()

        log.info(
            "%s: CAPTURED: Order %d, %d",
            self.name,
            order_of_slaughter,
            weight,
        )

        # Require another barcode before another
        # weight can be accepted.
        self.go_scanned = False

        log.info(
            "%s: READY - Scan 'GO' to capture weight.",
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
# LIVE station - MSI-8000HD
# ============================================================


class LiveStation(Station):

    def handle_scale(self):

        raw = self.scale.readline()

        if not raw:
            return

        try:

            weight_text = raw.decode("ascii").strip()

            weight = float(weight_text)

        except (UnicodeDecodeError, ValueError):

            log.warning(
                "%s: Invalid scale data: %r",
                self.name,
                raw,
            )

            return

        # Keep the original LIVE behavior.
        self.save_measurement(weight)


# ============================================================
# HANG station - IQ355+
# ============================================================


class HangStation(Station):

    def handle_scale(self):

        raw = self.scale.readline()

        if not raw:
            return

        weight = self.parse_iq355(raw)

        if weight is None:
            return

        # The IQ355+ continuously streams.
        #
        # Valid weights are ignored until a barcode has
        # been scanned. After a successful capture,
        # save_measurement() clears the animal ID, so the
        # continuing stream cannot create duplicate records.
        if not self.go_scanned:
            return

        self.save_measurement(weight)

    # ========================================================
    # IQ355+ continuous-stream parser
    # ========================================================

    def parse_iq355(self, raw):
        """
        IQ355+ continuous output format:

        <STX><POL><WWWWWWW><UNIT><G/N><S><TERM>

        STX:
            ASCII 02

        POL:
            space = positive
            -     = negative
            ^     = overload
            ]     = underrange

        WWWWWWW:
            7-character weight field

        UNIT:
            L = pounds
            K = kilograms
            T = tons
            G = grams
            O = ounces

        G/N:
            G = gross
            N = net

        STATUS:
            space = valid
            I     = invalid
            M     = motion
            O     = over/under range

        For the HANG station, only a VALID, GROSS,
        POUNDS reading is accepted.
        """

        # ----------------------------------------------------
        # Decode ASCII
        # ----------------------------------------------------

        try:

            message = raw.decode("ascii")

        except UnicodeDecodeError:

            log.warning(
                "%s: Non-ASCII IQ355+ data: %r",
                self.name,
                raw,
            )

            return None

        # ----------------------------------------------------
        # Remove ONLY CR/LF.
        #
        # Do NOT use .strip().
        #
        # A trailing space in the IQ355+ data is meaningful:
        # it means the reading status is VALID.
        # ----------------------------------------------------

        message = message.rstrip("\r\n")

        # ----------------------------------------------------
        # Verify STX
        # ----------------------------------------------------

        if not message:
            return None

        if ord(message[0]) != 0x02:

            log.debug(
                "%s: IQ355+ packet missing STX: %r",
                self.name,
                raw,
            )

            return None

        # ----------------------------------------------------
        # Remove STX
        # ----------------------------------------------------

        data = message[1:]

        # ----------------------------------------------------
        # Expected fixed-width payload:
        #
        # POL       1
        # WEIGHT    7
        # UNIT      1
        # G/N       1
        # STATUS    1
        #
        # Total = 11 characters
        # ----------------------------------------------------

        if len(data) != 11:

            log.debug(
                "%s: IQ355+ unexpected packet " "length %d: %r",
                self.name,
                len(data),
                raw,
            )

            return None

        # ----------------------------------------------------
        # Split fields
        # ----------------------------------------------------

        polarity = data[0]

        weight_text = data[1:8]

        unit = data[8]

        gross_net = data[9]

        status = data[10]

        # ----------------------------------------------------
        # Polarity
        # ----------------------------------------------------

        if polarity == " ":

            negative = False

        elif polarity == "-":

            negative = True

        elif polarity == "^":

            log.warning(
                "%s: IQ355+ reports overload.",
                self.name,
            )

            return None

        elif polarity == "]":

            log.warning(
                "%s: IQ355+ reports underrange.",
                self.name,
            )

            return None

        else:

            log.warning(
                "%s: IQ355+ unknown polarity: %r",
                self.name,
                polarity,
            )

            return None

        # ----------------------------------------------------
        # Status
        # ----------------------------------------------------

        if status == "M":

            # Motion is normal while the carcass is moving.
            # Ignore it and continue waiting for a valid
            # stable reading.
            return None

        if status == "I":

            # Invalid reading.
            return None

        if status == "O":

            log.warning(
                "%s: IQ355+ reports " "over/under range.",
                self.name,
            )

            return None

        if status != " ":

            log.warning(
                "%s: IQ355+ unknown status: %r",
                self.name,
                status,
            )

            return None

        # ----------------------------------------------------
        # Units
        # ----------------------------------------------------

        if unit != "L":

            log.warning(
                "%s: IQ355+ ignored reading " "because unit is %r, not pounds.",
                self.name,
                unit,
            )

            return None

        # ----------------------------------------------------
        # Gross / Net
        # ----------------------------------------------------

        if gross_net != "G":

            log.warning(
                "%s: IQ355+ ignored reading " "because mode is %r, not gross.",
                self.name,
                gross_net,
            )

            return None

        # ----------------------------------------------------
        # Weight
        # ----------------------------------------------------

        try:

            weight = float(weight_text.strip())

        except ValueError:

            log.warning(
                "%s: IQ355+ invalid weight " "field: %r",
                self.name,
                weight_text,
            )

            return None

        if negative:
            weight = -weight

        # ----------------------------------------------------
        # Valid hanging weight
        # ----------------------------------------------------

        return weight


# ============================================================
# Signal handling
# ============================================================


def handle_shutdown_signal(signum, frame):
    """
    Convert SIGTERM/SIGINT into a normal Python interruption
    so execution reaches the existing finally cleanup block.
    """
    signal_name = signal.Signals(signum).name

    log.info(
        "Received %s. Shutting down...",
        signal_name,
    )

    raise KeyboardInterrupt


signal.signal(
    signal.SIGTERM,
    handle_shutdown_signal,
)

signal.signal(
    signal.SIGINT,
    handle_shutdown_signal,
)

# ============================================================
# Create stations
# ============================================================

live = None
hang = None

try:

    log.info("Starting slaughter measurement capture...")

    # --------------------------------------------------------
    # LIVE station
    # --------------------------------------------------------

    live = LiveStation(
        name="LIVE",
        scanner_device=LIVE_SCANNER_DEVICE,
        scale_device=LIVE_SCALE_DEVICE,
        scale_timeout=0,
    )

    # --------------------------------------------------------
    # HANG station
    # --------------------------------------------------------

    hang = HangStation(
        name="HANG",
        scanner_device=HANG_SCANNER_DEVICE,
        scale_device=HANG_SCALE_DEVICE,
        scale_timeout=1,
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

            inputs.append(station.scanner.fd)

            inputs.append(station.scale.fileno())

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

    log.info("Stopping slaughter measurement capture...")


finally:

    if live is not None:
        live.close()

    if hang is not None:
        hang.close()

    db.close()

    log.info("Slaughter measurement capture stopped.")
