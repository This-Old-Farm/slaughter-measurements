import sqlite3
import unittest

import matching

DATE = "2026-10-20"


class MatchDayTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

        # Mirrors the tables listen.py creates.
        self.conn.execute(
            """
            CREATE TABLE measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_of_slaughter INTEGER NOT NULL,
                scan_code TEXT NOT NULL DEFAULT '',
                station TEXT NOT NULL,
                weight REAL NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE animal_details (
                measurement_id INTEGER PRIMARY KEY,
                animal_id TEXT NOT NULL,
                species TEXT NOT NULL,
                sex TEXT NOT NULL,
                over_30_months INTEGER NOT NULL DEFAULT 0
            )
            """
        )

    def tearDown(self):
        self.conn.close()

    def live(self, animal_id, species, sex, weight, over_30_months=False):
        cursor = self.conn.execute(
            "INSERT INTO measurements (order_of_slaughter, scan_code, station, weight, recorded_at)"
            " VALUES (0, ?, 'live', ?, ?)",
            (animal_id, weight, f"{DATE} 12:00:00"),
        )
        self.conn.execute(
            "INSERT INTO animal_details VALUES (?, ?, ?, ?, ?)",
            (cursor.lastrowid, animal_id, species, sex, int(over_30_months)),
        )

    def hang(self, tag, weight, date=DATE):
        self.conn.execute(
            "INSERT INTO measurements (order_of_slaughter, scan_code, station, weight, recorded_at)"
            " VALUES (0, ?, 'hang', ?, ?)",
            (tag, weight, f"{date} 12:00:00"),
        )

    def test_pairs_animals_with_tags_in_scan_order(self):
        self.live("1234B", "BEEF", "Steer", 1250, over_30_months=True)
        self.live("77L", "LAMB", "Lamb", 120)
        self.live("88P", "PORK", "Gilt", 280)
        for tag, weight in [("T9", 400), ("T2", 405), ("T5", 60), ("T1", 110), ("T7", 112)]:
            self.hang(tag, weight)

        result = matching.match_day(self.conn, DATE)

        animals = result["animals"]
        self.assertEqual(
            [[s["scan_code"] for s in a["hang"]] for a in animals],
            [["T9", "T2"], ["T5"], ["T1", "T7"]],
        )
        self.assertTrue(animals[0]["over_30_months"])
        self.assertTrue(all(a["complete"] for a in animals))
        self.assertTrue(all(a["qa"] == [] for a in animals))
        self.assertEqual(result["unmatched_hang"], [])

    def test_reweigh_uses_latest_weight_keeps_order_and_flags_qa(self):
        self.live("1234B", "BEEF", "Steer", 1250)
        self.live("1235B", "BEEF", "Cow", 1300)
        self.live("1234B", "BEEF", "Steer", 1248)
        self.hang("T1", 400)
        self.hang("T2", 405)
        self.hang("T3", 410)
        self.hang("T1", 402)
        self.hang("T4", 415)

        animals = matching.match_day(self.conn, DATE)["animals"]

        self.assertEqual([a["animal_id"] for a in animals], ["1234B", "1235B"])
        self.assertEqual(animals[0]["live"]["weight"], 1248)
        self.assertEqual(animals[0]["live"]["weights"], [1250, 1248])
        self.assertEqual(animals[0]["hang"][0]["weight"], 402)
        self.assertEqual(
            animals[0]["qa"],
            ["live reweighed 2 times", "hang tag T1 reweighed 2 times"],
        )
        self.assertEqual([s["scan_code"] for s in animals[1]["hang"]], ["T3", "T4"])
        self.assertEqual(animals[1]["qa"], [])

    def test_animal_waiting_on_hang_is_incomplete(self):
        self.live("1234B", "BEEF", "Steer", 1250)
        self.live("1235B", "BEEF", "Cow", 1300)
        self.hang("T1", 400)
        self.hang("T2", 405)
        self.hang("T3", 410)

        animals = matching.match_day(self.conn, DATE)["animals"]

        self.assertTrue(animals[0]["complete"])
        self.assertFalse(animals[1]["complete"])
        self.assertEqual([s["scan_code"] for s in animals[1]["hang"]], ["T3"])

    def test_extra_hang_tags_are_unmatched(self):
        self.live("77L", "LAMB", "Lamb", 120)
        self.hang("T1", 60)
        self.hang("T2", 61)

        result = matching.match_day(self.conn, DATE)

        self.assertEqual([s["scan_code"] for s in result["unmatched_hang"]], ["T2"])

    def test_other_days_and_unscanned_rows_are_ignored(self):
        self.live("77L", "LAMB", "Lamb", 120)
        self.hang("T0", 59, date="2026-10-19")
        self.hang("", 70)
        self.hang("T1", 60)

        result = matching.match_day(self.conn, DATE)

        self.assertEqual([s["scan_code"] for s in result["animals"][0]["hang"]], ["T1"])
        self.assertEqual(result["unmatched_hang"], [])

    def test_missing_species_assumes_two_tags_and_flags_qa(self):
        self.conn.execute(
            "INSERT INTO measurements (order_of_slaughter, scan_code, station, weight, recorded_at)"
            " VALUES (0, '1234B', 'live', 1250, ?)",
            (f"{DATE} 12:00:00",),
        )
        self.hang("T1", 400)
        self.hang("T2", 405)

        animal = matching.match_day(self.conn, DATE)["animals"][0]

        self.assertEqual(len(animal["hang"]), 2)
        self.assertEqual(animal["qa"], ["no species recorded at live"])


if __name__ == "__main__":
    unittest.main()
