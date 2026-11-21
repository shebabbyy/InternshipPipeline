"""Writes the facts part of the project knowledge file 2_Shehab_Profile from master_profile.json, so the two
can never drift apart. Output: output/2_Shehab_Profile.generated.md (paste into the Claude project).
Only stdlib; reads nothing but master_profile.json. Estimated-number bullets (verified: false) are listed
separately and marked as unconfirmed."""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P = json.loads((ROOT / "master_profile.json").read_text(encoding="utf-8"))


def bullets(items, verified):
    return [f"- {b['text']}" for b in items if bool(b.get("verified", True)) == verified]


def main() -> str:
    c, e = P["candidate"], P["education"]
    out = ["# Shehab Moqbel: Profile (knowledge file)",
           f"Generated {date.today():%B %d, %Y} from master_profile.json (the laptop's single source of facts). Do not edit by hand; "
           "change master_profile.json and regenerate (python pipeline/make_profile_doc.py).", "",
           "## Contact",
           f"- Applications, portal accounts, resumes, cover letters: {c['email']}",
           f"- UH / Bauer / Gateway / career-fair roles and alumni outreach: {c.get('email_campus', '')}",
           f"- Phone: {c['phone']} · {c['location']}",
           f"- LinkedIn: {c['linkedin']}",
           f"- Work authorization: {c.get('work_authorization', '')}", "",
           "## Academics",
           f"- {e['school']}: {e['degree']} (declared {e['declared']})",
           f"- {e['class_standing']}; graduating {e['graduation']}; cumulative GPA {e['gpa']}",
           "- Dean's List: " + ", ".join(f"{k} ({v})" for k, v in e["semester_gpas"].items()),
           "- Courses done or in progress: " + "; ".join(e["relevant_coursework"]), "",
           "## Experience (verified bullets only)"]
    for job in P["experience"]:
        out += [f"**{job['organization']}, {job['location']}**" if not job.get("continues") else "",
                f"*{job['title']}* ({job['dates']})"]
        out += bullets(job["bullets"], True)
    unconfirmed = [b for j in P["experience"] for b in j["bullets"] if not b.get("verified", True)]
    if unconfirmed:
        out += ["", "### Not confirmed (estimated numbers; do not use until Shehab confirms)"] + [f"- {b['text']}" for b in unconfirmed]
    out += ["", "## Leadership and activities"]
    for item in P["leadership"]:
        out += [f"**{item['organization']}: {item['title']}** ({item['dates']})"] + bullets(item["bullets"], True)
    out += ["", "## Honors and awards"] + [f"- {h}" for h in P["honors"]]
    s = P["skills"]
    out += ["", "## Skills", "- " + "; ".join(s["languages"]), "- Technical: " + (", ".join(s["technical"]) or "none listed"),
            "- Certifications listed: " + (", ".join(s["certifications"]) or "none"),
            "- Held but not listed: " + (", ".join(s.get("_held_not_listed", [])) or "none")]
    out += ["", "## Never use"] + [f"- {x}" for x in P["never_use"]]
    text = "\n".join(line for i, line in enumerate(out) if line != "" or (i and out[i - 1] != ""))
    dest = ROOT / "output" / "2_Shehab_Profile.generated.md"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(text + "\n", encoding="utf-8")
    return text


if __name__ == "__main__":
    main()
    print("Wrote output/2_Shehab_Profile.generated.md")
