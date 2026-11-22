# How to review a race

The user's request: constructive criticism of a race based on the available data. Reply in the user's language.
The user asks something like "review my last race".

## Steps

1. `py -3 tools/race_report.py` analyses the last race. Another session: `--any`, list of recordings: `--list`, a file can be given explicitly.
2. Read `races/reports/<recording>/report.md` in full, starting with the "Data quality" section. If it shows gaps, missing packets
   or a slip threshold that clearly doesn't fit, say so and take it into account in the conclusions.
3. For details go to `laps.csv`, `corners.csv` next to the report. If a single pass is needed, read the raw data:
   `race_report.load(path).trace`, the columns are listed in `PLAYER_COLUMNS`. Do calculations with a script, not in your head.
4. Write the review in the form below.

## Review form

1. **In short:** the result and the main point in 2 sentences.
2. **What went well:** 1–3 items with numbers. Only real strengths.
3. **What cost the most time:** 3–5 items from the most expensive to the cheapest. In each:
   - what happened, where and on which laps;
   - what it cost: seconds per lap and per race;
   - what to try next time, as one concrete action.
4. **Strategy and incidents:** pit stops, tyres, ERS, fuel, contacts, penalties, if there is something to say.
5. **One task for the next race:** one corner or one skill, and how it will be visible that it worked.

## Rules

- Every conclusion is backed by a number from the report. No number, no conclusion.
- Separate fact from assumption: "you brake 14 m earlier" is a fact, "because of a lack of confidence" is an assumption and must be labelled as such.
- Give an action, not a grade: not "you take T7 badly", but "in T7 on fast laps you brake 12 m later and exit 8 km/h faster, start by braking 5 m later".
- Don't attribute a mistake without checking the context: traffic, blue flags, safety car, damage, tyre wear and fuel change the picture.
  Traffic is marked in the report. Late braking in a fight may be an overtake, not a mistake.
- The best and the worst passes happen on different tyres and fuel. Compare within a stint when it matters.
- Bots: comparison with them is approximate, treat the user's own best passes as the main reference.
- Assists: with ABS or TC on, the absence of lock-ups and wheelspin proves nothing. With automatic ERS or DRS these are not the user's decisions.
- Corner numbers in the report are our own, not official. Name the official number only when certain, otherwise the lap metre.
- If the data has no answer, say so. Don't fill the review with generic advice.
- Check the setup before blaming the hands: the report's "Setup" section, the changes during the session and
  `tools/setup_history.py`. A lap is compared with the setup it was driven on, and a pattern of equal corners with slow
  straights or exits is a setup finding, not a driving one. Details: `docs/RACE_SETUP.md`.

## Review page

The review is presented as a page following the skill `.claude/skills/race-review/SKILL.md`: `tools/race_review_data.py` (chart data),
text in `review.json`, `tools/race_review_page.py` builds the page from `tools/race_review_template.html`.
Page labels come from `tools/race_review_labels/`, so the page is in the same language as the review text.

## Notes from real recordings

- Qualifying: `race_report.py` builds a report, but "Result" is taken from the session classification, and "Clean laps" count
  cool-down laps. The lap number in `trace` in qualifying is shifted against `race.history` (the out lap comes first).
- If the player did not get into Q3, the Q3 recording has 0 player frames (`race.player` = None), the report says "No lap data in the recording".
- When a session is restarted the game gives the same UID, the bridge writes a second file. The first one (2 laps) can be skipped.
- In the comparison with a rival the sign of "Full throttle, m" = yours minus theirs: minus means you are earlier.
- The last corner's segment includes the straight to the finish, so a loss there may come from ERS rather than the corner.
- A recording of a ~52 min race: 171 MB, 0 gaps, 0 flashbacks, the file closed by itself. The report is built in 6 s.
- ERS maximum 4.00 MJ, `ERS_MAX_JOULES` is correct.
- Slip: median 0.029, 95% 0.086, 99% 0.177; the 0.15 threshold looks right (spins give 0.5–4), but it is not verified.
- Mexico: 7 corners found, sections merge, there are more official ones. Enough for the review, describe places by metres.
- Online, the second player is also called `Player`, find the teammate via `race.teams`.
- An impact is visible in `g_lon`/`g_lat` (−18.9 g and 9.6 g), wing damage in `wing_fl/wing_fr`.
- Recordings of the same race from two PCs have the same session UID; the time in the file name follows each PC's clock.

- The setup changes during a session: the differential and the brake bias on the move, the front wing at a pit stop
  (Melbourne 20.11: 23 → 22 → 35). The first setup packets can carry the garage default. The report shows the setup driven
  with longest and every change.
- Parc ferme is on in the lobby, but after a weather change between qualifying and the race the setup could be changed
  without a pit lane start (Shanghai 21.11).

## What to check on real recordings

The model matched everything, but the thresholds were set before real game data. Check and adjust `tools/race_report.py`:

- the lock-up and wheelspin threshold `SLIP_THRESHOLD`: the distribution is printed in "Data quality", the slip units are not stated in the specification;
- `ERS_MAX_JOULES` against the maximum from the recording;
- corner boundaries: do they match the real ones on a familiar track, does an extra corner trigger;
- the real file size per hour and the presence of gaps;
- do `LGOT` and Final Classification fire the way the recording expects, and did the file close by itself;
- is 20 Hz rival data enough to compare braking;
- is the cool-down lap included in the recording and how does it look in the table;
- what happens to the data on a session restart and whether a separate file is needed.
