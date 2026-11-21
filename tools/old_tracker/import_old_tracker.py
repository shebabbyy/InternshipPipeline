"""One-time import (Nov 19, 2026): old Excel tracker history -> Google Sheet "Shehab Internship Tracker".
- outreach_history.csv -> Outreach tab (all Status "Closed": history only, no follow-up reminders)
- applications_history.csv -> new tab "Old Applications (Aug 2026)" (the pipeline never reads or writes it)
Safe to run twice: rows already in the Sheet are skipped. Never sends or submits anything."""
import csv, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "pipeline"))
from common import spreadsheet, worksheet  # noqa: E402

def load(name):
    with open(HERE / name, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]

def push(sh, tab, file, key):
    header, rows = load(file)
    ws = worksheet(sh, tab, header)
    have = {key(r) for r in ws.get_all_values()[1:]}
    new = [r for r in rows if key(r) not in have]
    if new:
        ws.append_rows(new, value_input_option="RAW", table_range="A1")
    print(f"{tab}: added {len(new)}, skipped {len(rows) - len(new)} already there")

sh = spreadsheet()
if sh is None:
    sys.exit("Google Sheet not reachable (see message above). Nothing changed.")
push(sh, "Outreach", "outreach_history.csv", lambda r: (r[1].strip().lower(), r[0][:10]) if len(r) > 1 else None)
push(sh, "Old Applications (Aug 2026)", "applications_history.csv", lambda r: (r[0].strip().lower(), r[1].strip().lower()) if len(r) > 1 else None)
print("Done. Restart the Control Center to see the history on the Network tab.")
