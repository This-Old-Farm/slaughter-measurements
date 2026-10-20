"""
Match live station animals to hang station tags.

This is a prototype that may move to lapis, so it only reads the
measurements and animal_details tables and nothing else in this
repository depends on how it works.

Animals are matched to hang tags by order: the nth animal scanned at
the live station owns the next hang tags in the order they were first
scanned, two halves for BEEF and PORK and one carcass for LAMB.

Every weight stays in the database. Matching uses the most recent
weight for each code and flags anything weighed more than once for QA.
"""

HANG_TAGS_PER_SPECIES = {
    "BEEF": 2,
    "PORK": 2,
    "LAMB": 1,
}

# Used when a live scan has no animal_details row, so the rest of the
# day still lines up as well as it can. The animal is flagged for QA.
DEFAULT_HANG_TAGS = 2


def load_scans(conn, station, date):
    """
    Return one dict per scan code weighed at a station on a date
    (YYYY-MM-DD), in the order each code was first scanned.

    Measurements without a scan code were recorded before scan codes
    existed and cannot be matched, so they are left out.
    """
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
        WHERE m.station = ? AND m.scan_code != '' AND DATE(m.recorded_at) = ?
        ORDER BY m.id
        """,
        (station, date),
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

        # Rows arrive oldest first, so the last one wins.
        scan["weights"].append(weight)
        scan["weight"] = weight
        scan["measurement_id"] = measurement_id
        scan["recorded_at"] = recorded_at

        if station == "live":
            scan["species"] = species
            scan["sex"] = sex
            scan["over_30_months"] = bool(over_30_months)

    # dicts keep insertion order, which is first-scan order.
    return list(scans.values())


def match(live_scans, hang_scans):
    """
    Pair live scans with hang scans, both in first-scan order.

    Returns the animals, each with its hang scans and a list of QA
    reasons, plus any hang scans left over once every animal has its
    tags. An animal with fewer hang scans than it needs is incomplete,
    which is normal while its carcass has not reached the hang station.
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

        hang = hang_scans[hang_index : hang_index + tags_needed]
        hang_index += len(hang)

        for scan in hang:
            if len(scan["weights"]) > 1:
                qa.append(f"hang tag {scan['scan_code']} reweighed {len(scan['weights'])} times")

        animals.append(
            {
                "order": order,
                "animal_id": live["scan_code"],
                "species": live["species"],
                "sex": live["sex"],
                "over_30_months": live["over_30_months"],
                "live": live,
                "hang": hang,
                "complete": len(hang) == tags_needed,
                "qa": qa,
            }
        )

    return {
        "animals": animals,
        "unmatched_hang": hang_scans[hang_index:],
    }


def match_day(conn, date):
    """
    Match every live and hang scan recorded on a date (YYYY-MM-DD).
    """
    return match(
        load_scans(conn, "live", date),
        load_scans(conn, "hang", date),
    )
