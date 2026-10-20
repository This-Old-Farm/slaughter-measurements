#!/usr/bin/python3

import logging
import os
import select
import signal
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
        scan_code TEXT NOT NULL DEFAULT '',
        station TEXT NOT NULL,
        weight REAL NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)

# Databases created before scan_code existed need the column added.
# barcode_buffer was its name on main before it was ever deployed.
measurement_columns = {row[1] for row in db.execute("PRAGMA table_info(measurements)")}

if "barcode_buffer" in measurement_columns:
    db.execute("ALTER TABLE measurements RENAME COLUMN barcode_buffer TO scan_code")
elif "scan_code" not in measurement_columns:
    db.execute("ALTER TABLE measurements ADD COLUMN scan_code TEXT NOT NULL DEFAULT ''")

db.execute(
    """
    CREATE TABLE IF NOT EXISTS animal_details (
        measurement_id INTEGER PRIMARY KEY,
        animal_id TEXT NOT NULL,
        species TEXT NOT NULL,
        sex TEXT NOT NULL,
        over_30_months INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (measurement_id)
            REFERENCES measurements(id)
    )
    """
)

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


def get_first_order_of_slaughter(station_name, scan_code):
    """
    Get the order number a scan code was first given at a station
    for the current day, or None if it has not been weighed yet.
    """
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT MIN(order_of_slaughter)
        FROM measurements
        WHERE station = ? AND scan_code = ? AND DATE(recorded_at) = DATE('now', 'localtime')
        """,
        (station_name.lower(), scan_code),
    )
    return cursor.fetchone()[0]


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

SEX_CODES = {
    "BEEF": {
        "D": "Dairy",
        "B": "Bull",
        "H": "Heifer",
        "S": "Steer",
        "C": "Cow",
    },
    "PORK": {
        "B": "Barrow",
        "G": "Gilt",
        "BO": "Boar",
        "S": "Sow",
    },
    "LAMB": {
        "L": "Lamb",
        "G": "Goat",
        "M": "Mutton",
    },
}


# ============================================================
# Station
# ============================================================


class Station:
    SCAN_PROMPT = "Scan an animal."
    NEXT_SCAN_PROMPT = "Scan next animal."

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

        # The code scanned for the weight about to be captured.
        # None means a weight arriving now is not saved.
        self.scan_code = None

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
            "%s: READY - %s",
            self.name,
            self.SCAN_PROMPT,
        )

    def handle_barcode(self, barcode):

        # A second scan before a weight means the first tag was
        # the wrong one; the most recent scan is the one saved.
        if self.scan_code is not None:
            log.warning(
                "%s: %s replaces %s, which was never weighed.",
                self.name,
                barcode,
                self.scan_code,
            )

        self.scan_code = barcode

        log.info(
            "%s: SCANNED: %s",
            self.name,
            self.scan_code,
        )

        log.info(
            "%s: Waiting for weight...",
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
                barcode = self.barcode_buffer
                self.barcode_buffer = ""

                if barcode:
                    self.handle_barcode(barcode)

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

        if self.scan_code is None:
            return

        # Round to the nearest integer
        weight = round(weight)

        # A code that was already weighed today is a reweigh and
        # keeps its original place in the order. Every weight is
        # kept; matching uses the most recent one.
        order_of_slaughter = get_first_order_of_slaughter(self.name, self.scan_code)

        if order_of_slaughter is None:
            order_of_slaughter = get_next_order_of_slaughter(self.name)
        else:
            log.warning(
                "%s: REWEIGH: %s keeps order %d.",
                self.name,
                self.scan_code,
                order_of_slaughter,
            )

        cursor = db.execute(
            """
            INSERT INTO measurements
                (order_of_slaughter, scan_code, station, weight)
            VALUES
                (?, ?, ?, ?)
            """,
            (
                order_of_slaughter,
                self.scan_code,
                self.name,
                weight,
            ),
        )

        measurement_id = cursor.lastrowid

        db.commit()

        log.info(
            "%s: CAPTURED: Order %d, %s, %d",
            self.name,
            order_of_slaughter,
            self.scan_code,
            weight,
        )

        # Require another barcode before another
        # weight can be accepted.
        self.scan_code = None

        log.info(
            "%s: READY - %s",
            self.name,
            self.NEXT_SCAN_PROMPT,
        )

        return measurement_id

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
    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        # Additional data collected only at the LIVE station.
        self.species = None
        self.sex = None
        self.over_30_months = False

    def get_species(self, animal_id):

        if not animal_id:
            return None

        species_code = animal_id[-1].upper()

        species_codes = {
            "B": "BEEF",
            "P": "PORK",
            "L": "LAMB",
        }

        return species_codes.get(species_code)

    def handle_barcode(self, barcode):

        barcode = barcode.upper()

        # --------------------------------------------------------
        # No animal pending: this barcode must be the animal ID.
        # --------------------------------------------------------

        if self.scan_code is None:
            species = self.get_species(barcode)

            if species is None:
                log.warning(
                    "%s: Invalid animal ID %s - expected ID ending in B, P, or L.",
                    self.name,
                    barcode,
                )

                return

            self.scan_code = barcode
            self.species = species
            self.sex = None
            self.over_30_months = False

            log.info(
                "%s: ANIMAL: %s (%s)",
                self.name,
                self.scan_code,
                self.species,
            )

            log.info(
                "%s: Waiting for sex/type scan...",
                self.name,
            )

            return

        # --------------------------------------------------------
        # Optional PLUS30 scan
        # --------------------------------------------------------

        if barcode == "PLUS30":
            if self.species != "BEEF":
                log.warning(
                    "%s: PLUS30 is only valid for BEEF.",
                    self.name,
                )

                return

            self.over_30_months = True

            log.info(
                "%s: +30 recorded for %s.",
                self.name,
                self.scan_code,
            )

            return

        # Once sex/type has been recorded, the station is
        # waiting for weight and should not accept another
        # sex/type barcode for this animal. PLUS30 is still
        # accepted above so it can be scanned in either order.
        if self.sex is not None:
            log.warning(
                "%s: Barcode %s ignored - waiting for weight for %s.",
                self.name,
                barcode,
                self.scan_code,
            )

            return

        # --------------------------------------------------------
        # Sex/type scan
        # --------------------------------------------------------

        valid_codes = SEX_CODES[self.species]

        if barcode not in valid_codes:
            log.warning(
                "%s: Invalid %s sex/type code: %s",
                self.name,
                self.species,
                barcode,
            )

            return

        self.sex = valid_codes[barcode]

        log.info(
            "%s: SEX/TYPE: %s (%s)",
            self.name,
            barcode,
            self.sex,
        )

        log.info(
            "%s: Waiting for weight...",
            self.name,
        )

    def save_measurement(self, weight):

        # LIVE measurements require an animal ID and
        # a completed sex/type scan.
        if self.scan_code is None:
            return

        if self.sex is None:
            return

        # Preserve the metadata because the base save method
        # clears scan_code after successfully saving the weight.
        animal_id = self.scan_code
        species = self.species
        sex = self.sex
        over_30_months = self.over_30_months

        # Save the normal measurement first.
        measurement_id = super().save_measurement(weight)

        if measurement_id is None:
            return

        # Store the additional LIVE animal information separately
        # so the measurements table schema remains unchanged.
        db.execute(
            """
                INSERT INTO animal_details
                        (
                                measurement_id,
                                animal_id,
                                species,
                                sex,
                                over_30_months
                        )
                VALUES
                        (?, ?, ?, ?, ?)
                """,
            (
                measurement_id,
                animal_id,
                species,
                sex,
                int(over_30_months),
            ),
        )

        db.commit()

        log.info(
            "%s: ANIMAL DETAILS: %s,%s,%s,+30=%s",
            self.name,
            animal_id,
            species,
            sex,
            "YES" if over_30_months else "NO",
        )

        # Reset LIVE-only state for the next animal.
        self.species = None
        self.sex = None
        self.over_30_months = False

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

        self.save_measurement(weight)


# ============================================================
# HANG station - IQ355+
# ============================================================


class HangStation(Station):
    SCAN_PROMPT = "Scan a hang tag."
    NEXT_SCAN_PROMPT = "Scan next hang tag."

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
        # save_measurement() clears the scan code, so the
        # continuing stream cannot create duplicate records.
        if self.scan_code is None:
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
                "%s: IQ355+ unexpected packet length %d: %r",
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
                "%s: IQ355+ reports over/under range.",
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
                "%s: IQ355+ ignored reading because unit is %r, not pounds.",
                self.name,
                unit,
            )

            return None

        # ----------------------------------------------------
        # Gross / Net
        # ----------------------------------------------------

        if gross_net != "G":
            log.warning(
                "%s: IQ355+ ignored reading because mode is %r, not gross.",
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
                "%s: IQ355+ invalid weight field: %r",
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


class Shutdown(Exception):
    """Raised to unwind to the cleanup block on SIGTERM/SIGINT."""


def handle_shutdown_signal(signum, frame):
    signal_name = signal.Signals(signum).name

    log.info(
        "Received %s. Shutting down...",
        signal_name,
    )

    raise Shutdown


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
        name="live",
        scanner_device=LIVE_SCANNER_DEVICE,
        scale_device=LIVE_SCALE_DEVICE,
        scale_timeout=0,
    )

    # --------------------------------------------------------
    # HANG station
    # --------------------------------------------------------

    hang = HangStation(
        name="hang",
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

except (KeyboardInterrupt, Shutdown):
    log.info("Stopping slaughter measurement capture...")


finally:
    if live is not None:
        live.close()

    if hang is not None:
        hang.close()

    db.close()

    log.info("Slaughter measurement capture stopped.")
