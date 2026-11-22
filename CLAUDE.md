# F1 25 Dashboard

Start with `README.md` (what the project is, layout, how to run). Race review rules: `docs/RACE_REVIEW.md`, setup rules: `docs/RACE_SETUP.md`.
Skills: `.claude/skills/race-review` (race review page), `.claude/skills/race-setup` (setup sheet for coming sessions),
`.claude/skills/lap-video` (best lap video).

Reply in the user's language. Everything in the repository is in English: user interface text (the web dashboard,
console messages, command line help), code comments, docstrings, tests, docs and commit messages.

`local/` is git-ignored and holds personal files that are not part of the project. If `local/CLAUDE.md` exists, read it too.

Tests: `py -3 tools/test_bridge.py`, `py -3 tools/test_race_report.py` and `py -3 tools/test_web.py` (or `run/test_all.bat`).
