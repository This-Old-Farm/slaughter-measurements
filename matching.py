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

Each complete animal also gets two plausibility checks, carried over
from the matching GUI prototype in This-Old-Farm/lapis#23, so a wrong
pairing is visible rather than silently accepted:

- yield: hang total / live weight, against the species' dressing band
- balance: how evenly two halves split, since halves of one carcass
  weigh close to the same
"""

import local_time

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


def load_scans(conn, station, date):
    """
    Return one dict per scan code weighed at a station on an Indiana
    date (YYYY-MM-DD), in the order each code was first scanned.

    Measurements without a scan code were recorded before scan codes
    existed and cannot be matched, so they are left out.
    """
    start, end = local_time.utc_bounds(date)

    rows = conn.execute(
        """
        SELECT
            m.id,
            m.scan_code,
            m.weight,
            m.recorded_at,
            d.species,
            d.sex,
            d.over_30_months
        FROM measurements m
        LEFT JOIN animal_details d ON d.measurement_id = m.id
        WHERE m.station = ? AND m.scan_code != ''
            AND m.recorded_at >= ? AND m.recorded_at < ?
        ORDER BY m.id
        """,
        (station, start, end),
    ).fetchall()

    scans = {}

    for (
        measurement_id,
        scan_code,
        weight,
        recorded_at,
        species,
        sex,
        over_30_months,
    ) in rows:
        scan = scans.setdefault(
            scan_code,
            {
                "scan_code": scan_code,
                "weights": [],
            },
        )

        # Rows arrive oldest first, so the last valid one wins. A
        # failed capture stays in the history but never becomes the
        # weight; a code with only failed captures keeps its place in
        # the order with no weight.
        scan["weights"].append(weight)
        scan.setdefault("weight", None)

        if not capture_failed(weight):
            scan["weight"] = weight
            scan["measurement_id"] = measurement_id
            scan["recorded_at"] = recorded_at

        if station == "live":
            scan["species"] = species
            scan["sex"] = sex
            scan["over_30_months"] = bool(over_30_months)

    # dicts keep insertion order, which is first-scan order.
    return list(scans.values())


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
                "sex": live["sex"],
                "over_30_months": live["over_30_months"],
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
