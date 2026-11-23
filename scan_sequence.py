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

The animal ID's QR code also carries the offals the farmer asked to
keep, in brackets after a space: 1234B [L,H,T]. Empty brackets mean
the farmer asked for none (#71).
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

OFFAL_CODES = {
    "L": "Liver",
    "H": "Heart",
    "T": "Tongue",
    "O": "Oxtail",
    "K": "Kidney",
    "SB": "Sweetbreads",
    "HE": "Head",
    "HI": "Hide",
    "CF": "Caul fat",
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


# Hogs and lambs are bled before the live weight is taken, so pages
# show an estimate with the blood added back (#66). The estimate is
# calculated when it is shown and never saved: the database holds only
# real measurements. Each species maps to (up to and including this
# many lb, add this many lb) brackets, in order; None means no upper
# limit.
BLOOD_WEIGHT = {
    "PORK": [(350, 6), (500, 8), (None, 10)],
    "LAMB": [(None, 4)],
}


def estimate_with_blood(species, weight):
    """
    An estimate of the live weight with the blood added back, or None
    when there is nothing to estimate: a species that isn't bled first,
    no weight, or a zero or negative reading (empty or unsettled scale).
    """
    if weight is None or weight <= 0:
        return None

    for up_to, blood in BLOOD_WEIGHT.get(species, []):
        if up_to is None or weight <= up_to:
            return weight + blood

    return None


def get_species(barcode):
    """
    The species an animal ID belongs to, from its last letter, or None
    if the barcode is not an animal ID.
    """
    if not barcode or barcode in STEP_CODES:
        return None

    return SPECIES_CODES.get(barcode[-1])


def parse_animal_scan(barcode):
    """
    Split an animal ID scan into the ID and its offal codes.

    Returns (animal_id, offals). offals is a list, empty when the
    farmer asked for none, or None when the scan has no brackets (a
    label printed before #71). Returns None if the brackets are
    malformed, such as a scan cut off before the closing bracket.
    Unknown offal codes are kept as scanned so a label typo still
    shows on screen.
    """
    if "[" not in barcode and "]" not in barcode:
        return barcode.strip(), None

    animal_id, bracket, rest = barcode.partition("[")

    if not bracket or not rest.endswith("]") or "[" in rest or "]" in rest[:-1]:
        return None

    animal_id = animal_id.strip()
    items = rest[:-1].split(",")
    offals = [item.strip() for item in items if item.strip()]

    if not animal_id:
        return None

    return animal_id, offals


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
