---
name: race-setup
description: Car setups for coming F1 25 sessions, built from the user's own recordings, as an artifact page in a fixed format. Use when the user asks (in any language) for a setup, a configuration or settings for a track or a session ("prepare a setup for Shanghai", "what setup for a wet qualifying", "the car felt different, what changed", «подготовь конфигурацию», «настройки на квалу и гонку», «сетап на дождь»), or when a race review shows that the setup, not the driving, explains a loss.
---

# Race setup

Reply in the user's language, and write all page text in that language too. Goal: a setup the user can type into the game
tonight, with every change explained, and a way to check it in the next report. Rules and known facts: `docs/RACE_SETUP.md`,
read it first.

## Steps

1. Ask only what the data cannot answer: track, which sessions are coming and in what weather, race length, whether the user
   wants only the basic parameters or everything (geometry, suspension). If the user already said it, don't ask.
2. Setup history: `py -3 tools/setup_history.py` (one track: `--track Shanghai`, for scripts: `--json`). It shows, per
   recording, the setup the car was driven with longest, how many times it changed, parc ferme, weather and the result.
3. Pick the **base**: the user's setup from the session with the best result and a normal-feeling car on the same car
   (team), preferably at a similar track. Name it on the page. A setup that only appears in a garage or for one lap is not a base.
4. Diagnose before changing anything. For the latest sessions at this track (or the last race) read the report's
   "Setup" section and look at:
   - **changes during the session** (the table under "Setup"): the differential and the brake bias change on the move,
     the front wing at a pit stop, the rest only in the garage. A lap must be compared with the setup it was driven on;
   - **top speed** against the rivals in qualifying and on clean race laps (`Vmax` in the ERS table): 8 km/h or more on
     every lap points to wings (or an empty battery), not to driving;
   - **where the time goes** against a rival's best lap of similar pace: a script in the scratchpad over
     `race.rival_best[car]["trace"]` (columns `RIVAL_COLUMNS`) against the player's best lap, cumulative delta every
     200 m with min and max speed per segment. Corners equal and straights or exits slower → setup; corners slower → driving or grip;
   - **traction**: slip above 0.15 with throttle above 30 % on corner exits, laps with wheelspin in "Corners";
   - **tyre temperature**: inner temperature by lap in "Tyres and brakes", and per quarter of a lap for a qualifying lap
     (cold tyres on the push lap make "a different car");
   - **wear**: which tyre limits the stint ("Tyre stints"), for camber and pit stop planning;
   - **damage**: `wing_fl/wing_fr/wing_r/floor` by lap; a pit stop fixes only the front wing.
5. Parc ferme: the report says whether the rule is on. Read "Parc ferme" in `docs/RACE_SETUP.md` and give the user
   a plan for both cases: changes allowed after qualifying, and not allowed (a compromise setup, what can still change
   on the move and at a pit stop).
6. Build the sheets: change only what the diagnosis or the conditions give a reason for, keep the base elsewhere. For every
   change write one line in `why`: the number from the data and the mechanics. Mark untested assumptions as assumptions.
   When the user wants everything, add a second section with geometry, suspension and engine braking, and say to revert
   the geometry first if the car feels wrong.
7. Write `<scratchpad>/setup.json` (form: the docstring of `tools/race_setup_page.py`), build the page:
   `py -3 tools/race_setup_page.py setup.json -o <scratchpad>/race_setup_<track>.html`, check the page script
   (`node --check` on the extracted `<script>`) and publish via Artifact. A new weekend is a new artifact; changes for the
   same weekend go to the same one.
8. Copy `setup.json` and the html into `races/setups/<Track>_<date of the sessions>/`.
9. In the reply: the link, what changed against the base in one table or 3–5 lines, the parc ferme plan, and what to check
   in the next report.

## Page language

`lang` in setup.json picks `tools/race_setup_labels/<lang>.json` (English when missing). For a language without a file,
translate `tools/race_setup_labels/en.json` and put the values into setup.json as `labels`.

## How to write

- Start every setup from the base. Never invent a full setup from general knowledge: values the user has not driven are
  assumptions, and the page says so.
- One reason per change, with a number: "top speed 339 against 348 km/h with 50/35" beats "less drag".
- Name what the user will feel ("the rear steps out on exits") as well as the parameter.
- Give the checks for the next report in the checklist: the number that shows whether the change worked.
- After the sessions, review them with the race-review skill and compare with the sheet: the setup history marks every
  changed value in bold.
