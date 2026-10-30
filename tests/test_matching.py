import sqlite3
import unittest

import matching
import scan_sequence

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
            )
            """
        )

    def tearDown(self):
        self.conn.close()

    def live(self, animal_id, species, sex, weight, over_30_months=False, time="12:00:00"):
        recorded_at = f"{DATE} {time}"
        cursor = self.conn.execute(
            "INSERT INTO measurements (order_of_slaughter, scan_code, station, weight, recorded_at)"
            " VALUES (0, ?, 'live', ?, ?)",
            (animal_id, weight, recorded_at),
        )
        self.sequence(
            animal_id,
            species,
            measurement_id=cursor.lastrowid,
            firearm=False,
            dirty=False,
            over_30_months=over_30_months,
            dairy=False,
            sex=sex,
            time=time,
        )

    def sequence(self, animal_id, species, measurement_id=None, time="12:00:00", **answers):
        """Insert a scan sequence with the answers that apply to its species."""
        steps = [step for step in scan_sequence.STEPS[species] if step in answers]
        self.conn.execute(
            f"INSERT INTO animal_details (measurement_id, animal_id, species, recorded_at"
            f"{''.join(', ' + step for step in steps)})"
            f" VALUES (?, ?, ?, ?{', ?' * len(steps)})",
            (measurement_id, animal_id, species, f"{DATE} {time}", *(answers[s] for s in steps)),
        )

    def hang(self, tag, weight, date=DATE, time="12:00:00"):
        self.conn.execute(
            "INSERT INTO measurements (order_of_slaughter, scan_code, station, weight, recorded_at)"
            " VALUES (0, ?, 'hang', ?, ?)",
            (tag, weight, f"{date} {time}"),
        )

    def test_pairs_animals_with_tags_in_scan_order(self):
        self.live("1234B", "BEEF", "Steer", 1250, over_30_months=True)
        self.live("77L", "LAMB", "Lamb", 120)
        self.live("88P", "PORK", "Gilt", 280)
        for tag, weight in [("T9", 400), ("T2", 405), ("T5", 60), ("T1", 100), ("T7", 102)]:
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
            [
                "live reweighed 2 times",
                "live scan sequence entered 2 times, using entry 2",
                "hang tag T1 reweighed 2 times",
            ],
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

    def test_day_follows_indiana_time_not_utc(self):
        self.live("77L", "LAMB", "Lamb", 120)
        # 11:30 PM on Oct 19 in Indiana, stored as the 20th in UTC.
        self.hang("T0", 59, date="2026-10-20", time="03:30:00")
        # 8:30 PM on Oct 20 in Indiana, stored as the 21st in UTC.
        self.hang("T1", 60, date="2026-10-21", time="00:30:00")

        result = matching.match_day(self.conn, DATE)

        self.assertEqual([s["scan_code"] for s in result["animals"][0]["hang"]], ["T1"])
        self.assertEqual(result["unmatched_hang"], [])

    def test_failed_capture_then_reweigh_uses_valid_weight(self):
        self.live("1234B", "BEEF", "Steer", 1250)
        self.hang("T1", -2)
        self.hang("T1", 402)
        self.hang("T2", 405)

        animal = matching.match_day(self.conn, DATE)["animals"][0]

        self.assertEqual(animal["hang"][0]["weights"], [-2, 402])
        self.assertEqual(animal["hang"][0]["weight"], 402)
        self.assertEqual(animal["checks"]["yield"]["verdict"], "ok")
        self.assertEqual(animal["qa"], ["hang tag T1 reweighed 2 times"])

    def test_only_failed_capture_keeps_order_and_flags_qa(self):
        self.live("1234B", "BEEF", "Steer", 1250)
        self.live("1235B", "BEEF", "Cow", 1300)
        self.hang("T1", 0)
        self.hang("T2", 405)
        self.hang("T3", 410)
        self.hang("T4", 415)

        first, second = matching.match_day(self.conn, DATE)["animals"]

        self.assertIsNone(first["hang"][0]["weight"])
        self.assertEqual(first["qa"], ["hang tag T1 has no valid weight"])
        self.assertEqual(first["checks"]["yield"]["verdict"], "unknown")
        self.assertEqual(first["checks"]["balance"]["verdict"], "unknown")
        self.assertEqual([s["scan_code"] for s in second["hang"]], ["T3", "T4"])
        self.assertEqual(second["qa"], [])

    def test_failed_live_capture_flags_qa(self):
        self.live("1234B", "BEEF", "Steer", -6)
        self.hang("T1", 400)
        self.hang("T2", 405)

        animal = matching.match_day(self.conn, DATE)["animals"][0]

        self.assertIsNone(animal["live"]["weight"])
        self.assertEqual(animal["qa"], ["live has no valid weight"])
        self.assertEqual(animal["checks"]["yield"]["verdict"], "unknown")

    def test_implausible_yield_flags_qa(self):
        # Halves from a much smaller animal: a missed or extra hang scan
        # earlier in the day shifts every later pairing like this.
        self.live("1234B", "BEEF", "Steer", 1250)
        self.hang("T1", 200)
        self.hang("T2", 195)

        animal = matching.match_day(self.conn, DATE)["animals"][0]

        self.assertEqual(animal["checks"]["yield"]["percent"], 31.6)
        self.assertEqual(animal["checks"]["yield"]["verdict"], "warn")
        self.assertEqual(animal["qa"], ["yield 31.6% outside 52-67% for BEEF"])

    def test_uneven_halves_flag_qa(self):
        # Plausible total, but the halves belong to two different animals.
        self.live("1234B", "BEEF", "Steer", 1250)
        self.hang("T1", 450)
        self.hang("T2", 360)

        animal = matching.match_day(self.conn, DATE)["animals"][0]

        self.assertEqual(animal["checks"]["yield"]["verdict"], "ok")
        self.assertEqual(animal["checks"]["balance"]["difference_percent"], 22.2)
        self.assertEqual(animal["checks"]["balance"]["verdict"], "warn")
        self.assertEqual(animal["qa"], ["halves differ by 22.2% (limit 15%)"])

    def test_lamb_has_yield_but_no_balance_check(self):
        self.live("77L", "LAMB", "Lamb", 120)
        self.hang("T1", 60)

        animal = matching.match_day(self.conn, DATE)["animals"][0]

        self.assertEqual(animal["checks"]["yield"]["verdict"], "ok")
        self.assertIsNone(animal["checks"]["balance"])

    def test_incomplete_animal_is_not_checked_yet(self):
        self.live("1234B", "BEEF", "Steer", 1250)
        self.hang("T1", 200)

        animal = matching.match_day(self.conn, DATE)["animals"][0]

        self.assertEqual(animal["checks"], {"yield": None, "balance": None})
        self.assertEqual(animal["qa"], [])

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

    def weigh(self, animal_id, weight, time="12:00:00"):
        cursor = self.conn.execute(
            "INSERT INTO measurements (order_of_slaughter, scan_code, station, weight, recorded_at)"
            " VALUES (0, ?, 'live', ?, ?)",
            (animal_id, weight, f"{DATE} {time}"),
        )
        return cursor.lastrowid

    def test_unscanned_steps_are_none_not_no_and_flag_qa(self):
        self.sequence(
            "1234B",
            "BEEF",
            measurement_id=self.weigh("1234B", 1250),
            firearm=True,
            dirty=False,
        )

        animal = matching.match_day(self.conn, DATE)["animals"][0]

        self.assertTrue(animal["firearm"])
        self.assertFalse(animal["dirty"])
        self.assertIsNone(animal["over_30_months"])
        self.assertIsNone(animal["dairy"])
        self.assertIsNone(animal["sex"])
        self.assertEqual(animal["qa"], ["live scan sequence missing over_30_months, dairy, sex"])

    def test_more_complete_rescan_overrides_and_keeps_the_original(self):
        self.sequence("1234B", "BEEF", self.weigh("1234B", 1250), firearm=False, dirty=True)
        self.sequence(
            "1234B",
            "BEEF",
            self.weigh("1234B", 1250, time="12:01:00"),
            time="12:01:00",
            firearm=False,
            dirty=False,
            over_30_months=False,
            dairy=False,
            sex="Steer",
        )

        animal = matching.match_day(self.conn, DATE)["animals"][0]
        first, second = animal["live"]["sequences"]

        self.assertFalse(animal["dirty"])
        self.assertEqual(animal["sex"], "Steer")
        self.assertFalse(first["in_use"])
        self.assertTrue(first["dirty"])
        self.assertEqual(first["missing"], ["over_30_months", "dairy", "sex"])
        self.assertTrue(second["in_use"])
        self.assertIn("live scan sequence entered 2 times, using entry 2", animal["qa"])

    def test_less_complete_rescan_does_not_override(self):
        self.sequence("1234P", "PORK", self.weigh("1234P", 280), firearm=False, sex="Gilt")
        self.sequence("1234P", "PORK", time="12:01:00", firearm=True)

        animal = matching.match_day(self.conn, DATE)["animals"][0]

        self.assertFalse(animal["firearm"])
        self.assertEqual(animal["sex"], "Gilt")
        self.assertEqual(animal["qa"], ["live scan sequence entered 2 times, using entry 1"])

    def test_equally_complete_rescan_wins(self):
        self.sequence("1234P", "PORK", self.weigh("1234P", 280), firearm=True, sex="Gilt")
        self.sequence("1234P", "PORK", time="12:01:00", firearm=False, sex="Gilt")

        animal = matching.match_day(self.conn, DATE)["animals"][0]

        self.assertFalse(animal["firearm"])
        self.assertEqual(animal["live"]["weight"], 280)

    def test_sequence_without_weight_keeps_its_place(self):
        self.sequence("1234L", "LAMB", time="12:00:00", firearm=False)
        self.live("1235L", "LAMB", "Lamb", 110, time="12:05:00")
        self.hang("T1", 55)
        self.hang("T2", 56)

        first, second = matching.match_day(self.conn, DATE)["animals"]

        self.assertEqual(first["animal_id"], "1234L")
        self.assertEqual([s["scan_code"] for s in first["hang"]], ["T1"])
        self.assertEqual(
            first["qa"],
            ["live has no valid weight", "live scan sequence missing dirty, sex"],
        )
        self.assertEqual([s["scan_code"] for s in second["hang"]], ["T2"])


if __name__ == "__main__":
    unittest.main()
