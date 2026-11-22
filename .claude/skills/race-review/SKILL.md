---
name: race-review
description: Review of an F1 25 race from a telemetry recording, as an artifact page in a fixed format. Use when the user asks (in any language) to review, analyse or critique a race ("review my race", "I recorded a race", "what did I do wrong", «разбери гонку», «я записал гонку», «что я делал не так»), or sends a .f1rec.gz recording.
---

# Race review

Reply in the user's language, and write all review text in that language too. Goal: the user learns. Every conclusion is backed by a number from the data, an assumption is labelled as an assumption.
Critique rules and what to check in the data: `docs/RACE_REVIEW.md`, read it first.

## Steps

1. Report: `py -3 tools/race_report.py` (another recording: path as a file, list: `--list`). Read `races/reports/<recording>/report.md`
   in full, starting with "Data quality".
2. Look up facts that are not in the report in the raw data with a script in the scratchpad, not in your head:
   `race_report.load(path)` → `race.trace` (columns `PLAYER_COLUMNS`), `race.history`, `race.final`, `race.events`, `race.teams`.
   Be sure to look at:
   - the teammate: cars with the same `race.teams[car]`. This is the only fair comparison in the same car;
   - damage `wing_fl/wing_fr/wing_r/floor` by lap: was the pit stop forced;
   - every one-off loss over 3 s: telemetry 100–300 m around it (speed, throttle, brake, steering, slip, gear,
     `off_wheels`, `gap_ms`) against the best clean lap. Find the moment where the lap differs and what the driver did with their hands;
   - the deficit to the car ahead at the finish and to P10 by total lap time from `race.history`: how many places the mistakes cost;
   - the fights: series of back-and-forth overtakes in "Events", where who overtook, ERS charge on the straights.
   - the setup (rules: `docs/RACE_SETUP.md`): the report's "Setup" section and `py -3 tools/setup_history.py --track <track>`.
     Compare the main setup with the previous sessions at this track and with the user's base (bold = changed); check the
     changes during the session (differential and brake bias on the move, front wing at a pit stop) before comparing laps;
     top speed against the rivals in qualifying and on clean laps; tyre temperatures. Before calling a loss a driving
     mistake, compare the best lap with a rival's best lap of similar pace every 200 m (`race.rival_best[car]["trace"]`):
     equal corners with slower straights or exits point to the setup, slower corners to the driving.
3. Page data, with telemetry windows for the main episodes (window number = order of `--window`):
   `py -3 tools/race_review_data.py --window "7:4180:4400:Lap 7, spin" -o <scratchpad>/data.json`
   (write window labels in the user's language).
4. Write the review text into `<scratchpad>/review.json` following the form below.
5. Page: `py -3 tools/race_review_page.py data.json review.json -o <scratchpad>/race_review.html`.
   Check the syntax of the page script (`node --check` on the extracted `<script>`), publish via Artifact.
   Publish each new race as a separate artifact (a new file path, for example `race_review_<track>_<date>.html`).
6. Copy `data.json`, `review.json` and the html into `races/reports/<recording>/` so that races can be compared.
7. In the reply to the user: the link and 3–4 lines of the main points. Don't retell the whole page.
   If the setup explains part of the loss, say so and offer the race-setup skill for the next sessions.

## Page language

Set `lang` in review.json to the user's language code (`en`, `ru`, ...). The page's fixed labels (section titles, table headers,
tooltips) come from `tools/race_review_labels/<lang>.json`. If there is no file for that language, print the English labels with
`py -3 tools/race_review_page.py --labels`, translate the values and put them into review.json as `labels` (keep the keys and
the `{placeholders}`). For English nothing is needed.

## review.json form

The template `tools/race_review_template.html` always draws the sections in the same order: header with tiles, 01 In short, 02 Race story
(charts of position, lap vs field, gaps), 03 What went well, 04 What cost time (with telemetry), 05 Map, 06 Corners,
07 Tyres/strategy/energy, 08 Field pace, 09 What to learn, 10 Task, 11 Caveats. Charts come from data.json, the text from here:

```json
{
  "lang": "ru",                                    // the user's language, see "Page language"
  "labels": {},                                    // only if tools/race_review_labels/<lang>.json does not exist
  "page_title": "Mexico: race review",             // <title>, 2–4 words
  "date": "10 November 2026",
  "title": "the main point in one line",
  "headline": "2–3 sentences: the result and what decided the race",
  "tiles": [{"label": "Finish", "value": "P13", "note": "started P20", "tone": "good|bad|"}],   // exactly 4
  "summary": ["paragraph", "paragraph", "paragraph"],   // result; where time went; the main lesson
  "story": "the race lap by lap, 3–5 sentences",
  "marks": [{"lap": 7, "label": "spin", "tone": "bad|good"}],   // labels on the lap charts, up to 6
  "good": [{"title", "when", "what", "why", "keep"}],           // 1–3, only real strengths
  "bad_intro": "one sentence",
  "bad": [{"title", "when", "cost": "≈11 s", "what", "why", "assumption": "or empty",
           "cost_note", "action", "window": 0, "window_note"}],     // 3–5, from the most to the least expensive; window: index or null
  "map_marks": [{"dist": 4300, "label": "L7 spin", "dx": 12, "dy": -10, "anchor": "start|end"}],
  "corners_text": "what the losses in corners have in common",
  "strategy": [{"title": "Pit stops|Tyres|ERS and fuel|Start|Setup", "text"}],   // "Setup" whenever the setup changed or explains a loss
  "field_text": "comparison with the teammate and the field",
  "lessons": [{"title", "text", "drill"}],          // 2–4 skills: explain the mechanics, not just "do this"
  "task": {"title", "text", "check"},               // one task and how to check it in the next report
  "caveats": ["data limitations"]
}
```

## How to write

- `what`: what is visible in the telemetry, with metres, speeds, throttle, steering. `why`: the mechanics, why it happens.
  `action`: one concrete action that can be done on the next lap.
- The cost of an episode: lap time against neighbouring laps or against the field median, write the method in `cost_note`.
- Explain strengths and lessons so that the user understands the mechanics (weight, grip, torque, downforce, DRS).
- Corner numbers in the report are our own. Describe the place by metres and character ("the last complex before the main straight").
- Don't repeat the same thing in different sections: sections 03–04 are about episodes, 09 about skills.
- Previous reviews are in `races/reports/*/review.json`. If there are any, compare with the previous race and say whether
  the previous task was done.
