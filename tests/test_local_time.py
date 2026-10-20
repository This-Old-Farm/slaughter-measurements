import datetime
import unittest

import local_time


class UtcBoundsTest(unittest.TestCase):
    def test_daylight_saving_day_starts_at_4am_utc(self):
        self.assertEqual(
            local_time.utc_bounds("2026-10-20"),
            ("2026-10-20 04:00:00", "2026-10-21 04:00:00"),
        )

    def test_standard_time_day_starts_at_5am_utc(self):
        self.assertEqual(
            local_time.utc_bounds(datetime.date(2026, 12, 1)),
            ("2026-12-01 05:00:00", "2026-12-02 05:00:00"),
        )

    def test_dst_change_days_are_25_and_23_hours(self):
        self.assertEqual(
            local_time.utc_bounds("2026-11-01"),
            ("2026-11-01 04:00:00", "2026-11-02 05:00:00"),
        )
        self.assertEqual(
            local_time.utc_bounds("2026-03-08"),
            ("2026-03-08 05:00:00", "2026-03-09 04:00:00"),
        )

    def test_invalid_date_raises(self):
        with self.assertRaises(ValueError):
            local_time.utc_bounds("not-a-date")


class ToLocalTest(unittest.TestCase):
    def test_evening_utc_is_previous_indiana_day(self):
        local = local_time.to_local(datetime.datetime(2026, 10, 21, 0, 30))

        self.assertEqual(local.strftime("%Y-%m-%d %H:%M"), "2026-10-20 20:30")

    def test_winter_offset_is_five_hours(self):
        local = local_time.to_local(datetime.datetime(2026, 12, 1, 17, 0))

        self.assertEqual(local.strftime("%H:%M"), "12:00")


if __name__ == "__main__":
    unittest.main()
