"""Setup history: the player's car setup in every recording, side by side.

Scans the session, participants and setup packets of each recording (about 2 s for a race file). The setup shown is the
one the car was driven with longest: the first packets can carry the garage default, and the differential and brake bias change on the move.
The result of a session is taken from its report in races/reports/ when the report exists.

    py -3 tools/setup_history.py                    # all recordings, newest first
    py -3 tools/setup_history.py --track Shanghai   # one track
    py -3 tools/setup_history.py --json             # machine-readable, for scripts
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import struct
import sys
from collections import Counter
from typing import Dict, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import race_report as rr  # noqa: E402
from f1_record import DEFAULT_DIR, read_packets  # noqa: E402

TEAMS = {0: "Mercedes", 1: "Ferrari", 2: "Red Bull", 3: "Williams", 4: "Aston Martin", 5: "Alpine", 6: "RB", 7: "Haas",
         8: "McLaren", 9: "Sauber"}
def peek(path: pathlib.Path) -> Optional[dict]:
    """Session, team and setup of the player. The whole file is scanned (about 2 s for a race): the first setup packets
    can show the garage default and the setup changes on the move, so the setup shown is the one the car was driven with longest."""
    sessions = Counter()
    first_session = None
    team = setup = player = None
    setups = []  # (session time, setup) at every change
    last_t = 0.0
    last_telemetry = None
    driven: Dict[tuple, float] = {}  # setup -> seconds on track above 90 km/h
    for _, data in read_packets(path):
        if len(data) < rr.HEADER.size:
            continue
        header = rr.HEADER.unpack_from(data)
        if header[0] != rr.FORMAT:
            continue
        last_t = max(last_t, header[7])
        pid = header[5]
        if pid not in (rr.PID_SESSION, rr.PID_PARTICIPANTS, rr.PID_SETUPS, rr.PID_TELEMETRY):
            continue
        if pid in rr.SIZES and len(data) != rr.SIZES[pid]:
            continue
        if header[10] < rr.CARS:
            player = header[10]
        o = rr.HEADER.size
        if pid == rr.PID_TELEMETRY:
            if player is not None and setups:
                moving = struct.unpack_from("<H", data, o + player * rr.TELEMETRY.size)[0] > 90  # above the pit lane limit
                dt = header[7] - last_telemetry if last_telemetry is not None else 0.0
                if moving and 0 < dt < 1:
                    driven[setups[-1][1]] = driven.get(setups[-1][1], 0.0) + dt
            last_telemetry = header[7]
        elif pid == rr.PID_SESSION:
            weather, track_temp = struct.unpack_from("<Bb", data, o)
            kind, track = data[o + 6], struct.unpack_from("<b", data, o + 7)[0]
            first_session = first_session or (kind, track)
            sessions[(weather, track_temp, data[o + 694])] += 1
        elif pid == rr.PID_PARTICIPANTS and player is not None:
            team = rr.PARTICIPANT.unpack_from(data, o + 1 + player * rr.PARTICIPANT.size)[3]
        elif pid == rr.PID_SETUPS and player is not None:
            setup = rr.SETUP.unpack_from(data, o + player * rr.SETUP.size)
            if not setups or setups[-1][1] != setup:
                setups.append((header[7], setup))
    if not first_session or not setup:
        return None
    (weather, track_temp, parc), _ = sessions.most_common(1)[0]
    held: Dict[tuple, float] = {}
    for i, (start, values) in enumerate(setups):
        stop = setups[i + 1][0] if i + 1 < len(setups) else last_t
        held[values] = held.get(values, 0.0) + max(stop - start, 0.0)
    totals = driven if driven else held  # garage time does not count when there is driving
    setup = max(totals, key=totals.get)
    kind, track = first_session
    return {"file": path.name, "track": rr.TRACKS.get(track, f"track {track}"),
            "session": rr.SESSION_TYPES.get(kind, "?"), "team": TEAMS.get(team, f"team {team}"),
            "weather": rr.WEATHER.get(weather, "?"), "track_temp": track_temp, "parc_ferme": bool(parc),
            "setup_changes": len(setups) - 1, "setup": rr.setup_named(setup),
            "changes": [{"t": round(start), **{name: value for name, value in rr.setup_named(values).items()
                                               if i and value != rr.setup_named(setups[i - 1][1])[name]}}
                        for i, (start, values) in enumerate(setups) if i]}


def results_from_reports(reports: pathlib.Path) -> Dict[str, str]:
    """Recording file name -> the result line of its report."""
    found = {}
    for report in reports.glob("*/report.md"):
        text = report.read_text(encoding="utf-8", errors="replace")
        name = re.search(r"File: `([^`]+)`", text)
        if not name:
            continue
        result = re.search(r"- Result: (P\d+) from grid (P\d+)", text)
        best = re.search(r"Best clean lap: (\S+)", text)
        if result:
            found[name.group(1)] = f"{result.group(1)} (grid {result.group(2)})"
        elif best:
            found[name.group(1)] = f"best {best.group(1).rstrip('.')}"
        elif "No final classification" in text:
            found[name.group(1)] = "not finished"
    return found


COLUMNS = (
    ("Wings", ("front_wing", "rear_wing"), 0), ("Diff", ("diff_on_throttle", "diff_off_throttle"), 0),
    ("Camber", ("front_camber", "rear_camber"), 2), ("Toe", ("front_toe", "rear_toe"), 2),
    ("Susp.", ("front_suspension", "rear_suspension"), 0), ("ARB", ("front_anti_roll_bar", "rear_anti_roll_bar"), 0),
    ("Ride h.", ("front_ride_height", "rear_ride_height"), 0), ("Bias", ("brake_bias",), 0), ("Eng. br.", ("engine_braking",), 0),
    ("Press. F", ("pressure_fl",), 1), ("Press. R", ("pressure_rl",), 1), ("Fuel", ("fuel",), 1),
)


def cell(setup: dict, names, digits: int) -> str:
    return "/".join(f"{setup[name]:.{digits}f}" for name in names)


def render(rows: List[dict]) -> str:
    head = ["Recording", "Track", "Session", "Team", "Weather", "Parc f.", "Changes"] + [c[0] for c in COLUMNS] + ["Result"]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    # Rows are newest first; a value is marked when it differs from the next older session at the same track.
    for i, row in enumerate(rows):
        older = next((r for r in rows[i + 1:] if r["track"] == row["track"]), None)
        cells = []
        for title, names, digits in COLUMNS:
            value = cell(row["setup"], names, digits)
            if older and value != cell(older["setup"], names, digits):
                value = f"**{value}**"
            cells.append(value)
        out.append("| " + " | ".join([row["file"][:16], row["track"], row["session"], row["team"], f"{row['weather']} {row['track_temp']}°",
                                      "on" if row["parc_ferme"] else "off", str(row["setup_changes"])] + cells + [row.get("result", "")]) + " |")
    out.append("")
    out.append("Bold: changed against the previous session at the same track. "
               "Changes: how many times the setup changed during the recording, the table shows the setup the car was driven with longest (details: the Setup section of the report). Tyre pressures: front FL, rear RL (left and right are equal in all recordings so far).")
    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="The player's setup in every recording")
    parser.add_argument("--dir", default=str(DEFAULT_DIR), help="recordings folder")
    parser.add_argument("--track", help="only this track (part of the name, any case)")
    parser.add_argument("--limit", type=int, default=0, help="at most this many recordings")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args()
    directory = pathlib.Path(args.dir)
    results = results_from_reports(directory / "reports")
    rows = []
    for path in rr.recordings(directory):
        info = peek(path)
        if not info:
            continue
        if args.track and args.track.lower() not in info["track"].lower():
            continue
        info["result"] = results.get(path.name, "")
        rows.append(info)
        if args.limit and len(rows) >= args.limit:
            break
    print(json.dumps(rows, ensure_ascii=False, indent=1) if args.json else render(rows))


if __name__ == "__main__":
    main()
