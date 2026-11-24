import unittest

import weight_slip


def animal(
    animal_id,
    species,
    sex,
    live_weight,
    hang_weights,
    order=1,
    complete=True,
    firearm=False,
    dirty=False,
    over_30_months=False,
    dairy=False,
):
    """A matched animal shaped like matching.match's output."""
    return {
        "order": order,
        "animal_id": animal_id,
        "species": species,
        "firearm": firearm,
        "dirty": dirty,
        "over_30_months": over_30_months if species == "BEEF" else None,
        "dairy": dairy if species == "BEEF" else None,
        "sex": sex,
        "live": {"weight": live_weight},
        "hang": [{"weight": weight} for weight in hang_weights],
        "complete": complete,
    }


def slip(*animals):
    return weight_slip.weight_slip({"animals": list(animals), "unmatched_hang": []})


class WeightSlipTest(unittest.TestCase):
    def test_beef_row_fills_the_weight_slip_columns(self):
        row = slip(
            animal("2191B", "BEEF", "Steer", 1250, [400, 405], order=3, firearm=True, over_30_months=True)
        )["rows"][0]

        self.assertEqual(row["number"], 2191)
        self.assertEqual(row["species"], "B")
        self.assertEqual(row["weights"], ["TRUE", "S", "1250", "400", "405"])
        self.assertEqual(row["qa"], ["FIREARM", "3"])

    def test_dairy_cow_is_d_and_other_dairy_keeps_its_code(self):
        rows = slip(
            animal("1B", "BEEF", "Cow", 1300, [400, 400], dairy=True),
            animal("2B", "BEEF", "Heifer", 1100, [350, 350], dairy=True),
            animal("3B", "BEEF", "Cow", 1300, [400, 400], dairy=False),
        )["rows"]

        self.assertEqual([row["weights"][1] for row in rows], ["D", "H", "C"])

    def test_hogs_and_lambs_get_the_blood_estimate(self):
        rows = slip(
            animal("10P", "PORK", "Boar", 403, [133, 140]),
            animal("11L", "LAMB", "Lamb", 120, [60]),
        )["rows"]

        self.assertEqual(rows[0]["weights"], ["", "BO", "411", "133", "140"])
        self.assertEqual(rows[1]["weights"], ["", "L", "124", "60", ""])

    def test_gaps_and_unfinished_animals_get_blank_rows(self):
        rows = slip(
            animal("20B", "BEEF", "Steer", 1250, [400, 405], order=1),
            animal("21B", "BEEF", "Steer", 1250, [400], order=2, complete=False),
            animal("23B", "BEEF", "Steer", 1250, [400, 405], order=3, dirty=True),
        )["rows"]

        self.assertEqual([row["number"] for row in rows], [20, 21, 22, 23])
        self.assertEqual([row["status"] for row in rows], ["", "not finished", "not scanned", ""])
        self.assertEqual(rows[1]["weights"], [""] * 5)
        self.assertEqual(rows[2]["qa"], ["", ""])
        self.assertEqual(rows[3]["qa"], ["DIRTY", "3"])

    def test_missing_weight_is_not_finished(self):
        rows = slip(
            animal("30B", "BEEF", "Steer", None, [400, 405]),
            animal("31B", "BEEF", "Steer", 1250, [400, None]),
        )["rows"]

        self.assertEqual([row["status"] for row in rows], ["not finished", "not finished"])

    def test_rows_follow_animal_number_not_scan_order(self):
        rows = slip(
            animal("41B", "BEEF", "Steer", 1250, [400, 405], order=1),
            animal("40B", "BEEF", "Steer", 1250, [400, 405], order=2),
        )["rows"]

        self.assertEqual([(row["number"], row["qa"][1]) for row in rows], [(40, "2"), (41, "1")])

    def test_id_without_a_number_is_listed_separately(self):
        result = slip(animal("B", "BEEF", "Steer", 1250, [400, 405]))

        self.assertEqual(result["rows"], [])
        self.assertEqual(result["unplaced"], ["B"])

    def test_copy_blocks_are_tab_separated_rows(self):
        rows = slip(
            animal("1B", "BEEF", "Steer", 1250, [400, 405]),
            animal("3B", "BEEF", "Steer", 1250.5, [400, 405]),
        )["rows"]

        self.assertEqual(
            weight_slip.tsv(rows, "weights"),
            "FALSE\tS\t1250\t400\t405\n\t\t\t\t\nFALSE\tS\t1250.5\t400\t405",
        )

    def test_page_renders_and_escapes(self):
        page = weight_slip.render("2026-11-25", slip(animal("<b>", "BEEF", "Steer", 1, [1, 1])))

        self.assertIn("Copy weights (M to Q)", page)
        self.assertIn("&lt;b&gt;", page)
        self.assertNotIn("<b>", page)


if __name__ == "__main__":
    unittest.main()
