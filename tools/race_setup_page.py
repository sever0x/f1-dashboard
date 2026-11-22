"""Setup sheet page: car setups for the coming sessions, built from setup.json.

    py -3 tools/race_setup_page.py setup.json -o race_setup.html

setup.json (all text in the user's language, `lang` picks tools/race_setup_labels/<lang>.json, English by default):

{
  "lang": "ru", "page_title": "Shanghai setup", "eyebrow": "...", "title": "...", "lead": "...",
  "base": {"label": "Melbourne race, Mercedes", "setup": {<every field of race_report.SETUP_FIELDS>}},
  "facts_title": "...", "facts": [{"title": "...", "text": "...", "tone": "on|ok|"}], "facts_text": "...",
  "steps": ["..."],
  "sections": [{"title": "...", "text": "...", "sheets": [
      {"kind": "wet|dry|mix", "tag": "...", "title": "...", "when": "...",
       "setup": {<fields that differ from the base; a value may be a string such as "6-8 kg">},
       "fuel_calc": {"per_lap": 1.8, "margin": 1.0, "laps": 29},
       "why": ["..."]}]}],
  "checklist": [{"eyebrow": "...", "title": "...", "items": ["..."]}],
  "sources": ["..."]
}

A field missing from a sheet's setup takes the base value; a value that differs from the base is highlighted.
`pressure_front` and `pressure_rear` set both tyres of an axle. Text may use **bold**.
"""
from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from race_report import SETUP_FIELDS  # noqa: E402

TEMPLATE = HERE / "race_setup_template.html"
LABELS = HERE / "race_setup_labels"
# (group, [(row label key, fields, decimals, unit key)])
ROWS = [
    ("aero", [("wings", ("front_wing", "rear_wing"), 0, None)]),
    ("transmission", [("diff_on_throttle", ("diff_on_throttle",), 0, None), ("diff_off_throttle", ("diff_off_throttle",), 0, None)]),
    ("geometry", [("camber", ("front_camber", "rear_camber"), 2, None), ("toe", ("front_toe", "rear_toe"), 2, None)]),
    ("suspension", [("suspension", ("front_suspension", "rear_suspension"), 0, None),
                    ("anti_roll_bar", ("front_anti_roll_bar", "rear_anti_roll_bar"), 0, None),
                    ("ride_height", ("front_ride_height", "rear_ride_height"), 0, None)]),
    ("brakes", [("brake_pressure", ("brake_pressure",), 0, None), ("brake_bias", ("brake_bias",), 0, None),
                ("engine_braking", ("engine_braking",), 0, None)]),
    ("tyres", [("pressure_front", ("pressure_fl",), 1, "psi"), ("pressure_rear", ("pressure_rl",), 1, "psi")]),
    ("fuel", [("fuel", ("fuel",), 1, "kg")]),
]


def text(value: str) -> str:
    """Escaped text with **bold**."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(str(value)))


def expand(setup: dict) -> dict:
    out = dict(setup)
    for axle, wheels in (("pressure_front", ("pressure_fl", "pressure_fr")), ("pressure_rear", ("pressure_rl", "pressure_rr"))):
        if axle in out:
            for wheel in wheels:
                out[wheel] = out[axle]
            del out[axle]
    unknown = set(out) - set(SETUP_FIELDS)
    if unknown:
        raise SystemExit(f"Unknown setup fields: {', '.join(sorted(unknown))}. Known: {', '.join(SETUP_FIELDS)}")
    return out


def number(value, digits: int, decimal: str) -> str:
    if isinstance(value, str):
        return value
    formatted = f"{value:.{digits}f}"
    if formatted.startswith("-"):
        formatted = "−" + formatted[1:]
    return formatted.replace(".", decimal)


def cell(setup: dict, fields, digits: int, unit, labels: dict) -> str:
    values = [setup.get(field) for field in fields]
    if any(isinstance(value, str) for value in values):
        return " / ".join(str(value) for value in values if value is not None)
    joined = " / ".join(number(value, digits, labels["decimal"]) for value in values)
    return f"{joined} {labels[unit]}" if unit else joined


def render_sheet(sheet: dict, base: dict, labels: dict) -> str:
    setup = {**base, **expand(sheet.get("setup", {}))}
    rows = []
    for group, items in ROWS:
        rows.append(f'<tr class="grp"><td colspan="2">{text(labels["groups"][group])}</td></tr>')
        for key, fields, digits, unit in items:
            if key == "fuel" and sheet.get("fuel_calc") and "fuel" not in sheet.get("setup", {}):
                calc = sheet["fuel_calc"]
                value = f"× {number(calc['per_lap'], 1, labels['decimal'])} + {number(calc['margin'], 0, labels['decimal'])} {labels['kg']}"
                changed = True
            else:
                value = cell(setup, fields, digits, unit, labels)
                changed = value != cell(base, fields, digits, unit, labels)
            css = ' class="chg"' if changed else ""
            rows.append(f'<tr{css}><td>{text(labels["rows"][key])}</td><td>{text(value)}</td></tr>')
    parts = [f'<article class="sheet {html.escape(sheet.get("kind", "dry"))}">',
             f'<div class="top"><span class="tag">{text(sheet.get("tag", ""))}</span><h3>{text(sheet.get("title", ""))}</h3>'
             f'<span class="when">{text(sheet.get("when", ""))}</span></div>',
             "<table>" + "".join(rows) + "</table>"]
    calc = sheet.get("fuel_calc")
    if calc:
        ident = re.sub(r"\W+", "-", sheet.get("title", "laps")).strip("-").lower() or "laps"
        parts.append(f'<div class="fuel" data-per-lap="{calc["per_lap"]}" data-margin="{calc["margin"]}" data-unit="{html.escape(labels["kg"])}" '
                     f'data-decimal="{html.escape(labels["decimal"])}"><label for="laps-{ident}">{text(labels["fuel_laps"])}</label>'
                     f'<input id="laps-{ident}" type="number" min="1" max="100" value="{int(calc.get("laps", 29))}" inputmode="numeric">'
                     f'<span>{text(labels["fuel_word"])}</span> <output for="laps-{ident}">—</output></div>')
    if sheet.get("why"):
        parts.append('<div class="why"><ul>' + "".join(f"<li>{text(item)}</li>" for item in sheet["why"]) + "</ul></div>")
    parts.append("</article>")
    return "".join(parts)


def render(data: dict, labels: dict) -> str:
    base = expand(data["base"]["setup"])
    missing = [field for field in SETUP_FIELDS if field not in base]
    if missing:
        raise SystemExit(f"The base setup misses: {', '.join(missing)}")
    body = ['<header>', f'<span class="eyebrow">{text(data.get("eyebrow", ""))}</span>', f'<h1>{text(data.get("title", ""))}</h1>']
    if data.get("lead"):
        body.append(f'<p class="lead">{text(data["lead"])}</p>')
    body.append("</header>")
    if data.get("facts") or data.get("steps"):
        body.append('<section class="panel">')
        if data.get("facts_title"):
            body.append(f'<h2>{text(data["facts_title"])}</h2>')
        if data.get("facts"):
            body.append('<div class="facts">' + "".join(
                f'<div class="fact {html.escape(fact.get("tone", ""))}"><b>{text(fact["title"])}</b><span>{text(fact["text"])}</span></div>'
                for fact in data["facts"]) + "</div>")
        if data.get("facts_text"):
            body.append(f'<p>{text(data["facts_text"])}</p>')
        if data.get("steps"):
            body.append(f'<h3>{text(labels["steps"])}</h3><ol class="flow">' +
                        "".join(f"<li><span>{text(step)}</span></li>" for step in data["steps"]) + "</ol>")
        body.append("</section>")
    body.append(f'<div class="legend"><i></i> {text(labels["changed"])}: {text(labels["base_prefix"])} — {text(data["base"].get("label", ""))}</div>')
    for section in data.get("sections", []):
        if section.get("title") or section.get("text"):
            body.append('<section class="panel">' + (f'<h2>{text(section["title"])}</h2>' if section.get("title") else "") +
                        (f'<p>{text(section["text"])}</p>' if section.get("text") else "") + "</section>")
        body.append('<section class="sheets">' + "".join(render_sheet(sheet, base, labels) for sheet in section.get("sheets", [])) + "</section>")
    if data.get("checklist"):
        body.append('<section class="cols">' + "".join(
            f'<div class="box"><span class="eyebrow">{text(box.get("eyebrow", ""))}</span><h3>{text(box["title"])}</h3><ul>' +
            "".join(f"<li>{text(item)}</li>" for item in box.get("items", [])) + "</ul></div>" for box in data["checklist"]) + "</section>")
    if data.get("sources"):
        body.append(f'<footer class="src"><span class="eyebrow">{text(labels["sources"])}</span><ul>' +
                    "".join(f"<li>{text(item)}</li>" for item in data["sources"]) + "</ul></footer>")
    page = TEMPLATE.read_text(encoding="utf-8")
    return page.replace("{{TITLE}}", html.escape(data.get("page_title", "Setup"))).replace("{{BODY}}", "\n".join(body))


def load_labels(lang: str) -> dict:
    labels = json.loads((LABELS / "en.json").read_text(encoding="utf-8"))
    own = LABELS / f"{lang}.json"
    if own.exists():
        local = json.loads(own.read_text(encoding="utf-8"))
        for key, value in local.items():
            labels[key] = {**labels[key], **value} if isinstance(value, dict) else value
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup sheet page from setup.json")
    parser.add_argument("setup", help="setup.json")
    parser.add_argument("-o", "--out", required=True, help="output .html")
    args = parser.parse_args()
    data = json.loads(pathlib.Path(args.setup).read_text(encoding="utf-8"))
    labels = load_labels(data.get("lang", "en"))
    labels.update({k: v for k, v in data.get("labels", {}).items() if not isinstance(v, dict)})
    pathlib.Path(args.out).write_text(render(data, labels), encoding="utf-8")
    print(f"Done: {args.out}")


if __name__ == "__main__":
    main()
