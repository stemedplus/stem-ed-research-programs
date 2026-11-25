#!/usr/bin/env python3
"""Validate every program file and build the README table and the website data.

    python scripts/build.py           # validate, then write README.md table, site/programs.json, site/feeds/new.xml
    python scripts/build.py --check   # validate and fail if README.md is out of date
    python scripts/build.py --validate  # only check the program files and other-lists.yml
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = ROOT / "programs"
SCHEMA = json.loads((ROOT / "schema" / "program.schema.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stemedplus_disciplines as shared  # noqa: E402  (the list shared across STEMEd+ Commons)

DISCIPLINE_FILE = ROOT / "schema" / "stemedplus_disciplines.json"
DISCIPLINES = shared.labels_by_id(DISCIPLINE_FILE)
ISSUE_FORM = ROOT / ".github" / "ISSUE_TEMPLATE" / "add_program.yml"
README = ROOT / "README.md"
SITE_DATA = ROOT / "site" / "programs.json"
FEED = ROOT / "site" / "feeds" / "new.xml"
SITE_URL = "https://stemedplus.github.io/stem-ed-research-programs/"
FEED_SIZE = 50
START, END = "<!-- programs:start -->", "<!-- programs:end -->"
OTHER_START, OTHER_END = "<!-- other-lists:start -->", "<!-- other-lists:end -->"
OTHER_LISTS = ROOT / "other-lists.yml"


def _plain(value):
    """YAML reads dates as date objects; the schema expects strings."""
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def load_programs() -> tuple[list[dict], list[str]]:
    validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
    programs, errors = [], []
    for path in sorted(PROGRAMS.glob("*.yml")):
        name = path.relative_to(ROOT).as_posix()
        try:
            record = _plain(yaml.safe_load(path.read_text(encoding="utf-8")))
        except yaml.YAMLError as exc:
            errors.append(f"{name}: not valid YAML ({exc})")
            continue
        if not isinstance(record, dict):
            errors.append(f"{name}: expected a mapping of fields")
            continue
        for err in sorted(validator.iter_errors(record), key=lambda e: list(e.path)):
            where = ".".join(str(p) for p in err.path) or "(top level)"
            errors.append(f"{name}: {where}: {err.message}")
        if record.get("id") != path.stem:
            errors.append(f"{name}: id must match the file name ({path.stem!r})")
        for d in record.get("disciplines") or []:
            if d not in DISCIPLINES:
                errors.append(f"{name}: unknown discipline {d!r}; use one of {sorted(DISCIPLINES)} "
                              "(the STEMEd+ disciplines in schema/stemedplus_disciplines.json, kept in stemedplus-theme)")
        programs.append(record)
    errors.extend(issue_form_errors())
    seen: dict[tuple, str] = {}
    for r in programs:
        key = ((r.get("institution") or {}).get("name"), (r.get("home_department") or "").lower(),
               tuple(r.get("disciplines") or []))
        if key in seen:
            errors.append(f"programs/{r.get('id')}.yml duplicates programs/{seen[key]}.yml")
        seen[key] = r.get("id")
    return programs, errors


def issue_form_errors() -> list[str]:
    """The "Add a program" form must offer every STEMEd+ discipline."""
    form = yaml.safe_load(ISSUE_FORM.read_text(encoding="utf-8"))
    options = next((f["attributes"]["options"] for f in form.get("body", []) if f.get("id") == "disciplines"), [])
    offered = {str(o).split(" (")[0] for o in options}
    return [f"{ISSUE_FORM.relative_to(ROOT).as_posix()}: the Discipline list is missing {label!r}"
            for label in DISCIPLINES.values() if label not in offered]


def load_other_lists() -> tuple[list[dict], list[str]]:
    entries = yaml.safe_load(OTHER_LISTS.read_text(encoding="utf-8")) or []
    errors = []
    for i, e in enumerate(entries, 1):
        has_list = all(e.get(k) for k in ("title", "url", "maintainer"))
        if not e.get("field") or not (has_list or e.get("note")):
            errors.append(f"other-lists.yml entry {i}: needs a field, and either title, url and maintainer, or a note")
        elif has_list and not str(e["url"]).startswith(("http://", "https://")):
            errors.append(f"other-lists.yml entry {i}: url must start with http:// or https://")
        for d in e.get("disciplines") or []:
            if d not in DISCIPLINES:
                errors.append(f"other-lists.yml entry {i}: unknown discipline {d!r}")
    return entries, errors


def other_lists_table(entries: list[dict]) -> str:
    rows = ["| Field | List | Maintained by |", "|---|---|---|"]
    for e in entries:
        if e.get("url"):
            by = f"Created by {e['created_by']}; maintained by {e['maintainer']}" if e.get("created_by") else e["maintainer"]
            rows.append(f"| {_md(e['field'])} | [{_md(e['title'])}]({e['url']}) | {_md(by)} |")
        else:
            rows.append(f"| {_md(e['field'])} | {_md(' '.join(e['note'].split()))} | |")
    return f"{OTHER_START}\n_Generated from `other-lists.yml` by `scripts/build.py`._\n\n" + "\n".join(rows) + f"\n{OTHER_END}"


def _md(text: str) -> str:
    return text.replace("|", "\\|")


def readme_table(programs: list[dict]) -> str:
    rows = ["| Institution | Home department | Degrees | Discipline | Program faculty |",
            "|---|---|---|---|---|"]
    for r in sorted(programs, key=lambda r: (r["institution"]["name"], r["id"])):
        inst = _md(r["institution"]["name"])
        if r.get("url"):
            inst = f"[{inst}]({r['url']})"
        inst += f"<br><sub>{_md(r['institution']['country'])}</sub>"
        if r.get("closed_to_new_students"):
            inst += "<br><sub>Closed to new students</sub>"
        dept = _md(r["home_department"]) + (" ⚠️" if "home_department" in r.get("needs_check", []) else "")
        degrees = ", ".join(r["degree_types"]) + (" ⚠️" if "degree_types" in r.get("needs_check", []) else "")
        disc = ", ".join(DISCIPLINES[d] for d in r["disciplines"])
        faculty = ", ".join(_md(f["name"]) for f in r["faculty"])
        rows.append(f"| {inst} | {dept} | {degrees} | {disc} | {faculty} |")
    count = f"{len(programs)} program{'s' if len(programs) != 1 else ''}."
    note = " ⚠️ marks details still to be confirmed with the program." if any(
        r.get("needs_check") for r in programs) else ""
    return f"{START}\n_Generated from `programs/` by `scripts/build.py`; edit the program files, not this table. {count}{note}_\n\n" \
           + "\n".join(rows) + f"\n{END}"


def _replace_between(text: str, start: str, end: str, new: str) -> str:
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    return before + new + after


def updated_readme(programs: list[dict], other_lists: list[dict]) -> str:
    text = README.read_text(encoding="utf-8")
    text = _replace_between(text, START, END, readme_table(programs))
    return _replace_between(text, OTHER_START, OTHER_END, other_lists_table(other_lists))


def site_data(programs: list[dict], other_lists: list[dict]) -> dict:
    return {
        "generated": dt.date.today().isoformat(),
        "disciplines": DISCIPLINES,
        "other_lists": [{**e, "note": " ".join(e["note"].split())} if e.get("note") else e for e in other_lists],
        "programs": sorted(programs, key=lambda r: (r["institution"]["name"], r["id"])),
    }


def added_dates() -> dict[str, str]:
    """Program id -> the day its file was first added (from git history). Empty when git can't say,
    for example in a shallow checkout; the feed then falls back to last_verified."""
    try:
        if subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip() == "true":
            return {}
        log = subprocess.run(["git", "log", "--diff-filter=A", "--name-only", "--format=%x00%as", "--", "programs/"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return {}
    dates, day = {}, ""
    for line in log.splitlines():
        if line.startswith("\x00"):
            day = line[1:]
        elif line.startswith("programs/") and line.endswith(".yml"):
            dates[Path(line).stem] = day  # newest first, so the last date seen is the first time it was added
    return dates


def new_programs_rss(programs: list[dict], added: dict[str, str]) -> str:
    """site/feeds/new.xml: the most recently added programs, newest first."""
    def when(r):
        return added.get(r["id"]) or str(r.get("last_verified") or "")
    items = []
    for r in sorted(programs, key=lambda r: (when(r), r["id"]), reverse=True)[:FEED_SIZE]:
        inst = r["institution"]["name"]
        fields = ", ".join(DISCIPLINES.get(d, d) for d in r.get("disciplines") or [])
        title = f"{inst}: {fields}" if fields else inst
        parts = [r.get("home_department") or ""]
        if r.get("degree_types"):
            parts.append("Degrees: " + ", ".join(r["degree_types"]))
        if r.get("faculty"):
            parts.append("Faculty: " + ", ".join(f["name"] for f in r["faculty"] if f.get("name")))
        if r.get("closed_to_new_students"):
            parts.append("Not taking new students.")
        day = when(r)
        date = ""
        if day:
            stamp = dt.datetime.strptime(day[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
            date = f"<pubDate>{format_datetime(stamp)}</pubDate>"
        items.append(f"<item><title>{escape(title)}</title><link>{escape(r['url'])}</link>"
                     f'<guid isPermaLink="false">{escape(SITE_URL + "#" + r["id"])}</guid>{date}'
                     + "".join(f"<category>{escape(DISCIPLINES.get(d, d))}</category>" for d in r.get("disciplines") or [])
                     + f"<description>{escape('. '.join(x for x in parts if x))}</description></item>")
    return ('<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
            "<title>STEM+ Education Research Graduate Programs: new programs</title>"
            f"<link>{SITE_URL}</link>"
            f'<atom:link href="{SITE_URL}feeds/new.xml" rel="self" type="application/rss+xml"/>'
            f"<description>The {FEED_SIZE} programs and research groups most recently added to the list, newest first.</description>"
            "<language>en</language>" + "".join(items) + "</channel></rss>\n")


def main() -> int:
    programs, errors = load_programs()
    other_lists, other_errors = load_other_lists()
    errors += other_errors
    if errors:
        print(f"{len(errors)} problem(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    if "--validate" in sys.argv:
        print(f"OK: {len(programs)} programs valid.")
        return 0
    new_readme = updated_readme(programs, other_lists)
    if "--check" in sys.argv:
        if new_readme != README.read_text(encoding="utf-8"):
            print("README.md tables are out of date: run python scripts/build.py and commit the result.")
            return 1
        print(f"OK: {len(programs)} programs valid; README.md is up to date.")
        return 0
    README.write_text(new_readme, encoding="utf-8")
    SITE_DATA.parent.mkdir(exist_ok=True)
    SITE_DATA.write_text(json.dumps(site_data(programs, other_lists), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    added = added_dates()
    FEED.parent.mkdir(exist_ok=True)
    FEED.write_text(new_programs_rss(programs, added), encoding="utf-8")
    print(f"OK: {len(programs)} programs valid; wrote README.md tables, {SITE_DATA.relative_to(ROOT)}, and "
          f"{FEED.relative_to(ROOT)} (dates from {'git history' if added else 'last_verified: no full git history here'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
