# F1 25 Dashboard

Telemetry tools for the F1 25 game: a UDP bridge that records every race, a report that turns a recording into numbers
for an honest race debrief, a review page, and a best-lap video maker.

The bridge also serves a web dashboard for a second screen, so you do not have to dig through the in-game menus: car
condition (tyre wear and damage on a car diagram), temperatures, power unit wear, the pit window and spare tyre sets, and
unserved penalties. The blocks can be dragged and resized on a single page that never scrolls. A test race and a replay
of a recording let you check it without the game.

The bridge also sends the main readings (speed, gear, revs and rev lights, tyre wear and temperature, position, gap,
last lap, DRS, ERS, safety car and pit call alerts) over USB serial to a small hardware display; that display's firmware
is not part of this repository.

## What's inside

| Path | What it is |
|---|---|
| `f1_bridge.py` | Receives the game's UDP telemetry, tracks the player's (or spectated) car, sends dashboard lines over serial and records races |
| `f1_state.py`, `f1_web.py`, `web/` | Web dashboard: car state from the packets, a standard library HTTP server, the page |
| `f1_demo.py` | Test race for the dashboard: real packets built from the specification, events on demand |
| `f1_record.py` | Race recording into `races/*.f1rec.gz` and reading it back |
| `tools/race_report.py` | Report on a recording: `races/reports/<recording>/report.md`, `laps.csv`, `corners.csv` |
| `tools/race_review_data.py`, `tools/race_review_page.py`, `tools/race_review_template.html`, `tools/race_review_labels/` | Race review page: chart data, review text, page labels per language |
| `tools/setup_history.py` | The player's car setup in every recording side by side, with the changes marked |
| `tools/race_setup_page.py`, `tools/race_setup_template.html`, `tools/race_setup_labels/` | Setup sheet page for the coming sessions |
| `tools/lap_video.py` | Best lap video: a replay clip with lap timer and telemetry panels, and a title card with a song |
| `tools/test_bridge.py`, `tools/test_race_report.py`, `tools/test_web.py` | Tests on real UDP packets, on a simulated race with planted mistakes and of the web dashboard |
| `run/*.bat` | Windows shortcuts for all bridge modes, reports and tests; extra arguments are passed through |
| `docs/RACE_REVIEW.md` | How to review a race from the report |
| `.claude/skills/` | [Claude Code](https://claude.com/claude-code) skills: `race-review` (review page) and `lap-video` (best lap video) |

## Requirements

- Python 3.9 or newer (developed on 3.13), `pip install -r requirements.txt` (only `pyserial`; the report tools use the standard library).
- F1 25 with telemetry on: Settings > Telemetry Settings: UDP Telemetry On, Broadcast Off, IP `127.0.0.1`, port `20777`,
  Send Rate 60 Hz, UDP Format 2025. The 2026 format is not supported yet, the bridge tells you to switch.
- For `lap_video.py`: Windows, `ffmpeg`/`ffprobe` with libass and an NVIDIA GPU (`h264_nvenc`).

## Usage

```
python f1_bridge.py                          # dashboard + race recording
python f1_bridge.py --no-record              # dashboard only
python f1_bridge.py --record-all-sessions    # record practice and qualifying too
python f1_bridge.py --record-full-packets    # record all packets without filtering
python f1_bridge.py --demo                   # drive the display without the game
python f1_bridge.py --port COM5              # serial port by hand (detected automatically by default)
python f1_bridge.py --no-nano                # no hardware display: web dashboard and recording only
python f1_bridge.py --no-web                 # no web dashboard

python f1_web.py --demo --open               # web dashboard with a test race, no game needed
python f1_web.py --replay --open             # web dashboard replaying the last recording
python f1_web.py --replay FILE --speed 4 --skip 10   # a given recording, 4x faster, from minute 10

python tools/race_report.py                  # report on the last race
python tools/race_report.py --any            # the last session of any type
python tools/race_report.py --list           # list recordings
python tools/setup_history.py --track Shanghai # setups of all sessions at a track
```

The web dashboard runs at http://localhost:8025/ while the bridge is running (`--web-port` to change it, `--web-host
0.0.0.0` to open it from a tablet). Drag a block by its header to move it, drag the bottom right corner to resize it;
a block dropped on another one swaps places with it. The layout is saved in the browser, "Reset layout" restores the
default. In the test race the "Test" menu triggers damage, penalties, overheating, faults, a pit stop or rain at once.
Color thresholds for temperatures and wear are at the top of `web/app.js`; the temperature ones are estimates. The game
does not send the strategy plan from the MFD, only the pit window (ideal and latest lap, rejoin position).

Only one program can listen on UDP port 20777, so recording is built into the bridge. Packets are received in their own
thread: the recording loses nothing even if the serial port hangs. A file opens when a race starts and closes 15 s after
the final classification. By default only what the report needs is kept (own car at full rate, rivals at 20 Hz), which
makes a 50-minute race about 170 MB.

The report only measures: laps, clean laps, stints and degradation, field pace, corners found from speed drops and where
time is lost in them, one-off losses, comparison with the fastest rival and the car ahead, tyres, brakes, ERS, fuel, pit
stops, events and data quality. How to turn it into criticism is described in `docs/RACE_REVIEW.md`.

## Tests

```
python tools/test_bridge.py
python tools/test_race_report.py
python tools/test_web.py
```

None of them needs the game or hardware, and both can run next to a working bridge.

## Personal files

`local/` is ignored by git. Keep personal notes and settings there, for example `local/lap-video.md` for the lap video skill.

## License

MIT, see `LICENSE`. This is a fan project, not affiliated with Electronic Arts, Codemasters or Formula 1.
The UDP format follows the specification EA publishes for F1 25.
