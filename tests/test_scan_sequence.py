import unittest

from scan_sequence import AnimalSequence, get_species


class GetSpeciesTest(unittest.TestCase):
    def test_last_letter_is_species(self):
        self.assertEqual(get_species("1234B"), "BEEF")
        self.assertEqual(get_species("1234P"), "PORK")
        self.assertEqual(get_species("1234L"), "LAMB")

    def test_other_barcodes_are_not_animals(self):
        self.assertIsNone(get_species(""))
        self.assertIsNone(get_species("1234X"))

    def test_step_codes_are_never_animals(self):
        # Bull and Lamb end in a species letter.
        self.assertIsNone(get_species("B"))
        self.assertIsNone(get_species("L"))
        self.assertIsNone(get_species("CLEAN"))


class AnimalSequenceTest(unittest.TestCase):
    def test_beef_answers_every_step_in_order(self):
        sequence = AnimalSequence("1234B", "BEEF")

        for barcode in ["NOFIREARM", "DIRTY", "PLUS30", "NOTDAIRY", "S"]:
            self.assertIsNotNone(sequence.answer(barcode))

        self.assertEqual(
            sequence.answers,
            {
                "firearm": False,
                "dirty": True,
                "over_30_months": True,
                "dairy": False,
                "sex": "Steer",
            },
        )
        self.assertIsNone(sequence.next_step)
        self.assertEqual(sequence.missing(), [])

    def test_pork_skips_dirty_30_and_dairy(self):
        sequence = AnimalSequence("1234P", "PORK")

        self.assertEqual(sequence.answer("FIREARM"), "firearm")
        self.assertIsNone(sequence.answer("CLEAN"))
        self.assertEqual(sequence.answer("G"), "sex")
        self.assertEqual(sequence.answers, {"firearm": True, "sex": "Gilt"})

    def test_lamb_skips_30_and_dairy(self):
        sequence = AnimalSequence("1234L", "LAMB")

        self.assertEqual(sequence.answer("NOFIREARM"), "firearm")
        self.assertEqual(sequence.answer("CLEAN"), "dirty")
        self.assertIsNone(sequence.answer("UNDER30"))
        self.assertEqual(sequence.answer("L"), "sex")

    def test_scan_for_a_later_step_is_rejected(self):
        sequence = AnimalSequence("1234B", "BEEF")

        self.assertIsNone(sequence.answer("DIRTY"))
        self.assertIsNone(sequence.answer("S"))
        self.assertEqual(sequence.next_step, "firearm")

    def test_dairy_is_no_longer_a_sex_code(self):
        sequence = AnimalSequence("1234B", "BEEF")

        for barcode in ["NOFIREARM", "CLEAN", "UNDER30", "DAIRY"]:
            sequence.answer(barcode)

        self.assertIsNone(sequence.answer("D"))
        self.assertEqual(sequence.missing(), ["sex"])

    def test_sequence_ignores_scans_once_complete(self):
        sequence = AnimalSequence("1234P", "PORK")
        sequence.answer("NOFIREARM")
        sequence.answer("B")

        self.assertIsNone(sequence.answer("FIREARM"))
        self.assertEqual(sequence.answers["firearm"], False)
