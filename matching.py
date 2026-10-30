"""
Match live station animals to hang station tags.

This is a prototype that may move to lapis, so it only reads the
measurements and animal_details tables and nothing else in this
repository depends on how it works.

Animals are matched to hang tags by order: the nth animal scanned at
the live station owns the next hang tags in the order they were first
scanned, two halves for BEEF and PORK and one carcass for LAMB.

Every weight stays in the database. Matching uses the most recent
valid weight for each code and flags anything weighed more than once
for QA.

Every live scan sequence stays in the database too. Matching takes an
animal's details from its most complete sequence, the newest on a tie,
and flags animals scanned more than once or missing a step.

Each complete animal also gets two plausibility checks, carried over
from the matching GUI prototype in This-Old-Farm/lapis#23, so a wrong
pairing is visible rather than silently accepted:

- yield: hang total / live weight, against the species' dressing band
- balance: how evenly two halves split, since halves of one carcass
  weigh close to the same
"""

import local_time
import scan_sequence

HANG_TAGS_PER_SPECIES = {
    "BEEF": 2,
    "PORK": 2,
    "LAMB": 1,
}

# Used when a live scan has no animal_details row, so the rest of the
# day still lines up as well as it can. The animal is flagged for QA.
DEFAULT_HANG_TAGS = 2

# Dressing-yield QA bands (percent), from the Weight Slip's own header.
YIELD_BANDS = {
    "BEEF": (57.0, 62.0),
    "PORK": (67.0, 72.0),
    "LAMB": (47.0, 52.0),
}

# How far outside its QA band a real animal plausibly falls. On
# 2026-09-18 the 22 known-good pairings ran 54.7-65.8% against a stated
# 57-62%; with this slack the day flags exactly its wrong pairings.
YIELD_SLACK = 5.0

# Largest plausible gap between two halves, as a percent of their mean.
# On 2026-09-18 good pairs differed by at most 9.6%; two wrong pairs that
# passed the yield check differed by 19% and 20%.
HALF_IMBALANCE_LIMIT = 15.0


def capture_failed(weight):
    """
    Whether a reading has no carcass weight in it. A zero or negative
    reading means the scale was empty or hadn't settled when the code
    was scanned. Values seen so far: 0, -1, -2, -5, -5.5, -6, and -8.
    """
    return weight <= 0


def yes_no(value):
    """An animal_details yes/no column as True, False, or None."""
    return None if value is None else bool(value)


def load_scans(conn, station, date):
    """
    Return one dict per scan code scanned at a station on an Indiana
    date (YYYY-MM-DD), in the order each code was first scanned.

    Measurements without a scan code were recorded before scan codes
    existed and cannot be matched, so they are left out.
    """
    start, end = local_time.utc_bounds(date)

    rows = conn.execute(
        """
        SELECT id, scan_code, weight, recorded_at
        FROM measurements
        WHERE station = ? AND scan_code != ''
            AND recorded_at >= ? AND recorded_at < ?
        ORDER BY id
        """,
        (station, start, end),
    ).fetchall()

    scans = {}
    first_scanned = {}
    weights = {}

    for measurement_id, scan_code, weight, recorded_at in rows:
        scan = scans.setdefault(scan_code, new_scan(scan_code))
        first_scanned.setdefault(scan_code, recorded_at)
        weights[measurement_id] = weight

        # Rows arrive oldest first, so the last valid one wins. A
        # failed capture stays in the history but never becomes the
        # weight; a code with only failed captures keeps its place in
        # the order with no weight.
        scan["weights"].append(weight)

        if not capture_failed(weight):
            scan["weight"] = weight
            scan["measurement_id"] = measurement_id
            scan["recorded_at"] = recorded_at

    if station == "live":
        for sequence in load_sequences(conn, start, end, weights):
            animal_id = sequence["animal_id"]
            scans.setdefault(animal_id, new_scan(animal_id))
            scans[animal_id]["sequences"].append(sequence)

            # An animal whose weight never arrived still holds its
            # place in the order from when its ID was scanned.
            first = first_scanned.setdefault(animal_id, sequence["recorded_at"])
            first_scanned[animal_id] = min(first, sequence["recorded_at"])

        for scan in scans.values():
            use_best_sequence(scan)

    # sorted() is stable, so codes first scanned in the same second
    # keep the order they were read in.
    return sorted(scans.values(), key=lambda scan: first_scanned[scan["scan_code"]])


def new_scan(scan_code):
    return {
        "scan_code": scan_code,
        "weights": [],
        "weight": None,
        "sequences": [],
    }


def load_sequences(conn, start, end, weights):
    """
    Every live scan sequence started in a UTC range, oldest first,
    numbered per animal. weights maps measurement ids to weights.
    """
    rows = conn.execute(
        """
        SELECT
            measurement_id,
            animal_id,
            species,
            firearm,
            dirty,
            over_30_months,
            dairy,
            sex,
            recorded_at
        FROM animal_details
        WHERE recorded_at >= ? AND recorded_at < ?
        ORDER BY id
        """,
        (start, end),
    ).fetchall()

    entries = {}
    sequences = []

    for (
        measurement_id,
        animal_id,
        species,
        firearm,
        dirty,
        over_30_months,
        dairy,
        sex,
        recorded_at,
    ) in rows:
        entries[animal_id] = entries.get(animal_id, 0) + 1

        # None means the step wasn't scanned or doesn't apply to the
        # species, which is different from a "no".
        answers = {
            "firearm": yes_no(firearm),
            "dirty": yes_no(dirty),
            "over_30_months": yes_no(over_30_months),
            "dairy": yes_no(dairy),
            "sex": sex,
        }

        steps = scan_sequence.STEPS.get(species, [])

        sequences.append(
            {
                "entry": entries[animal_id],
                "animal_id": animal_id,
                "species": species,
                **answers,
                "missing": [step for step in steps if answers[step] is None],
                "measurement_id": measurement_id,
                "weight": weights.get(measurement_id),
                "recorded_at": recorded_at,
                "in_use": False,
            }
        )

    return sequences


def use_best_sequence(scan):
    """
    Take the animal's details from its most complete scan sequence.
    A rescan is usually a correction, so the newer one wins a tie.
    Every sequence stays in scan["sequences"].
    """
    best = None

    if scan["sequences"]:
        best = max(
            scan["sequences"],
            key=lambda sequence: (-len(sequence["missing"]), sequence["entry"]),
        )
        best["in_use"] = True

    for field in ["species", *scan_sequence.YES_NO_CODES, "sex"]:
        scan[field] = best[field] if best else None


def check_yield(species, live_weight, hang_weights):
    """
    Judge hang total / live weight against the species' band, widened
    by YIELD_SLACK. The verdict is "unknown" when the species or any
    weight is missing.
    """
    band = YIELD_BANDS.get(species)

    if band is None or live_weight is None or None in hang_weights:
        return {"percent": None, "band": band, "plausible": None, "verdict": "unknown"}

    percent = sum(hang_weights) / live_weight * 100
    plausible = (band[0] - YIELD_SLACK, band[1] + YIELD_SLACK)

    return {
        "percent": round(percent, 1),
        "band": band,
        "plausible": plausible,
        "verdict": "ok" if plausible[0] <= percent <= plausible[1] else "warn",
    }


def check_balance(hang_weights):
    """
    Judge how evenly two halves split, as a percent of their mean. The
    verdict is "unknown" when either weight is missing.
    """
    if len(hang_weights) != 2 or None in hang_weights:
        return {"difference_percent": None, "limit": HALF_IMBALANCE_LIMIT, "verdict": "unknown"}

    first, second = hang_weights
    difference = abs(first - second) / ((first + second) / 2) * 100

    return {
        "difference_percent": round(difference, 1),
        "limit": HALF_IMBALANCE_LIMIT,
        "verdict": "ok" if difference <= HALF_IMBALANCE_LIMIT else "warn",
    }


def match(live_scans, hang_scans):
    """
    Pair live scans with hang scans, both in first-scan order.

    Returns the animals, each with its hang scans, its yield and
    balance checks, and a list of QA reasons, plus any hang scans left
    over once every animal has its tags. An animal with fewer hang scans
    than it needs is incomplete, which is normal while its carcass has
    not reached the hang station; its checks stay "unknown" until then.
    """
    animals = []
    hang_index = 0

    for order, live in enumerate(live_scans, start=1):
        qa = []

        tags_needed = HANG_TAGS_PER_SPECIES.get(live["species"])

        if tags_needed is None:
            tags_needed = DEFAULT_HANG_TAGS
            qa.append("no species recorded at live")

        if len(live["weights"]) > 1:
            qa.append(f"live reweighed {len(live['weights'])} times")

        if live["weight"] is None:
            qa.append("live has no valid weight")

        in_use = [sequence for sequence in live["sequences"] if sequence["in_use"]]

        if len(live["sequences"]) > 1:
            qa.append(
                f"live scan sequence entered {len(live['sequences'])} times, "
                f"using entry {in_use[0]['entry']}"
            )

        if in_use and in_use[0]["missing"]:
            qa.append(f"live scan sequence missing {', '.join(in_use[0]['missing'])}")

        hang = hang_scans[hang_index : hang_index + tags_needed]
        hang_index += len(hang)

        for scan in hang:
            if len(scan["weights"]) > 1:
                qa.append(f"hang tag {scan['scan_code']} reweighed {len(scan['weights'])} times")

            if scan["weight"] is None:
                qa.append(f"hang tag {scan['scan_code']} has no valid weight")

        complete = len(hang) == tags_needed
        hang_weights = [scan["weight"] for scan in hang]

        checks = {"yield": None, "balance": None}

        if complete:
            checks["yield"] = check_yield(live["species"], live["weight"], hang_weights)

            if checks["yield"]["verdict"] == "warn":
                low, high = checks["yield"]["plausible"]
                qa.append(
                    f"yield {checks['yield']['percent']}% outside {low:g}-{high:g}% "
                    f"for {live['species']}"
                )

            if tags_needed == 2:
                checks["balance"] = check_balance(hang_weights)

                if checks["balance"]["verdict"] == "warn":
                    qa.append(
                        f"halves differ by {checks['balance']['difference_percent']}% "
                        f"(limit {HALF_IMBALANCE_LIMIT:g}%)"
                    )

        animals.append(
            {
                "order": order,
                "animal_id": live["scan_code"],
                "species": live["species"],
                "firearm": live["firearm"],
                "dirty": live["dirty"],
                "over_30_months": live["over_30_months"],
                "dairy": live["dairy"],
                "sex": live["sex"],
                "live": live,
                "hang": hang,
                "complete": complete,
                "checks": checks,
                "qa": qa,
            }
        )

    return {
        "animals": animals,
        "unmatched_hang": hang_scans[hang_index:],
    }


def match_day(conn, date):
    """
    Match every live and hang scan recorded on an Indiana date (YYYY-MM-DD).
    """
    return match(
        load_scans(conn, "live", date),
        load_scans(conn, "hang", date),
    )
