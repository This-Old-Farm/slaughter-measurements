"""
The live station's scan sequence for one animal.

The animal ID starts weight collection right away. After it, the
operator scans one answer per step, in order, for the steps that
apply to the animal's species (#61):

    firearm         FIREARM / NOFIREARM         beef, pork, lamb
    dirty           DIRTY / CLEAN               beef, lamb
    over_30_months  PLUS30 / UNDER30            beef
    dairy           DAIRY / NOTDAIRY            beef
    sex             a sex/type code             beef, pork, lamb

Every step is required, so a "no" is scanned too. Each answer has its
own barcode, so a scan meant for another step is rejected instead of
being recorded as the wrong answer.
"""

SPECIES_CODES = {
    "B": "BEEF",
    "P": "PORK",
    "L": "LAMB",
}

SEX_CODES = {
    "BEEF": {
        "B": "Bull",
        "H": "Heifer",
        "S": "Steer",
        "C": "Cow",
    },
    "PORK": {
        "B": "Barrow",
        "G": "Gilt",
        "BO": "Boar",
        "S": "Sow",
    },
    "LAMB": {
        "L": "Lamb",
        "G": "Goat",
        "M": "Mutton",
    },
}

YES_NO_CODES = {
    "firearm": {"FIREARM": True, "NOFIREARM": False},
    "dirty": {"DIRTY": True, "CLEAN": False},
    "over_30_months": {"PLUS30": True, "UNDER30": False},
    "dairy": {"DAIRY": True, "NOTDAIRY": False},
}

# What each step is called on screen.
STEP_LABELS = {
    "firearm": "Firearm",
    "dirty": "Dirty",
    "over_30_months": "+30 months",
    "dairy": "Dairy",
    "sex": "Sex/type",
}

# Step names double as animal_details column names.
STEPS = {
    "BEEF": ["firearm", "dirty", "over_30_months", "dairy", "sex"],
    "PORK": ["firearm", "sex"],
    "LAMB": ["firearm", "dirty", "sex"],
}

# Every barcode that answers a step. None of them is ever an animal
# ID, even one like "B" that ends in a species letter.
STEP_CODES = {code for codes in YES_NO_CODES.values() for code in codes} | {
    code for codes in SEX_CODES.values() for code in codes
}


def get_species(barcode):
    """
    The species an animal ID belongs to, from its last letter, or None
    if the barcode is not an animal ID.
    """
    if not barcode or barcode in STEP_CODES:
        return None

    return SPECIES_CODES.get(barcode[-1])


def step_codes(species, step):
    """The barcodes that answer a step for a species, and their values."""
    if step == "sex":
        return SEX_CODES[species]

    return YES_NO_CODES[step]


class AnimalSequence:
    def __init__(self, animal_id, species):
        self.animal_id = animal_id
        self.species = species
        self.steps = STEPS[species]
        self.answers = {step: None for step in self.steps}

    @property
    def next_step(self):
        """The step waiting for a scan, or None once every step is answered."""
        for step in self.steps:
            if self.answers[step] is None:
                return step

        return None

    def missing(self):
        return [step for step in self.steps if self.answers[step] is None]

    def answer(self, barcode):
        """
        Record a barcode as the answer to the next step. Returns the
        step it answered, or None if it doesn't answer that step.
        """
        step = self.next_step

        if step is None:
            return None

        codes = step_codes(self.species, step)

        if barcode not in codes:
            return None

        self.answers[step] = codes[barcode]

        return step
