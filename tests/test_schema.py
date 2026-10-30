import sqlite3
import unittest

import schema


class InitTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_new_database_gets_both_tables(self):
        schema.init(self.conn)

        self.assertIn("scan_code", schema.columns(self.conn, "measurements"))
        self.assertIn("firearm", schema.columns(self.conn, "animal_details"))

    def test_details_from_before_61_are_rebuilt_and_kept(self):
        self.conn.executescript(
            """
            CREATE TABLE measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_of_slaughter INTEGER NOT NULL,
                scan_code TEXT NOT NULL DEFAULT '',
                station TEXT NOT NULL,
                weight REAL NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE animal_details (
                measurement_id INTEGER PRIMARY KEY,
                animal_id TEXT NOT NULL,
                species TEXT NOT NULL,
                sex TEXT NOT NULL,
                over_30_months INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO measurements (order_of_slaughter, scan_code, station, weight, recorded_at)
                VALUES (1, '1234B', 'live', 1250, '2026-10-20 12:00:00');
            INSERT INTO animal_details VALUES (1, '1234B', 'BEEF', 'Dairy', 1);
            """
        )

        schema.init(self.conn)
        schema.init(self.conn)

        self.assertEqual(
            self.conn.execute(
                "SELECT id, measurement_id, animal_id, species, firearm, over_30_months, sex,"
                " recorded_at FROM animal_details"
            ).fetchall(),
            [(1, 1, "1234B", "BEEF", None, 1, "Dairy", "2026-10-20 12:00:00")],
        )

        # Sex can be blank now, and new rows number after the old ones.
        cursor = self.conn.execute(
            "INSERT INTO animal_details (animal_id, species) VALUES ('1235B', 'BEEF')"
        )
        self.assertEqual(cursor.lastrowid, 2)

    def test_measurements_from_before_scan_code_get_the_column(self):
        self.conn.execute(
            """
            CREATE TABLE measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_of_slaughter INTEGER NOT NULL,
                station TEXT NOT NULL,
                weight REAL NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        schema.init(self.conn)

        self.assertIn("scan_code", schema.columns(self.conn, "measurements"))


if __name__ == "__main__":
    unittest.main()
