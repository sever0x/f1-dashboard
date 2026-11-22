# Car setup: data, rules and known facts

How to read the setup from recordings and how to prepare one. The page is built by `.claude/skills/race-setup/SKILL.md`.

## Where the setup is in the data

- Car Setups packet (id 5), one record per car; `race_report.SETUP_FIELDS` names the fields in packet order. Tyre pressures
  come rear first: RL, RR, FL, FR.
- `race.setup` is the last setup the game sent, `race.setup_log` every change with the session time,
  `race_report.setup_periods(race)` the periods with the lap and metre where each started, `race_report.main_setup(race)`
  the setup the car was driven with longest (frames above 90 km/h, so garage and pit lane time do not count).
- The report's "Setup" section prints the main setup, the changes during the session, the parc ferme rule and the top
  speed against the rivals' best laps. `tools/setup_history.py` lists all recordings side by side.
- Parc ferme: `m_parcFermeRules` in the Session packet (offset 694 after the header), `race.session["parc_ferme"]`.

## Rules

- Compare a lap with the setup it was driven on. The differential and the brake bias change on the move (MFD), the front
  wing at a pit stop. A report's last setup can differ from the one most laps were driven with.
- Separate setup from driving before calling something a driving mistake. Equal corner speeds with slower straights or
  exits against a rival of similar pace mean setup (wings, rear grip, ERS); slower corners mean driving or grip.
- A single best lap is noisy for top speed: DRS and slipstream add 10–15 km/h. Trust a gap that repeats in qualifying and on
  clean race laps.
- Change one thing at a time against a known base. A setup that changes six parameters at once makes the car "completely
  different" and nobody can tell which change did it.
- A value the user has not driven is an assumption. Say so on the page and give the number that will confirm it.

## Parameter ranges

The UDP specification does not give the ranges. Seen in the recordings: wings 0–50, differential 30–100 on throttle and 20–55 off throttle,
camber front −3.50…−3.40 and rear −2.00…−1.90, toe front 0.00–0.08 and rear 0.10–0.18, suspension 1–41, anti-roll bar 1–21,
ride height front 19–25 and rear 40–51, brake bias 52–58, tyre pressure front 23.5–29.5 and rear 20.5–25.5 psi.
The game's menus allow, as far as known (check in the game before relying on it): differential 10–100, camber front
−3.50…−2.50 and rear −2.00…−1.00, toe front 0.00–0.50 and rear 0.10–0.25, tyre pressure front 22.5–29.5 and rear 20.5–26.5 psi.

## Parc ferme

- The rule was on in every recording from 16.11 onwards (Qatar, Abu Dhabi, Melbourne, Shanghai).
- Shanghai 21.11: sprint qualifying dry, race in light rain. Wings, differential, tyre pressures and brake bias were
  changed before the race, and the car started from its grid slot (P18) without a penalty. The game seems to lift parc ferme
  when the conditions change between qualifying and the race. One case, not a documented rule.
- When the weather stays the same, the user kept the qualifying setup for the race (Qatar, Brazil), so the locked case has
  not been seen yet. Expected: a change means a pit lane start.
- Under parc ferme the differential and the brake bias still change on the move and the front wing at a pit stop.

## Known facts from recordings

- The first setup packets of a session can carry the garage default. In practice the setup packet alternated between two
  sets while the car was in the pit lane; only values driven above 90 km/h count.
- The user's working base (Alpine and Mercedes, 12.11–20.11): suspension 41/1–6, anti-roll bar front 1, tyre pressure
  front 29.5 and rear 20.5–22.0 psi, camber −3.50/−2.00, toe 0.00/0.10, differential 100 on throttle. Wins in Qatar and Abu
  Dhabi on this base.
- Shanghai 21.11: practice on 23/23 with suspension 37/19 and pressures 24.2/21.3 (a default-like setup) gave field-median
  top speed (334 km/h). Qualifying on 50/35, rear suspension 32 and rear pressure 25.2 psi: 9 km/h below the rivals on the
  straight and slow exits, the user felt "a completely different car". The rear tyres were also cold on the push lap
  (68–70 °C inner in the first half of the lap against 85–94 °C on practice push laps).
- Shanghai race in light rain on intermediates at 23.5/20.5 psi: inner temperatures 52–82 °C, the best lap came at 76–79 °C.
