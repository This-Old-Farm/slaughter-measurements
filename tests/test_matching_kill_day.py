"""
Replay a real slaughter day through matching to check that the yield
and balance thresholds flag wrong pairings on real weights.

The day was recorded before hang tags, so each HANG reading is given a
tag here to stand in for the new process.
"""

import sqlite3
import unittest

import matching
from tests.kill_day_2026_09_18 import HANG, LIVE

DATE = "2026-09-18"


def match_kill_day(tag_for_reading):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE measurements (
            id INTEGER PRIMARY KEY,
            order_of_slaughter INTEGER NOT NULL,
            scan_code TEXT NOT NULL,
            station TEXT NOT NULL,
            weight REAL NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
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

    for row_id, order, weight, recorded_at in LIVE:
        animal_id = f"{order:04d}B"
        conn.execute(
            "INSERT INTO measurements VALUES (?, ?, ?, 'live', ?, ?)",
            (row_id, order, animal_id, weight, recorded_at),
        )
        conn.execute(
            "INSERT INTO animal_details (measurement_id, animal_id, species, firearm, dirty,"
            " over_30_months, dairy, sex, recorded_at)"
            " VALUES (?, ?, 'BEEF', 0, 0, 0, 0, 'Steer', ?)",
            (row_id, animal_id, recorded_at),
        )

    for row_id, reading, weight, recorded_at in HANG:
        conn.execute(
            "INSERT INTO measurements VALUES (?, ?, ?, 'hang', ?, ?)",
            (row_id, reading, tag_for_reading(reading), weight, recorded_at),
        )

    try:
        return matching.match_day(conn, DATE)
    finally:
        conn.close()


def flagged(result):
    return [animal["order"] for animal in result["animals"] if animal["qa"]]


class KillDayReplayTest(unittest.TestCase):
    def test_reweigh_with_same_tag_lines_up_the_whole_day(self):
        # Reading 37 reweighs the half whose capture failed at 34.
        result = match_kill_day(lambda reading: "T34" if reading == 37 else f"T{reading}")

        self.assertEqual(flagged(result), [17])
        self.assertEqual(result["animals"][16]["qa"], ["hang tag T34 reweighed 2 times"])
        self.assertEqual(result["unmatched_hang"], [])

    def test_reweigh_with_new_tag_is_caught(self):
        # Worst case: a fresh tag for the reweigh shifts every later
        # pairing by one reading, as pairing by position did that day.
        result = match_kill_day(lambda reading: f"T{reading}")

        # Animal 21 is wrongly paired too, but its borrowed half weighs
        # almost the same as its own, so no weight check can see it. The
        # leftover tag at the end of the day still shows the shift.
        self.assertEqual(flagged(result), [17, 19, 20, 22, 23, 24, 25])
        self.assertEqual([scan["scan_code"] for scan in result["unmatched_hang"]], ["T51"])


if __name__ == "__main__":
    unittest.main()
