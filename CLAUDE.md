# F1 25 Dashboard

Start with `README.md` (what the project is, layout, how to run). Race review rules: `docs/RACE_REVIEW.md`.
Skills: `.claude/skills/race-review` (race review page), `.claude/skills/lap-video` (best lap video).

Reply in the user's language. Code, comments and docs in the repository are in English.

`local/` is git-ignored and holds personal files that are not part of the project. If `local/CLAUDE.md` exists, read it too.

Tests: `py -3 tools/test_bridge.py` and `py -3 tools/test_race_report.py` (or `run/test_all.bat`).
