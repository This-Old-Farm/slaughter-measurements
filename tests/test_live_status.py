import sqlite3
import unittest

from live_status import live_status

DATE = "2026-10-20"


class LiveStatusTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_of_slaughter INTEGER NOT NULL,
                scan_code TEXT NOT NULL,
                station TEXT NOT NULL,
                weight REAL NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE animal_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                measurement_id INTEGER UNIQUE,
                animal_id TEXT NOT NULL,
                species TEXT NOT NULL,
                firearm INTEGER,
                dirty INTEGER,
                over_30_months INTEGER,
                dairy INTEGER,
                sex TEXT,
                recorded_at TEXT NOT NULL
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def weigh(self, animal_id, weight, time="12:00:00"):
        cursor = self.conn.execute(
            "INSERT INTO measurements (order_of_slaughter, scan_code, station, weight, recorded_at)"
            " VALUES (0, ?, 'live', ?, ?)",
            (animal_id, weight, f"{DATE} {time}"),
        )
        return cursor.lastrowid

    def sequence(self, animal_id, species, measurement_id=None, time="12:00:00", **answers):
        columns = "".join(f", {step}" for step in answers)
        self.conn.execute(
            f"INSERT INTO animal_details (measurement_id, animal_id, species, recorded_at{columns})"
            f" VALUES (?, ?, ?, ?{', ?' * len(answers)})",
            (measurement_id, animal_id, species, f"{DATE} {time}", *answers.values()),
        )

    def test_nothing_scanned_yet(self):
        status = live_status(self.conn, DATE)

        self.assertIsNone(status["current"])
        self.assertEqual(status["animals"], [])

    def test_current_is_the_latest_sequence_with_its_next_step(self):
        self.sequence(
            "1234B",
            "BEEF",
            self.weigh("1234B", 1250),
            firearm=0,
            dirty=0,
            over_30_months=0,
            dairy=0,
            sex="Steer",
        )
        self.sequence("1235B", "BEEF", time="12:10:00", firearm=1)

        current = live_status(self.conn, DATE)["current"]

        self.assertEqual(current["animal_id"], "1235B")
        self.assertEqual(current["order"], 2)
        self.assertIsNone(current["weight"])
        self.assertFalse(current["weight_failed"])
        self.assertEqual(current["next_step"], "dirty")
        self.assertEqual(
            [(s["step"], s["answer"]) for s in current["steps"]],
            [
                ("firearm", True),
                ("dirty", None),
                ("over_30_months", None),
                ("dairy", None),
                ("sex", None),
            ],
        )
        self.assertEqual(
            current["steps"][1]["codes"],
            [{"code": "DIRTY", "value": True}, {"code": "CLEAN", "value": False}],
        )

    def test_rescan_shows_the_new_sequence_not_the_better_one(self):
        self.sequence("1234P", "PORK", self.weigh("1234P", 280), firearm=0, sex="Gilt")
        self.sequence("1234P", "PORK", time="12:01:00")

        current = live_status(self.conn, DATE)["current"]

        self.assertEqual((current["entry"], current["entries"]), (2, 2))
        self.assertEqual(current["next_step"], "firearm")
        self.assertIsNone(current["weight"])

    def test_failed_reading_needs_a_rescan(self):
        self.sequence("1234L", "LAMB", self.weigh("1234L", -2), firearm=0, dirty=0, sex="Lamb")

        current = live_status(self.conn, DATE)["current"]

        self.assertIsNone(current["weight"])
        self.assertTrue(current["weight_failed"])
        self.assertIsNone(current["next_step"])

    def test_animals_show_only_live_flags(self):
        self.sequence("1234L", "LAMB", self.weigh("1234L", 110), firearm=0)
        self.conn.execute(
            "INSERT INTO measurements (order_of_slaughter, scan_code, station, weight, recorded_at)"
            f" VALUES (0, 'T1', 'hang', 55, '{DATE} 12:30:00')"
        )
        self.conn.execute(
            "INSERT INTO measurements (order_of_slaughter, scan_code, station, weight, recorded_at)"
            f" VALUES (0, 'T1', 'hang', 56, '{DATE} 12:31:00')"
        )

        animal = live_status(self.conn, DATE)["animals"][0]

        self.assertEqual(animal["weight"], 110)
        self.assertEqual(animal["answers"], {"firearm": False, "dirty": None, "sex": None})
        self.assertEqual(animal["qa"], ["scan sequence missing dirty, sex"])


if __name__ == "__main__":
    unittest.main()
