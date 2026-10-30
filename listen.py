#!/usr/bin/python3

import logging
import os
import select
import signal
import sqlite3

import serial
from evdev import InputDevice, ecodes

import local_time
import scan_sequence

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

# One row per scan sequence: each animal ID scan starts one, and a
# rescan of the same ID starts another rather than changing the first.
# measurement_id links the weight once it is saved, and stays NULL if
# the animal is scanned again before a weight arrives. Answers stay
# NULL until they are scanned.
ANIMAL_DETAILS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS animal_details (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        measurement_id INTEGER UNIQUE,
        animal_id TEXT NOT NULL,
        species TEXT NOT NULL,
        firearm INTEGER,
        dirty INTEGER,
        over_30_months INTEGER,
        dairy INTEGER,
        sex TEXT,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (measurement_id)
            REFERENCES measurements(id)
    )
    """

# Databases created before #61 keyed details by measurement and
# required sex. SQLite can't change either in place, so rebuild the
# table. Old rows started when their weight was saved.
detail_columns = {row[1] for row in db.execute("PRAGMA table_info(animal_details)")}

if "id" not in detail_columns and detail_columns:
    with db:
        db.execute("ALTER TABLE animal_details RENAME TO animal_details_before_61")
        db.execute(ANIMAL_DETAILS_SCHEMA)
        db.execute("""
            INSERT INTO animal_details
                (id, measurement_id, animal_id, species, over_30_months, sex, recorded_at)
            SELECT d.measurement_id, d.measurement_id, d.animal_id, d.species,
                d.over_30_months, d.sex, m.recorded_at
            FROM animal_details_before_61 d
            JOIN measurements m ON m.id = d.measurement_id
            """)
        db.execute("DROP TABLE animal_details_before_61")

db.execute(ANIMAL_DETAILS_SCHEMA)

db.commit()


def get_next_order_of_slaughter(station_name):
    """
    Get the next order number for a given station for the current
    Indiana day.
    """
    start, end = local_time.utc_bounds(local_time.local_today())
    cursor = db.cursor()
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


def get_first_order_of_slaughter(station_name, scan_code):
    """
    Get the order number a scan code was first given at a station
    for the current Indiana day, or None if it has not been weighed yet.
    """
    start, end = local_time.utc_bounds(local_time.local_today())
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT MIN(order_of_slaughter)
        FROM measurements
        WHERE station = ? AND scan_code = ? AND recorded_at >= ? AND recorded_at < ?
        """,
        (station_name.lower(), scan_code, start, end),
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
    NEXT_SCAN_PROMPT = "Finish this animal's scans, or scan the next animal."

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        # The scan sequence for the most recent animal ID, and its
        # animal_details row. It stays after the weight is saved so
        # the remaining scans can still be recorded.
        self.sequence = None
        self.sequence_id = None

    def handle_barcode(self, barcode):

        barcode = barcode.upper()

        # --------------------------------------------------------
        # Next step for the current animal
        # --------------------------------------------------------

        if self.sequence is not None:
            step = self.sequence.answer(barcode)

            if step is not None:
                self.record_answer(step)

                return

        # --------------------------------------------------------
        # Anything else must be the next animal ID.
        # --------------------------------------------------------

        species = scan_sequence.get_species(barcode)

        if species is None:
            if self.sequence is not None and self.sequence.next_step is not None:
                log.warning(
                    "%s: Barcode %s ignored - expected %s scan for %s.",
                    self.name,
                    barcode,
                    self.sequence.next_step,
                    self.sequence.animal_id,
                )

            else:
                log.warning(
                    "%s: Invalid animal ID %s - expected ID ending in B, P, or L.",
                    self.name,
                    barcode,
                )

            return

        self.start_animal(barcode, species)

    def start_animal(self, animal_id, species):

        previous = self.sequence

        # The previous sequence is already saved. A rescan of the
        # same ID is how an operator starts over after a wrong scan;
        # matching decides which sequence to use.
        if previous is not None and previous.animal_id == animal_id:
            log.warning(
                "%s: %s rescanned%s - starting a new scan sequence. The earlier one is kept.",
                self.name,
                animal_id,
                " before its weight arrived" if self.scan_code is not None else "",
            )

        elif previous is not None and previous.missing():
            log.warning(
                "%s: %s left without %s scan(s).",
                self.name,
                previous.animal_id,
                ", ".join(previous.missing()),
            )

        # A second animal ID before a weight means the first
        # animal's weight was never captured.
        if self.scan_code is not None and previous.animal_id != animal_id:
            log.warning(
                "%s: %s replaces %s, which was never weighed. Its scans are kept.",
                self.name,
                animal_id,
                self.scan_code,
            )

        # The animal ID alone starts weight collection.
        self.scan_code = animal_id
        self.sequence = scan_sequence.AnimalSequence(animal_id, species)

        cursor = db.execute(
            "INSERT INTO animal_details (animal_id, species) VALUES (?, ?)",
            (animal_id, species),
        )

        db.commit()

        self.sequence_id = cursor.lastrowid

        log.info(
            "%s: ANIMAL: %s (%s)",
            self.name,
            animal_id,
            species,
        )

        log.info(
            "%s: Waiting for weight and %s scan...",
            self.name,
            self.sequence.next_step,
        )

    def record_answer(self, step):

        value = self.sequence.answers[step]

        log.info(
            "%s: %s: %s for %s.",
            self.name,
            step.upper(),
            value,
            self.sequence.animal_id,
        )

        # Each answer is written as it arrives so an unfinished
        # sequence keeps what was scanned.
        db.execute(
            f"UPDATE animal_details SET {step} = ? WHERE id = ?",
            (value, self.sequence_id),
        )

        db.commit()

        if self.sequence.next_step is not None:
            log.info(
                "%s: Waiting for %s scan...",
                self.name,
                self.sequence.next_step,
            )

        else:
            self.log_details()

    def save_measurement(self, weight):

        if self.scan_code is None:
            return

        measurement_id = super().save_measurement(weight)

        if measurement_id is None:
            return

        db.execute(
            "UPDATE animal_details SET measurement_id = ? WHERE id = ?",
            (measurement_id, self.sequence_id),
        )

        db.commit()

        if self.sequence.next_step is not None:
            log.info(
                "%s: Waiting for %s scan...",
                self.name,
                self.sequence.next_step,
            )

    def log_details(self):

        log.info(
            "%s: ANIMAL DETAILS: %s,%s,%s",
            self.name,
            self.sequence.animal_id,
            self.sequence.species,
            ",".join(f"{step}={value}" for step, value in self.sequence.answers.items()),
        )

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
