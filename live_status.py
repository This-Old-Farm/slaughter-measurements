"""
What the live-station pages show: the animal being scanned now and
today's live animals (#62).

The current animal is the most recent scan sequence. Its answers are
saved as they are scanned, so this shows what is queued for it and
which scan comes next.
"""

import matching
import scan_sequence


def current_animal(animal, sequence):
    steps = []

    for step in scan_sequence.STEPS[sequence["species"]]:
        codes = scan_sequence.step_codes(sequence["species"], step)

        steps.append(
            {
                "step": step,
                "label": scan_sequence.STEP_LABELS[step],
                "answer": sequence[step],
                "codes": [{"code": code, "value": value} for code, value in codes.items()],
            }
        )

    weight = sequence["weight"]

    return {
        "animal_id": sequence["animal_id"],
        "species": sequence["species"],
        "order": animal["order"],
        "entry": sequence["entry"],
        "entries": len(animal["live"]["sequences"]),
        "weight": None if weight is None or matching.capture_failed(weight) else weight,
        # A reading with no carcass in it, so the weight needs a rescan.
        "weight_failed": weight is not None and matching.capture_failed(weight),
        "steps": steps,
        "next_step": sequence["missing"][0] if sequence["missing"] else None,
    }


def live_status(conn, date):
    """The live-station view of an Indiana date (YYYY-MM-DD)."""
    animals = matching.match_day(conn, date)["animals"]

    latest = max(
        (
            (sequence["id"], animal, sequence)
            for animal in animals
            for sequence in animal["live"]["sequences"]
        ),
        key=lambda item: item[0],
        default=None,
    )

    return {
        "current": current_animal(latest[1], latest[2]) if latest else None,
        # The columns for the animals table, in scan order.
        "steps": [
            {"step": step, "label": label} for step, label in scan_sequence.STEP_LABELS.items()
        ],
        "animals": [
            {
                "order": animal["order"],
                "animal_id": animal["animal_id"],
                "species": animal["species"],
                "weight": animal["live"]["weight"],
                # Only the steps that apply to the species, so a missing
                # key means "doesn't apply" and None means "not scanned".
                "answers": {
                    step: animal[step] for step in scan_sequence.STEPS.get(animal["species"], [])
                },
                # Hang-station flags belong to another screen, and on
                # the live station's own screen "live " goes without
                # saying.
                "qa": [
                    reason.removeprefix("live ")
                    for reason in animal["qa"]
                    if reason.startswith("live ")
                ],
            }
            for animal in animals
        ],
    }
