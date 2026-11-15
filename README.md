# F1 25 Dashboard

Telemetry tools for the F1 25 game: a UDP bridge that records every race, a report that turns a recording into numbers
for an honest race debrief, a review page, and a best-lap video maker.

A full second-screen dashboard showing the car's main readings is the next goal. Today the bridge assembles the same
readings (speed, gear, revs and rev lights, tyre wear and temperature, position, gap, last lap, DRS, ERS, safety car and
pit call alerts) and sends them over USB serial to a small hardware display; that display's firmware is not part of this
repository.

## What's inside

| Path | What it is |
|---|---|
| `f1_bridge.py` | Receives the game's UDP telemetry, tracks the player's (or spectated) car, sends dashboard lines over serial and records races |
| `f1_record.py` | Race recording into `races/*.f1rec.gz` and reading it back |
| `tools/race_report.py` | Report on a recording: `races/reports/<recording>/report.md`, `laps.csv`, `corners.csv` |
| `tools/race_review_data.py`, `tools/race_review_page.py`, `tools/race_review_template.html`, `tools/race_review_labels/` | Race review page: chart data, review text, page labels per language |
| `tools/lap_video.py` | Best lap video: a replay clip with lap timer and telemetry panels, and a title card with a song |
| `tools/test_bridge.py`, `tools/test_race_report.py` | Tests on real UDP packets and on a simulated race with planted mistakes |
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

python tools/race_report.py                  # report on the last race
python tools/race_report.py --any            # the last session of any type
python tools/race_report.py --list           # list recordings
```

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
```

Neither needs the game or hardware, and both can run next to a working bridge.

## Personal files

`local/` is ignored by git. Keep personal notes and settings there, for example `local/lap-video.md` for the lap video skill.

## License

MIT, see `LICENSE`. This is a fan project, not affiliated with Electronic Arts, Codemasters or Formula 1.
The UDP format follows the specification EA publishes for F1 25.
