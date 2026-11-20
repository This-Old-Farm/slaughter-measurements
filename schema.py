"""
Create and migrate the measurement database.

listen.py and shout.py both call init() at startup, so either one can
start first, on a new database or one made by older code.
"""

MEASUREMENTS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_of_slaughter INTEGER NOT NULL,
        scan_code TEXT NOT NULL DEFAULT '',
        station TEXT NOT NULL,
        weight REAL NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """

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


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def init(conn):

    # Hold the write lock for the whole check-and-migrate, so when both
    # processes start at once only one migrates and the other sees the
    # result. Without an explicit transaction, sqlite3 runs each schema
    # statement on its own.
    conn.execute("BEGIN IMMEDIATE")

    try:
        conn.execute(MEASUREMENTS_SCHEMA)

        # Databases created before scan_code existed need the column added.
        # barcode_buffer was its name on main before it was ever deployed.
        measurement_columns = columns(conn, "measurements")

        if "barcode_buffer" in measurement_columns:
            conn.execute("ALTER TABLE measurements RENAME COLUMN barcode_buffer TO scan_code")
        elif "scan_code" not in measurement_columns:
            conn.execute("ALTER TABLE measurements ADD COLUMN scan_code TEXT NOT NULL DEFAULT ''")

        # Databases created before #61 keyed details by measurement and
        # required sex. SQLite can't change either in place, so rebuild
        # the table. Old rows started when their weight was saved.
        detail_columns = columns(conn, "animal_details")

        if detail_columns and "id" not in detail_columns:
            conn.execute("ALTER TABLE animal_details RENAME TO animal_details_before_61")
            conn.execute(ANIMAL_DETAILS_SCHEMA)
            conn.execute("""
                INSERT INTO animal_details
                    (id, measurement_id, animal_id, species, over_30_months, sex, recorded_at)
                SELECT d.measurement_id, d.measurement_id, d.animal_id, d.species,
                    d.over_30_months, d.sex, m.recorded_at
                FROM animal_details_before_61 d
                JOIN measurements m ON m.id = d.measurement_id
                """)
            conn.execute("DROP TABLE animal_details_before_61")

        conn.execute(ANIMAL_DETAILS_SCHEMA)

        conn.commit()

    except BaseException:
        conn.rollback()
        raise
