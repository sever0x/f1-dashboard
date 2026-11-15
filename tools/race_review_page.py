"""Builds the review page: template + data (race_review_data.py) + review text (review.json).

    py -3 tools/race_review_page.py data.json review.json -o race_review.html

The review.json format is described in .claude/skills/race-review/SKILL.md. Template: tools/race_review_template.html.

Page labels: English defaults from tools/race_review_labels/en.json, overlaid by the file for review.json "lang"
(for example ru.json) if it exists, then by review.json "labels" for any other language.
"""
from __future__ import annotations

import argparse
import html
import json
import pathlib

TOOLS = pathlib.Path(__file__).resolve().parent
TEMPLATE = TOOLS / "race_review_template.html"
LABELS = TOOLS / "race_review_labels"


def embed(obj) -> str:
    # JSON inside <script>: "</" would close the tag too early
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def labels(review: dict) -> dict:
    result = json.loads((LABELS / "en.json").read_text(encoding="utf-8"))
    lang = str(review.get("lang") or "en").lower()
    translated = LABELS / f"{lang}.json"
    if lang != "en" and translated.exists():
        result.update(json.loads(translated.read_text(encoding="utf-8")))
    result.update(review.get("labels") or {})
    return result


def main():
    parser = argparse.ArgumentParser(description="Race review page")
    parser.add_argument("data", nargs="?")
    parser.add_argument("review", nargs="?")
    parser.add_argument("-o", "--out")
    parser.add_argument("--labels", action="store_true", help="print the English page labels to translate into review.json")
    args = parser.parse_args()
    if args.labels:
        print((LABELS / "en.json").read_text(encoding="utf-8"))
        return
    if not (args.data and args.review and args.out):
        parser.error("data, review and -o are required")
    data = json.loads(pathlib.Path(args.data).read_text(encoding="utf-8"))
    review = json.loads(pathlib.Path(args.review).read_text(encoding="utf-8"))
    text = labels(review)
    page = TEMPLATE.read_text(encoding="utf-8")
    title = review.get("page_title") or text["page_title"].replace("{track}", data["meta"]["track"])
    page = page.replace("__TITLE__", html.escape(title))
    page = page.replace("__DATA__", embed(data)).replace("__REVIEW__", embed(review)).replace("__LABELS__", embed(text))
    pathlib.Path(args.out).write_text(page, encoding="utf-8")
    print(f"Done: {args.out}")


if __name__ == "__main__":
    main()
