"""
A day's finished animals laid out like the Weight Slip tab of the
Slaughter Schedule (Alpha) sheet, for the office to copy and paste in.

This is temporary: once LAPIS fills these columns itself
(This-Old-Farm/lapis#9), delete this module and the /weightslip page
(#77).

The page has two copy blocks, so a paste never blanks out the sheet's
Yield (R), Offals (S) or Slaughter Notes (T) columns:

    M  "+30" beef        TRUE or FALSE for beef, blank otherwise
    N  Type              the sex/type code, D for a dairy cow
    O  Live Weight       with the blood estimate for hogs and lambs (#66)
    P  Hanging Weight #1
    Q  Hanging Weight #2 blank for lambs

    U  Quality Issues    FIREARM, DIRTY
    V  Order of Slaughter  the order of the day's live scans

Rows run from the day's lowest animal number to its highest, the same
order as the day's rows in the sheet. A number with no finished animal
gets a blank row so the block still lines up.
"""

import html
import re

import scan_sequence

WEIGHTS_COLUMNS = ['"+30" beef', "Type", "Live Weight", "Hanging Weight #1", "Hanging Weight #2"]
QA_COLUMNS = ["Quality Issues", "Order of Slaughter"]

SPECIES_LETTERS = {species: letter for letter, species in scan_sequence.SPECIES_CODES.items()}


def animal_number(animal_id):
    """The number at the start of an animal ID ("2191B" -> 2191), or None."""
    found = re.match(r"\d+", animal_id or "")
    return int(found.group()) if found else None


def format_weight(weight):
    return f"{weight:g}"


def sex_code(species, sex, dairy):
    """The Weight Slip's Type: the scanned code, or D for a dairy cow."""
    if species == "BEEF" and sex == "Cow" and dairy:
        return "D"

    for code, name in scan_sequence.SEX_CODES.get(species, {}).items():
        if name == sex:
            return code

    return ""


def finished(animal):
    """Whether an animal has its live weight and every hang weight."""
    return (
        animal["complete"]
        and animal["live"]["weight"] is not None
        and all(scan["weight"] is not None for scan in animal["hang"])
    )


def row_for(animal):
    """The Weight Slip cells for one finished animal."""
    species = animal["species"]
    live_weight = animal["live"]["weight"]
    estimate = scan_sequence.estimate_with_blood(species, live_weight)
    hang_weights = [format_weight(scan["weight"]) for scan in animal["hang"]]

    if species == "BEEF" and animal["over_30_months"] is not None:
        over_30 = "TRUE" if animal["over_30_months"] else "FALSE"
    else:
        over_30 = ""

    issues = [name for name, flagged in [("FIREARM", animal["firearm"]), ("DIRTY", animal["dirty"])] if flagged]

    return {
        "weights": [
            over_30,
            sex_code(species, animal["sex"], animal["dairy"]),
            format_weight(estimate if estimate is not None else live_weight),
            *(hang_weights + ["", ""])[:2],
        ],
        "qa": [", ".join(issues), str(animal["order"])],
    }


def weight_slip(match_result):
    """
    The rows for a day's matches (see matching.match_day), one per
    animal number, plus the animals whose ID has no number to place.
    """
    by_number = {}
    unplaced = []

    for animal in match_result["animals"]:
        number = animal_number(animal["animal_id"])

        if number is None:
            unplaced.append(animal["animal_id"])
        else:
            by_number[number] = animal

    rows = []

    if by_number:
        for number in range(min(by_number), max(by_number) + 1):
            animal = by_number.get(number)
            row = {
                "number": number,
                "species": SPECIES_LETTERS.get(animal["species"], "") if animal else "",
                "status": "",
                "weights": [""] * len(WEIGHTS_COLUMNS),
                "qa": [""] * len(QA_COLUMNS),
            }

            if animal is None:
                row["status"] = "not scanned"
            elif not finished(animal):
                row["status"] = "not finished"
            else:
                row.update(row_for(animal))

            rows.append(row)

    return {"rows": rows, "unplaced": unplaced}


def tsv(rows, key):
    return "\n".join("\t".join(row[key]) for row in rows)


def render(date, slip):
    """The /weightslip page."""
    rows = slip["rows"]
    escape = html.escape

    header = "".join(
        f"<th>{escape(name)}</th>"
        for name in ["Animal #", "", *WEIGHTS_COLUMNS, "", *QA_COLUMNS, ""]
    )

    body = "".join(
        "<tr>"
        f"<td>{row['number']}</td><td>{escape(row['species'])}</td>"
        + "".join(f'<td class="copy">{escape(cell)}</td>' for cell in row["weights"])
        + '<td class="gap"></td>'
        + "".join(f'<td class="copy">{escape(cell)}</td>' for cell in row["qa"])
        + f'<td class="secondary-text">{escape(row["status"])}</td>'
        "</tr>"
        for row in rows
    )

    if not rows:
        body = f'<tr><td colspan="11" class="secondary-text">No animals scanned on {escape(date)}.</td></tr>'

    unplaced = ""

    if slip["unplaced"]:
        unplaced = (
            '<p class="secondary-text">Not on this list because the animal ID has no number: '
            f"{escape(', '.join(slip['unplaced']))}</p>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weight Slip {escape(date)}</title>
<style>
  @import url("https://fonts.googleapis.com/css2?family=Jost:wght@400;500;600&display=swap");
  :root {{
    --green: #6f7e67;
    --black: #2c2c2c;
    --brown: #865f50;
    --action: #4b9b4f;
    --action-border: #009b07;
  }}
  body {{ font-family: "Jost", system-ui, sans-serif; color: var(--black); margin: 1em; }}
  .secondary-text {{ color: var(--brown); }}
  button {{
    font: inherit; padding: 0.5em 1em; background: #fff; color: var(--black);
    border: 1px solid var(--black); border-radius: 4px; cursor: pointer;
  }}
  button.primary {{ background: var(--action); border-color: var(--action-border); color: #fff; }}
  form, .actions {{ display: flex; gap: 0.75em; align-items: center; flex-wrap: wrap; margin-bottom: 1em; }}
  table {{ border-collapse: collapse; }}
  th, td {{ padding: 0.25em 0.6em; border-bottom: 1px solid #ddd; text-align: left; white-space: nowrap; }}
  th {{ font-weight: 600; }}
  td.gap {{ border-bottom: none; width: 1.5em; }}
  #copied {{ color: var(--green); }}
  textarea.clipboard {{ position: absolute; left: -9999px; }}
</style>
</head>
<body>
<h1>Weight Slip</h1>
<p class="secondary-text">Temporary until LAPIS fills the Weight Slip. Rows are in animal-number order, like the day's rows in the sheet. Click into the sheet's first row for the day in column M (or U), then paste.</p>
<form method="get" action="/weightslip">
  <label>Slaughter day <input type="date" name="date" value="{escape(date)}"></label>
  <button type="submit">Show</button>
</form>
<div class="actions">
  <button class="primary" onclick="copyBlock('weights')">Copy weights (M to Q)</button>
  <button onclick="copyBlock('qa')">Copy quality issues and order (U to V)</button>
  <span id="copied"></span>
</div>
<textarea class="clipboard" id="weights" readonly>{escape(tsv(rows, "weights"))}</textarea>
<textarea class="clipboard" id="qa" readonly>{escape(tsv(rows, "qa"))}</textarea>
<table>
<thead><tr>{header}</tr></thead>
<tbody>{body}</tbody>
</table>
{unplaced}
<script>
  // navigator.clipboard needs https, and office PCs open this page over
  // plain http, so copy from a selected textarea instead.
  function copyBlock(id) {{
    const block = document.getElementById(id);
    block.select();
    const copied = document.execCommand("copy");
    window.getSelection().removeAllRanges();
    document.getElementById("copied").textContent = copied
      ? "Copied {len(rows)} rows. Paste into the sheet."
      : "Couldn't copy. Select the cells in the table and copy them instead.";
  }}
</script>
</body>
</html>
"""
