#!/usr/bin/env python3
"""Session recording analysis: a .f1rec.gz file becomes a short report for critiquing the race.

    py -3 tools/race_report.py              the last recorded race
    py -3 tools/race_report.py --any        the last session of any type
    py -3 tools/race_report.py --list       all recordings
    py -3 tools/race_report.py FILE         a specific recording

Output in races/reports/<track>_<sessionType>_<date>_<HH-MM>/: report.md, laps.csv, corners.csv.
Only measurements here. How to write a critique from them: docs/RACE_REVIEW.md.

Fields follow the EA F1 25 UDP specification v3. Standard library only.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime
import math
import pathlib
import re
import statistics
import struct
import sys
from array import array
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from f1_record import DEFAULT_DIR, read_packets  # noqa: E402

FORMAT = 2025
CARS = 22
HEADER = struct.Struct("<HBBBBBQfIIBB")
LAP = struct.Struct("<IIHBHBHBHBfff15BHHBfB")
TELEMETRY = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")
STATUS = struct.Struct("<BBBBBfffHHBBHBBBbfffBfffB")
DAMAGE = struct.Struct("<4f4B4B4B18B")
MOTION = struct.Struct("<6f6h6f")
MOTION_EX = struct.Struct("<61f")
SETUP = struct.Struct("<4B4f9B4fBf")
# Car Setups packet order. Tyre pressures come rear first: RL, RR, FL, FR.
SETUP_FIELDS = (
    "front_wing", "rear_wing", "diff_on_throttle", "diff_off_throttle", "front_camber", "rear_camber", "front_toe", "rear_toe",
    "front_suspension", "rear_suspension", "front_anti_roll_bar", "rear_anti_roll_bar", "front_ride_height", "rear_ride_height",
    "brake_pressure", "brake_bias", "engine_braking", "pressure_rl", "pressure_rr", "pressure_fl", "pressure_fr", "ballast", "fuel",
)
FINAL = struct.Struct("<7BIdBBB8B8B8B")
PARTICIPANT = struct.Struct("<7B32sBBHBB12x")
LAP_HISTORY = struct.Struct("<IHBHBHBB")
STINT_HISTORY = struct.Struct("<BBB")

PID_MOTION, PID_SESSION, PID_LAP, PID_EVENT, PID_PARTICIPANTS, PID_SETUPS, PID_TELEMETRY, PID_STATUS = range(8)
PID_FINAL, PID_LOBBY, PID_DAMAGE, PID_HISTORY, PID_TYRE_SETS, PID_MOTION_EX = range(8, 14)
FRAME_PACKETS = {PID_MOTION, PID_LAP, PID_TELEMETRY, PID_STATUS, PID_MOTION_EX}
SIZES = {
    PID_MOTION: 1349, PID_SESSION: 753, PID_LAP: 1285, PID_EVENT: 45, PID_PARTICIPANTS: 1284, PID_SETUPS: 1133,
    PID_TELEMETRY: 1352, PID_STATUS: 1239, PID_FINAL: 1042, PID_DAMAGE: 1041, PID_HISTORY: 1460, PID_MOTION_EX: 273,
}

TRACKS = {
    0: "Melbourne", 2: "Shanghai", 3: "Sakhir", 4: "Catalunya", 5: "Monaco", 6: "Montreal", 7: "Silverstone",
    9: "Hungaroring", 10: "Spa", 11: "Monza", 12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "Texas",
    16: "Brazil", 17: "Austria", 19: "Mexico", 20: "Baku", 26: "Zandvoort", 27: "Imola", 29: "Jeddah", 30: "Miami",
    31: "Las Vegas", 32: "Losail", 39: "Silverstone (Reverse)", 40: "Austria (Reverse)", 41: "Zandvoort (Reverse)",
}
SESSION_TYPES = {
    0: "Unknown", 1: "P1", 2: "P2", 3: "P3", 4: "Short Practice", 5: "Q1", 6: "Q2", 7: "Q3", 8: "Short Q",
    9: "One-Shot Q", 10: "SQ1", 11: "SQ2", 12: "SQ3", 13: "Short SQ", 14: "One-Shot SQ", 15: "Race", 16: "Race 2",
    17: "Race 3", 18: "Time Trial",
}
RACE_SESSIONS = {15, 16, 17}
WEATHER = {0: "clear", 1: "light cloud", 2: "overcast", 3: "light rain", 4: "heavy rain", 5: "storm"}
COMPOUNDS = {16: "S", 17: "M", 18: "H", 7: "I", 8: "W", 9: "D", 10: "W"}
ACTUAL_COMPOUNDS = {16: "C5", 17: "C4", 18: "C3", 19: "C2", 20: "C1", 21: "C0", 22: "C6", 7: "I", 8: "W"}
ERS_MODES = {0: "NONE", 1: "MEDIUM", 2: "HOTLAP", 3: "OVERTAKE"}
FUEL_MIXES = {0: "LEAN", 1: "STD", 2: "RICH", 3: "MAX"}
PENALTY_TYPES = {
    0: "Drive through", 1: "Stop Go", 2: "Grid penalty", 3: "Penalty reminder", 4: "Time penalty", 5: "Warning",
    6: "Disqualified", 7: "Removed from formation lap", 8: "Parked too long", 9: "Tyre regulations",
    10: "This lap invalidated", 11: "This and next lap invalidated", 12: "This lap invalidated", 13: "This and next lap invalidated",
    14: "This and previous lap invalidated", 15: "This and previous lap invalidated", 16: "Retired", 17: "Black flag timer",
}
INFRINGEMENTS = {
    0: "Blocking by slow driving", 1: "Wrong way", 2: "Reversing off start line", 3: "Big collision", 4: "Small collision",
    5: "Failed to hand back position", 6: "Failed to hand back positions", 7: "Corner cutting, time gained",
    8: "Corner cutting overtake", 9: "Corner cutting overtakes", 10: "Crossed pit exit lane", 11: "Ignoring blue flags",
    12: "Ignoring yellow flags", 13: "Ignoring drive through", 14: "Too many drive throughs", 15: "Drive through reminder",
    16: "Drive through reminder", 17: "Pit lane speeding", 18: "Parked too long", 19: "Tyre regulations",
    20: "Too many penalties", 21: "Multiple warnings", 22: "Approaching disqualification", 23: "Tyre regulations",
    24: "Tyre regulations", 25: "Lap invalidated, corner cutting", 26: "Lap invalidated, running wide",
    27: "Ran wide, minor time gain", 28: "Ran wide, significant time gain", 29: "Ran wide, extreme time gain",
    30: "Lap invalidated, wall riding", 31: "Lap invalidated, flashback", 32: "Lap invalidated, reset to track",
    33: "Blocking pit lane", 34: "Jump start", 35: "Safety car collision", 36: "Safety car illegal overtake",
    37: "Safety car pace exceeded", 38: "Virtual safety car pace exceeded", 39: "Formation lap below speed",
    40: "Formation lap parking", 41: "Retired, mechanical failure", 42: "Retired, terminally damaged",
    43: "Safety car fell too far back", 44: "Black flag timer", 45: "Unserved stop go", 46: "Unserved drive through",
    47: "Engine component change", 48: "Gearbox change", 49: "Parc ferme change", 50: "League grid penalty",
    51: "Retry penalty", 52: "Illegal time gain", 53: "Mandatory pit stop", 54: "Attribute assigned",
}
OFF_TRACK_SURFACES = {3, 4, 5, 6, 7, 8}  # rumble strips, gravel, mud, sand, grass, water

ERS_MAX_JOULES = 4_000_000  # from memory, the report prints the maximum from the recording
# The thresholds below are set by common sense and not verified on a game recording, see the "Data quality" section of the report.
SLIP_THRESHOLD = 0.15
MIN_SLIP_FRAMES = 3
BRAKE_ON = 0.2
FULL_THROTTLE = 0.95
OFF_WHEELS = 2  # this many wheels off track counts as going off
CORNER_SPEED_DROP = 15.0  # km/h: less noticeable braking does not count as a corner
GRID_METRES = 10.0
TRAFFIC_GAP_MS = 1000
GAP_SECONDS = 0.25  # a hole this big in session time counts as a data gap
MIN_PARTIAL_LAP_FRAMES = 60

PLAYER_COLUMNS = (
    "t", "lap", "dist", "lap_ms", "speed", "throttle", "brake", "steer", "gear", "rpm", "drs", "x", "z", "g_lat", "g_lon",
    "slip", "off_wheels", "surf_rl", "surf_rr", "surf_fl", "surf_fr", "inner_rl", "inner_rr", "inner_fl", "inner_fr",
    "brake_rl", "brake_rr", "brake_fl", "brake_fr", "wear_rl", "wear_rr", "wear_fl", "wear_fr", "position", "gap_ms",
    "pit", "invalid", "ers", "ers_mode", "fuel", "fuel_laps", "fuel_mix", "deployed", "harv_k", "harv_h", "compound",
    "age", "flag", "sc", "warnings", "cuts", "penalties", "pit_lane_ms", "pit_stop_ms", "wing_fl", "wing_fr", "wing_r",
    "floor", "bias", "lap_invalid_any",
)
RIVAL_COLUMNS = ("t", "dist", "lap_ms", "speed", "throttle", "brake", "gear", "x", "z")
RIVAL_INTERVAL = 0.049  # as in recording: the analysis skips frames without rival data by the car status


class Trace:
    """Number columns of equal length, sorted by time."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names = names
        self.columns = [array("f") for _ in names]
        self.__dict__.update(zip(names, self.columns))

    def __len__(self) -> int:
        return len(self.columns[0])

    def append(self, row: Sequence[float]) -> None:
        for column, value in zip(self.columns, row):
            column.append(value)

    def truncate_after(self, moment: float) -> int:
        """Removes samples after the moment, returns how many remain."""
        keep = bisect.bisect_right(self.columns[0], moment)
        for column in self.columns:
            del column[keep:]
        return keep


class Race:
    """Everything that could be extracted from the recording."""

    def __init__(self) -> None:
        self.path = None
        self.packets = Counter()
        self.wrong_format = 0
        self.first_wall = self.last_wall = None
        self.uid = None
        self.player: Optional[int] = None
        self.session: Dict[str, float] = {}
        self.weather_log: List[Tuple[int, float, int, int, int]] = []
        self.forecast: List[Tuple[int, int, int, int]] = []
        self.names: Dict[int, str] = {}
        self.teams: Dict[int, int] = {}
        self.setup: Optional[Tuple] = None  # the last setup the game sent
        self.setup_log: List[Tuple[float, Tuple]] = []  # (session time, setup) every time the setup changed
        self.history: Dict[int, dict] = {}
        self.final: Dict[int, dict] = {}
        self.events: List[dict] = []
        self.flashbacks: List[dict] = []
        self.trace = Trace(PLAYER_COLUMNS)
        self.rival_best: Dict[int, dict] = {}
        self.official_last_ms: Dict[int, int] = {}
        self.assists: Tuple[int, int] = (0, 0)
        self.gap_count = 0
        self.gap_seconds = 0.0
        self.motion_ex_frames = 0
        self.frames = 0
        self.ers_peak = 0.0
        self.lights_out: Optional[float] = None
        self.first_move: Optional[float] = None
        self.pit_window: Tuple[int, int] = (0, 0)


class Loader:
    """Reads packets one by one. A frame's data is gathered together: packets of one frame arrive in any order."""

    def __init__(self) -> None:
        self.race = Race()
        self._frame_id = None
        self._frame: Dict[int, bytes] = {}
        self._frame_time = 0.0
        self._rival_time: Optional[float] = None
        self._wear = (0.0, 0.0, 0.0, 0.0)
        self._wings = (0, 0, 0, 0)
        self._sc = 0
        self._last_t: Optional[float] = None
        self._lap_num = 0
        self._dist = 0.0
        self._invalid_any = 0
        self._last_weather = None
        self._status = (0,) * 14
        self._rival_lap: Dict[int, int] = {}
        self._rival_trace: Dict[int, Trace] = {}
        self._rival_clean: Dict[int, bool] = {}

    def feed(self, wall: float, data: bytes) -> None:
        race = self.race
        race.first_wall = race.first_wall if race.first_wall is not None else wall
        race.last_wall = wall
        if len(data) < HEADER.size:
            return
        header = HEADER.unpack_from(data)
        if header[0] != FORMAT:
            race.wrong_format += 1
            return
        pid = header[5]
        race.packets[pid] += 1
        if pid in SIZES and len(data) != SIZES[pid]:
            return
        race.uid = header[6]
        player = header[10]
        if player < CARS:
            race.player = player
        t, frame_id = header[7], header[8]

        if pid in FRAME_PACKETS:
            if frame_id != self._frame_id:
                self._flush_frame()
                self._frame_id = frame_id
                self._frame_time = t
            self._frame[pid] = data
        elif pid == PID_SESSION:
            self._read_session(data)
        elif pid == PID_EVENT:
            self._read_event(data, t)
        elif pid == PID_PARTICIPANTS:
            self._read_participants(data)
        elif pid == PID_SETUPS and race.player is not None:
            race.setup = SETUP.unpack_from(data, HEADER.size + race.player * SETUP.size)
            if not race.setup_log or race.setup_log[-1][1] != race.setup:
                race.setup_log.append((t, race.setup))
        elif pid == PID_DAMAGE and race.player is not None:
            record = DAMAGE.unpack_from(data, HEADER.size + race.player * DAMAGE.size)
            self._wear = record[:4]
            self._wings = record[16:20]
        elif pid == PID_HISTORY:
            self._read_history(data)
        elif pid == PID_FINAL:
            self._read_final(data)

    def finish(self) -> Race:
        self._flush_frame()
        return self.race

    def _read_session(self, data: bytes) -> None:
        o = HEADER.size
        weather, track_temp, air_temp, total_laps, length, kind, track = struct.unpack_from("<BbbBHBb", data, o)
        race = self.race
        self._sc = data[o + 124]
        race.session.update(
            total_laps=total_laps, length=length, kind=kind, track=track, ai=data[o + 640], network=data[o + 125],
            gearbox=data[o + 658], braking_assist=data[o + 657], ers_assist=data[o + 661], drs_assist=data[o + 662],
            racing_line=data[o + 663], flashback_limit=data[o + 681], damage=data[o + 687], equal_cars=data[o + 679],
            sc_periods=data[o + 676], vsc_periods=data[o + 677], red_flags=data[o + 678],
            pit_speed=data[o + 13], parc_ferme=data[o + 694],
        )
        race.pit_window = (data[o + 653], data[o + 654])
        now = (weather, track_temp, air_temp)
        if now != self._last_weather:
            self._last_weather = now
            race.weather_log.append((self._lap_num, HEADER.unpack_from(data)[7], *now))

    def _read_event(self, data: bytes, t: float) -> None:
        o = HEADER.size
        code = data[o:o + 4].decode("latin-1")
        race = self.race
        lap, dist = self._lap_num, self._dist
        if code == "FLBK":
            frame, target = struct.unpack_from("<If", data, o + 4)
            self._flashback(target, lap, dist)
            return
        if code == "LGOT":
            race.lights_out = t
            # The race started here: the formation lap and standing on the grid do not belong to lap 1
            self._frame.clear()
            self._frame_id = None
            race.trace.truncate_after(-1.0)
            self._rival_trace.clear()
            self._rival_lap.clear()
            self._invalid_any = 0
            self._last_t = t
        event = {"t": t, "lap": lap, "dist": dist, "code": code}
        if code == "COLL":
            event["cars"] = (data[o + 4], data[o + 5])
        elif code == "OVTK":
            event["cars"] = (data[o + 4], data[o + 5])
        elif code == "PENA":
            kind, infringement, car, other, seconds, on_lap, places = struct.unpack_from("<7B", data, o + 4)
            event.update(penalty=kind, infringement=infringement, car=car, other=other, seconds=seconds,
                         on_lap=on_lap, places=places)
        elif code == "RTMT":
            event.update(car=data[o + 4], reason=data[o + 5])
        elif code == "SCAR":
            event.update(sc_type=data[o + 4], sc_event=data[o + 5])
        elif code == "FTLP":
            event.update(car=data[o + 4], time=struct.unpack_from("<f", data, o + 5)[0])
        elif code not in ("SSTA", "SEND", "CHQF", "RDFL", "LGOT", "DRSE", "DRSD", "RCWN"):
            return  # buttons, lights, speed trap and the rest are not needed for the analysis
        race.events.append(event)

    def _flashback(self, target: float, lap: int, dist: float) -> None:
        """The undone stretch is removed from the data, but the fact itself and the lost events stay in the report."""
        self._frame.clear()
        self._frame_id = None
        race = self.race
        trace = race.trace
        erased = [event for event in race.events if event["t"] > target]
        race.events = [event for event in race.events if event["t"] <= target]
        rewound = trace.t[-1] - target if len(trace) else 0.0
        kept = trace.truncate_after(target)
        race.flashbacks.append({
            "lap": lap, "dist": dist, "seconds": rewound, "erased": erased,
            "to_lap": int(trace.lap[kept - 1]) if kept else 0, "to_dist": trace.dist[kept - 1] if kept else 0.0,
        })
        for car, rival in self._rival_trace.items():
            rival.truncate_after(target)
        for car, best in list(race.rival_best.items()):
            if best["end_t"] > target:
                del race.rival_best[car]
        self._last_t = target
        if kept:
            self._lap_num, self._dist = int(trace.lap[kept - 1]), trace.dist[kept - 1]
        if race.first_move is not None and race.first_move > target:
            race.first_move = None

    def _read_participants(self, data: bytes) -> None:
        for car in range(min(data[HEADER.size], CARS)):
            record = PARTICIPANT.unpack_from(data, HEADER.size + 1 + car * PARTICIPANT.size)
            name = record[7].split(b"\0", 1)[0].decode("utf-8", "replace")
            if name:
                self.race.names[car] = name
            self.race.teams[car] = record[3]

    def _read_history(self, data: bytes) -> None:
        o = HEADER.size
        car, laps, stints, best_lap, best1, best2, best3 = struct.unpack_from("<7B", data, o)
        if car >= CARS:
            return
        o += 7
        lap_rows = []
        for lap in range(min(laps, 100)):
            ms, s1, s1m, s2, s2m, s3, s3m, flags = LAP_HISTORY.unpack_from(data, o + lap * LAP_HISTORY.size)
            lap_rows.append({
                "ms": ms, "s1": s1m * 60_000 + s1, "s2": s2m * 60_000 + s2, "s3": s3m * 60_000 + s3,
                "valid": bool(flags & 1), "flags": flags,
            })
        o += 100 * LAP_HISTORY.size
        stint_rows = [STINT_HISTORY.unpack_from(data, o + i * STINT_HISTORY.size) for i in range(min(stints, 8))]
        self.race.history[car] = {"laps": lap_rows, "stints": stint_rows, "best_lap": best_lap,
                                  "best_sectors": (best1, best2, best3)}

    def _read_final(self, data: bytes) -> None:
        for car in range(min(data[HEADER.size], CARS)):
            r = FINAL.unpack_from(data, HEADER.size + 1 + car * FINAL.size)
            self.race.final[car] = {
                "position": r[0], "laps": r[1], "grid": r[2], "points": r[3], "stops": r[4], "status": r[5], "reason": r[6],
                "best_ms": r[7], "total": r[8], "penalty_s": r[9], "penalties": r[10], "stints": r[11],
                "visual": r[20:28], "end_laps": r[28:36],
            }

    def _flush_frame(self) -> None:
        frame = self._frame
        lap_data, telemetry = frame.get(PID_LAP), frame.get(PID_TELEMETRY)
        race = self.race
        car = race.player
        if lap_data is None or telemetry is None or car is None:
            frame.clear()
            return
        t = self._frame_time
        race.frames += 1
        if self._last_t is not None and t - self._last_t > GAP_SECONDS:
            race.gap_count += 1
            race.gap_seconds += t - self._last_t
        self._last_t = t
        self._append_player(car, t, lap_data, telemetry, frame.get(PID_STATUS), frame.get(PID_MOTION), frame.get(PID_MOTION_EX))
        if self._rival_time is None or t < self._rival_time or t - self._rival_time >= RIVAL_INTERVAL:
            self._rival_time = t
            self._append_rivals(car, t, lap_data, telemetry, frame.get(PID_MOTION))
        frame.clear()

    def _append_player(self, car, t, lap_data, telemetry, status, motion, motion_ex) -> None:
        race = self.race
        o = HEADER.size
        lap = LAP.unpack_from(lap_data, o + car * LAP.size)
        tel = TELEMETRY.unpack_from(telemetry, o + car * TELEMETRY.size)
        last_ms, current_ms = lap[0], lap[1]
        gap_ms = lap[7] * 60_000 + lap[6]
        dist, position, lap_num, pit, invalid = lap[10], lap[13], lap[14], lap[15], lap[18]
        warnings, cuts, penalties = lap[20], lap[21], lap[19]
        pit_lane_ms, pit_stop_ms = lap[28], lap[29]
        if lap_num != self._lap_num:
            if last_ms and lap_num == self._lap_num + 1:
                race.official_last_ms[self._lap_num] = last_ms
            self._invalid_any = 0
        self._invalid_any |= invalid
        self._lap_num, self._dist = lap_num, dist
        if lap[26] >= 3:  # finished or retired: the lap back to the pits is not analysed
            return

        speed, throttle, steer, brake, _clutch, gear, rpm, drs = tel[:8]
        brake_temps = tel[10:14]
        surface, inner = tel[14:18], tel[18:22]
        wheels_off = sum(1 for value in tel[-4:] if value in OFF_TRACK_SURFACES)
        if race.lights_out is not None and race.first_move is None and t >= race.lights_out and speed > 5:
            race.first_move = t

        if status is not None:
            s = STATUS.unpack_from(status, o + car * STATUS.size)
            self._status = (s[20], s[19], s[5], s[7], s[2], s[23], s[21], s[22], s[14], s[15], s[16], s[3], s[0], s[1])
            race.assists = (s[0], s[1])
            race.ers_peak = max(race.ers_peak, s[19])
        ers_mode, ers, fuel, fuel_laps, mix, deployed, harv_k, harv_h, compound, age, flag, bias, *_ = self._status

        x = z = g_lat = g_lon = 0.0
        if motion is not None:
            m = MOTION.unpack_from(motion, o + car * MOTION.size)
            x, z, g_lat, g_lon = m[0], m[2], m[12], m[13]
        slip = 0.0
        if motion_ex is not None:
            race.motion_ex_frames += 1
            slips = MOTION_EX.unpack_from(motion_ex, o)[16:20]
            slip = max(abs(value) for value in slips)

        wings = self._wings
        race.trace.append((
            t, lap_num, dist, current_ms, speed, throttle, brake, steer, gear, rpm, drs, x, z, g_lat, g_lon, slip,
            wheels_off, *surface, *inner, *brake_temps, *self._wear, position, gap_ms, pit, invalid, ers, ers_mode, fuel,
            fuel_laps, mix, deployed, harv_k, harv_h, compound, age, flag, self._sc, warnings, cuts, penalties, pit_lane_ms,
            pit_stop_ms, wings[0], wings[1], wings[2], wings[3], bias, self._invalid_any,
        ))

    def _append_rivals(self, player, t, lap_data, telemetry, motion) -> None:
        o = HEADER.size
        for car in range(CARS):
            if car == player:
                continue
            lap = LAP.unpack_from(lap_data, o + car * LAP.size)
            lap_num = lap[14]
            result = lap[26]
            if result not in (2, 3):  # only active and finished cars
                continue
            previous = self._rival_lap.get(car)
            if previous != lap_num:
                if previous is not None and lap_num == previous + 1:
                    self._finish_rival_lap(car, lap[0], t)
                self._rival_lap[car] = lap_num
                self._rival_trace[car] = Trace(RIVAL_COLUMNS)
                self._rival_clean[car] = True
            if lap[10] < 0:  # has not crossed the line yet
                continue
            if lap[15] or lap[18]:  # pits or an invalid lap
                self._rival_clean[car] = False
            tel = TELEMETRY.unpack_from(telemetry, o + car * TELEMETRY.size)
            x = z = 0.0
            if motion is not None:
                m = MOTION.unpack_from(motion, o + car * MOTION.size)
                x, z = m[0], m[2]
            self._rival_trace[car].append((t, lap[10], lap[1], tel[0], tel[1], tel[3], tel[5], x, z))

    def _finish_rival_lap(self, car: int, lap_ms: int, t: float) -> None:
        trace = self._rival_trace.get(car)
        length = self.race.session.get("length", 0)
        if trace is None or not lap_ms or not self._rival_clean.get(car) or not length or len(trace) < 10:
            return
        if trace.dist[0] > 150 or trace.dist[-1] < length - 150:  # the start or the end of the lap is not recorded
            return
        best = self.race.rival_best.get(car)
        if best is None or lap_ms < best["ms"]:
            self.race.rival_best[car] = {"ms": lap_ms, "lap": self._rival_lap[car], "trace": trace, "end_t": t}


def load(path) -> Race:
    loader = Loader()
    loader.race.path = pathlib.Path(path)
    for wall, data in read_packets(path):
        loader.feed(wall, data)
    return loader.finish()


class LapView:
    """One lap as a function of distance: samples with non-decreasing distance so that a spin does not break the lookup."""

    def __init__(self, trace: Trace, start: int, end: int, lap_ms: int) -> None:
        self.trace = trace
        self.lap_ms = lap_ms
        self.indices: List[int] = []
        self.dist: List[float] = []
        dist = trace.dist
        top = -1e9
        for i in range(start, end):
            if dist[i] > top and dist[i] >= 0:
                top = dist[i]
                self.indices.append(i)
                self.dist.append(top)

    def time_at(self, where: float) -> float:
        if not self.dist:
            return 0.0
        if where <= self.dist[0]:
            return 0.0 if where <= 1.0 else self.trace.lap_ms[self.indices[0]]
        if where >= self.dist[-1]:
            return float(self.lap_ms or self.trace.lap_ms[self.indices[-1]])
        return self.value_at(self.trace.lap_ms, where)

    def value_at(self, column, where: float) -> float:
        i = bisect.bisect_left(self.dist, where)
        i = min(max(i, 1), len(self.dist) - 1)
        d0, d1 = self.dist[i - 1], self.dist[i]
        v0, v1 = column[self.indices[i - 1]], column[self.indices[i]]
        return v0 if d1 == d0 else v0 + (v1 - v0) * (where - d0) / (d1 - d0)

    def between(self, a: float, b: float) -> List[int]:
        return self.indices[bisect.bisect_left(self.dist, a):bisect.bisect_right(self.dist, b)]

    def covers(self, length: float) -> bool:
        return len(self.dist) > 10 and self.dist[0] < 100 and self.dist[-1] > length - 100


class Corner:
    def __init__(self, number: int, start: float, apex: float, end: float, speed: float) -> None:
        self.number, self.start, self.apex, self.end, self.speed = number, start, apex, end, speed


def find_corners(view: LapView, length: float) -> List[Corner]:
    """A corner is detected by a speed drop on the reference lap; fast sections without braking do not count.

    Boundaries run through the speed maxima between the minima, so the sum of all segment times equals the lap time.
    """
    bins = int(length // GRID_METRES)
    if bins < 10 or not view.dist:
        return []
    raw = [view.value_at(view.trace.speed, i * GRID_METRES) for i in range(bins)]
    smooth = [statistics.fmean(raw[max(0, i - 2):i + 3]) for i in range(bins)]

    minima, maxima = [], []
    direction = 0  # 1 rising, -1 falling
    extreme_i, extreme_v = 0, smooth[0]
    for i, value in enumerate(smooth):
        if direction >= 0 and value > extreme_v or direction < 0 and value < extreme_v:
            extreme_i, extreme_v = i, value
        if direction >= 0 and value < extreme_v - CORNER_SPEED_DROP:
            if direction > 0 or extreme_i:
                maxima.append(extreme_i)
            direction = -1
            extreme_i, extreme_v = i, value
        elif direction <= 0 and value > extreme_v + CORNER_SPEED_DROP:
            minima.append(extreme_i)
            direction = 1
            extreme_i, extreme_v = i, value
    if direction < 0:  # the lap ended under braking or on the exit from it
        minima.append(extreme_i)
    corners = []
    for number, minimum in enumerate(minima, 1):
        before = [m for m in maxima if m < minimum]
        after = [m for m in maxima if m > minimum]
        start = before[-1] * GRID_METRES if before and number > 1 else 0.0
        end = after[0] * GRID_METRES if after and number < len(minima) else float(length)
        corners.append(Corner(number, start, minimum * GRID_METRES, end, smooth[minimum]))
    return corners


def corner_metrics(view: LapView, corner: Corner) -> Optional[dict]:
    rows = view.between(corner.start, corner.end)
    if len(rows) < 3:
        return None
    t = view.trace
    near = [i for i in rows if abs(t.dist[i] - corner.apex) <= 150] or rows
    apex = min(near, key=lambda i: t.speed[i])
    apex_dist = t.dist[apex]
    before = [i for i in rows if i <= apex]
    after = [i for i in rows if i > apex]
    brake_i = next((i for i in before if t.brake[i] > BRAKE_ON), None)
    throttle_i = next((i for i in after if t.throttle[i] >= FULL_THROTTLE), None)
    braking = [i for i in rows if t.brake[i] > 0.1]
    power = [i for i in rows if t.throttle[i] > 0.2 and t.speed[i] > 30]
    lock_frames = sum(1 for i in braking if t.slip[i] > SLIP_THRESHOLD)
    spin_frames = sum(1 for i in power if t.slip[i] > SLIP_THRESHOLD)
    off_frames = sum(1 for i in rows if t.off_wheels[i] >= OFF_WHEELS)
    exit_dist = min(corner.end, apex_dist + 100.0)
    return {
        "time_ms": view.time_at(corner.end) - view.time_at(corner.start),
        "brake_dist": t.dist[brake_i] if brake_i is not None else None,
        "entry_speed": t.speed[brake_i] if brake_i is not None else None,
        "min_speed": t.speed[apex],
        "apex_dist": apex_dist,
        "throttle_dist": t.dist[throttle_i] if throttle_i is not None else None,
        "exit_speed": view.value_at(t.speed, exit_dist),
        "min_gear": min(t.gear[i] for i in rows),
        "lockup": lock_frames >= MIN_SLIP_FRAMES,
        "wheelspin": spin_frames >= MIN_SLIP_FRAMES,
        "off_track": off_frames >= MIN_SLIP_FRAMES,
        "x": t.x[apex], "z": t.z[apex],
        "traffic": any(0 < t.gap_ms[i] < TRAFFIC_GAP_MS for i in rows),
    }


def rival_corner_metrics(view: LapView, corner: Corner) -> Optional[dict]:
    """The same for a rival: Motion Ex and tyre data do not arrive for them."""
    rows = view.between(corner.start, corner.end)
    if len(rows) < 3:
        return None
    t = view.trace
    near = [i for i in rows if abs(t.dist[i] - corner.apex) <= 150] or rows
    apex = min(near, key=lambda i: t.speed[i])
    after = [i for i in rows if i > apex]
    brake_i = next((i for i in rows if i <= apex and t.brake[i] > BRAKE_ON), None)
    throttle_i = next((i for i in after if t.throttle[i] >= FULL_THROTTLE), None)
    exit_dist = min(corner.end, t.dist[apex] + 100.0)
    return {
        "time_ms": view.time_at(corner.end) - view.time_at(corner.start),
        "brake_dist": t.dist[brake_i] if brake_i is not None else None,
        "min_speed": t.speed[apex],
        "throttle_dist": t.dist[throttle_i] if throttle_i is not None else None,
        "exit_speed": view.value_at(t.speed, exit_dist),
        "x": t.x[apex], "z": t.z[apex],
    }


def lap_ranges(trace: Trace) -> Dict[int, Tuple[int, int]]:
    ranges: Dict[int, Tuple[int, int]] = {}
    laps = trace.lap
    start = 0
    for i in range(1, len(trace) + 1):
        if i == len(trace) or laps[i] != laps[start]:
            lap = int(laps[start])
            ranges[lap] = (ranges[lap][0], i) if lap in ranges else (start, i)
            start = i
    return ranges


def analyze(race: Race) -> dict:
    """Computes the report tables. All times in milliseconds."""
    trace = race.trace
    length = race.session.get("length", 0)
    history = race.history.get(race.player, {"laps": [], "stints": []})
    laps: List[dict] = []
    views: Dict[int, LapView] = {}
    ranges = lap_ranges(trace)
    flash_laps = Counter(item["lap"] for item in race.flashbacks)
    event_laps = Counter()
    for event in race.events:
        if event["code"] == "COLL" and race.player in event["cars"]:
            event_laps[event["lap"]] += 1

    for lap, (start, end) in sorted(ranges.items()):
        if lap == 0:
            continue
        official = history["laps"][lap - 1] if lap <= len(history["laps"]) else None
        ms = (official["ms"] if official else 0) or race.official_last_ms.get(lap, 0)
        if not ms and end - start < MIN_PARTIAL_LAP_FRAMES:
            continue  # a fragment: for example, frames after the finish line
        view = LapView(trace, start, end, ms)
        views[lap] = view
        row = lap_summary(race, lap, start, end, ms, official, length, view)
        row["flashbacks"] = flash_laps.get(lap, 0)
        row["collisions"] = event_laps.get(lap, 0)
        laps.append(row)

    completed = [row["ms"] for row in laps if row["ms"]]
    limit = statistics.median(completed) * 1.07 if completed else 0
    for row in laps:
        row["clean"] = bool(
            row["ms"] and row["valid"] and not row["pit"] and not row["sc"] and row["lap"] > 1 and row["covered"]
            and row["ms"] <= limit and not row["flashbacks"]
        )
    clean = [row for row in laps if row["clean"]]
    usable = clean or [row for row in laps if row["ms"] and row["covered"]]
    if not usable:
        return {"laps": laps, "clean": clean, "corners": [], "field": field_pace(race, set()), "views": views}

    best_row = min(usable, key=lambda row: row["ms"])
    corners = find_corners(views[best_row["lap"]], length) if length else []
    per_lap: Dict[Tuple[int, int], dict] = {}
    for row in laps:
        view = views[row["lap"]]
        if not view.dist:
            continue
        for corner in corners:
            metrics = corner_metrics(view, corner)
            if metrics:
                per_lap[row["lap"], corner.number] = metrics

    sc_laps = {row["lap"] for row in laps if row["sc"]}
    field = field_pace(race, sc_laps)
    for row in laps:
        reference = field["lap_median"].get(row["lap"])
        row["to_field_ms"] = row["ms"] - reference if row["ms"] and reference else None

    summaries = [corner_summary(corner, clean or usable, per_lap) for corner in corners]
    summaries = [item for item in summaries if item]
    comparisons = compare_with_rivals(race, views[best_row["lap"]], corners, per_lap, best_row, length)
    mistakes = find_mistakes(corners, laps, per_lap, summaries)
    return {
        "laps": laps, "clean": clean, "best": best_row, "corners": corners, "per_lap": per_lap, "summaries": summaries,
        "field": field, "comparisons": comparisons, "mistakes": mistakes, "views": views,
        "stints": stint_summary(race, laps), "slip": slip_calibration(trace),
    }


def lap_summary(race, lap, start, end, ms, official, length, view) -> dict:
    t = race.trace
    n = end - start
    mean = lambda column: sum(column[start:end]) / n
    peak = lambda column: max(column[start:end])
    last = end - 1
    surf = [t.surf_fl, t.surf_fr, t.surf_rl, t.surf_rr]
    inner = [t.inner_fl, t.inner_fr, t.inner_rl, t.inner_rr]
    mode_share = Counter(int(value) for value in t.ers_mode[start:end])
    mix_share = Counter(int(value) for value in t.fuel_mix[start:end])
    invalid = bool(t.lap_invalid_any[last])
    valid = official["valid"] if official and official["ms"] else not invalid
    return {
        "lap": lap, "ms": ms, "s1": official["s1"] if official else 0, "s2": official["s2"] if official else 0,
        "s3": official["s3"] if official else 0, "valid": valid, "position": int(t.position[last]),
        "compound": COMPOUNDS.get(int(t.compound[last]), "?"), "age": int(t.age[last]),
        "pit": any(t.pit[start:end]), "pit_lane_ms": peak(t.pit_lane_ms), "pit_stop_ms": peak(t.pit_stop_ms),
        "sc": any(t.sc[start:end]) and any(value in (1, 2) for value in t.sc[start:end]),
        "traffic_pct": round(100 * sum(1 for value in t.gap_ms[start:end] if 0 < value < TRAFFIC_GAP_MS) / n),
        "wear": (t.wear_fl[last], t.wear_fr[last], t.wear_rl[last], t.wear_rr[last]),
        "surface": tuple(round(mean(column)) for column in surf),
        "surface_peak": tuple(round(peak(column)) for column in surf),
        "inner": tuple(round(mean(column)) for column in inner),
        "brake_peak": (round(peak(t.brake_fl)), round(peak(t.brake_fr)), round(peak(t.brake_rl)), round(peak(t.brake_rr))),
        "ers_start": t.ers[start], "ers_end": t.ers[last], "deployed": t.deployed[last],
        "harvested": t.harv_k[last] + t.harv_h[last],
        "ers_modes": {ERS_MODES.get(mode, "?"): round(100 * count / n) for mode, count in mode_share.items()},
        "fuel": t.fuel[last], "fuel_laps": t.fuel_laps[last],
        "fuel_mix": {FUEL_MIXES.get(mix, "?"): round(100 * count / n) for mix, count in mix_share.items()},
        "warnings": int(t.warnings[last]), "cuts": int(t.cuts[last]), "penalties": int(t.penalties[last]),
        "damage": (int(t.wing_fl[last]), int(t.wing_fr[last]), int(t.wing_r[last]), int(t.floor[last])),
        "top_speed": round(peak(t.speed)), "covered": view.covers(length) if length else False,
    }


def corner_summary(corner: Corner, rows: Iterable[dict], per_lap) -> Optional[dict]:
    metrics = [(row["lap"], per_lap[row["lap"], corner.number]) for row in rows if (row["lap"], corner.number) in per_lap]
    if not metrics:
        return None
    ordered = sorted(metrics, key=lambda item: item[1]["time_ms"])
    times = [item[1]["time_ms"] for item in ordered]
    # Reference: the fastest 10% of passes. Cut corners don't get in here, only valid laps count as clean.
    baseline = statistics.fmean(times[:max(1, len(times) // 10)])
    median = statistics.median(times)
    third = max(1, len(ordered) // 3)
    fast, slow = ordered[:third], ordered[-third:]
    keys = ("brake_dist", "entry_speed", "min_speed", "throttle_dist", "exit_speed", "min_gear")
    return {
        "corner": corner, "laps": len(times), "best_ms": times[0], "median_ms": median, "baseline_ms": baseline,
        "loss_ms": statistics.fmean(times) - baseline, "total_loss_ms": sum(times) - baseline * len(times),
        "spread_ms": ordered[-1][1]["time_ms"] - times[0],
        "min_speed_ms": statistics.median(item[1]["min_speed"] for item in metrics),
        "apex_dist": statistics.median(item[1]["apex_dist"] for item in metrics),
        "fast": {key: median_of(item[1][key] for item in fast) for key in keys},
        "slow": {key: median_of(item[1][key] for item in slow) for key in keys},
        "fast_laps": [item[0] for item in fast], "slow_laps": [item[0] for item in slow],
        "lockups": [lap for lap, data in metrics if data["lockup"]],
        "wheelspins": [lap for lap, data in metrics if data["wheelspin"]],
        "offs": [lap for lap, data in metrics if data["off_track"]],
    }


def median_of(values: Iterable[Optional[float]]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return statistics.median(present) if present else None


def field_pace(race: Race, sc_laps: set) -> dict:
    """Pace of all cars from Session History: without pit stop laps, lap 1 and laps under the safety car."""
    rows = []
    by_lap: Dict[int, List[int]] = {}
    for car, data in race.history.items():
        stops = {stint[0] for stint in data["stints"] if stint[0] != 255}
        excluded = stops | {lap + 1 for lap in stops} | sc_laps | {1}
        times = []
        for lap, item in enumerate(data["laps"], 1):
            if item["ms"] and item["valid"] and lap not in excluded:
                times.append((lap, item["ms"]))
        if not times:
            continue
        median = statistics.median(ms for _, ms in times)
        kept = [(lap, ms) for lap, ms in times if ms <= median * 1.07]
        for lap, ms in kept:
            if car != race.player:
                by_lap.setdefault(lap, []).append(ms)
        rows.append({
            "car": car, "name": race.names.get(car, f"car {car}"), "median_ms": statistics.median(ms for _, ms in kept),
            "best_ms": min(ms for _, ms in kept), "laps": len(kept),
            "stops": len(stops), "stints": [COMPOUNDS.get(stint[2], "?") + (f"-L{stint[0]}" if stint[0] != 255 else "")
                                            for stint in data["stints"]],
        })
    rows.sort(key=lambda row: row["median_ms"])
    return {"rows": rows, "lap_median": {lap: statistics.median(values) for lap, values in by_lap.items()}}


def compare_with_rivals(race, player_view, corners, per_lap, best_row, length) -> List[dict]:
    """The player's best lap against the best laps of the fastest rival and the neighbour in the classification."""
    if not race.rival_best or not corners:
        return []
    fastest = min(race.rival_best, key=lambda car: race.rival_best[car]["ms"])
    picks = [("fastest rival", fastest)]
    own = race.final.get(race.player)
    if own and own["position"] > 1:
        ahead = next((car for car, item in race.final.items() if item["position"] == own["position"] - 1), None)
        if ahead in race.rival_best and ahead != fastest:
            picks.append(("car ahead at the finish", ahead))
    result = []
    for label, car in picks:
        best = race.rival_best[car]
        trace = best["trace"]
        view = LapView(trace, 0, len(trace), best["ms"])
        rows = []
        for corner in corners:
            mine = per_lap.get((best_row["lap"], corner.number))
            theirs = rival_corner_metrics(view, corner)
            if not mine or not theirs:
                continue
            rows.append({
                "corner": corner.number, "delta_ms": mine["time_ms"] - theirs["time_ms"],
                "brake_m": difference(mine["brake_dist"], theirs["brake_dist"]),
                "min_speed": mine["min_speed"] - theirs["min_speed"],
                "throttle_m": difference(mine["throttle_dist"], theirs["throttle_dist"]),
                "exit_speed": mine["exit_speed"] - theirs["exit_speed"],
                "line_m": math.hypot(mine["x"] - theirs["x"], mine["z"] - theirs["z"]),
            })
        result.append({"label": label, "car": car, "name": race.names.get(car, f"car {car}"), "ms": best["ms"],
                       "lap": best["lap"], "rows": rows})
    return result


def difference(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return a - b if a is not None and b is not None else None


def find_mistakes(corners, laps, per_lap, summaries) -> List[dict]:
    """One-off losses: a lap segment noticeably slower than usual for that segment."""
    medians = {item["corner"].number: item for item in summaries}
    flash = {row["lap"] for row in laps if row["flashbacks"]}
    found = []
    for row in laps:
        if row["pit"] or row["sc"] or row["lap"] == 1 or not row["ms"]:
            continue
        for corner in corners:
            metrics = per_lap.get((row["lap"], corner.number))
            usual = medians.get(corner.number)
            if not metrics or not usual:
                continue
            lost = metrics["time_ms"] - usual["median_ms"]
            if lost < 300:
                continue
            found.append({
                "lap": row["lap"], "corner": corner.number, "lost_ms": lost, "lockup": metrics["lockup"],
                "wheelspin": metrics["wheelspin"], "off_track": metrics["off_track"], "traffic": metrics["traffic"],
                "flashback": row["lap"] in flash, "min_speed": metrics["min_speed"] - (usual["fast"]["min_speed"] or 0),
                "brake_m": difference(metrics["brake_dist"], usual["fast"]["brake_dist"]),
            })
    found.sort(key=lambda item: -item["lost_ms"])
    return found


def stint_summary(race, laps) -> List[dict]:
    """Degradation: the slope of lap time and the slope of the deficit to the field, which does not depend on fuel burn."""
    stints, current = [], []
    for row in laps:
        # A new set: a different compound, or an age that does not continue the previous lap (used tyres are older than new ones)
        if current and (row["compound"] != current[-1]["compound"] or row["age"] not in (current[-1]["age"], current[-1]["age"] + 1)):
            stints.append(current)
            current = []
        current.append(row)
    if current:
        stints.append(current)
    result = []
    for rows in stints:
        clean = [row for row in rows if row["clean"]]
        relative = [row for row in clean if row.get("to_field_ms") is not None]
        result.append({
            "first": rows[0]["lap"], "last": rows[-1]["lap"], "compound": rows[-1]["compound"], "age": rows[-1]["age"],
            "clean": len(clean),
            "median_ms": statistics.median(row["ms"] for row in clean) if clean else None,
            "slope_ms": slope([(row["lap"], row["ms"]) for row in clean]),
            "field_slope_ms": slope([(row["lap"], row["to_field_ms"]) for row in relative]),
            "wear_per_lap": wear_rate(rows),
        })
    return result


def slope(points: Sequence[Tuple[float, float]]) -> Optional[float]:
    if len(points) < 3:
        return None
    mean_x = statistics.fmean(x for x, _ in points)
    mean_y = statistics.fmean(y for _, y in points)
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / spread if spread else None


def wear_rate(rows) -> Optional[Tuple[float, ...]]:
    if len(rows) < 2:
        return None
    count = rows[-1]["lap"] - rows[0]["lap"]
    return tuple((b - a) / count for a, b in zip(rows[0]["wear"], rows[-1]["wear"])) if count else None


def slip_calibration(trace: Trace) -> Optional[dict]:
    """Slip distribution: it shows whether the threshold is reasonable on real data."""
    values = sorted(trace.slip)
    if not values or values[-1] == 0:
        return None
    pick = lambda share: values[min(len(values) - 1, int(len(values) * share))]
    over = sum(1 for value in values if value > SLIP_THRESHOLD)
    return {"p50": pick(0.5), "p95": pick(0.95), "p99": pick(0.99), "max": values[-1], "over_pct": 100 * over / len(values)}


def lap_time(ms: Optional[float]) -> str:
    if not ms:
        return "-"
    ms = round(ms)
    return f"{ms // 60000}:{ms % 60000 / 1000:06.3f}"


def signed_s(ms: Optional[float]) -> str:
    return "-" if ms is None else f"{ms / 1000:+.3f}"


def signed(value: Optional[float], digits: int = 0) -> str:
    return "-" if value is None else f"{value:+.{digits}f}"


def plain(value: Optional[float], digits: int = 0) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def shares(items: Dict[str, int]) -> str:
    return " ".join(f"{name} {share}%" for name, share in sorted(items.items(), key=lambda item: -item[1]) if share >= 5)


def table(head: Sequence[str], rows: Iterable[Sequence]) -> List[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines + [""]


def car_name(race: Race, car: int) -> str:
    return "PLAYER" if car == race.player else race.names.get(car, f"car {car}")


def render(race: Race, result: dict) -> str:
    s = race.session
    laps = result["laps"]
    title = TRACKS.get(s.get("track"), f"track {s.get('track')}")
    kind = SESSION_TYPES.get(s.get("kind"), "?")
    started = datetime.datetime.fromtimestamp(race.first_wall).strftime("%Y-%m-%d %H:%M") if race.first_wall else "?"
    out = [f"# {title}, {kind}, {started}", ""]
    if not laps:
        out += ["No lap data in the recording.", ""]
        out += render_quality(race, result)
        return "\n".join(out)
    out += render_overview(race, result)
    out += render_laps(result)
    out += render_stints(result)
    out += render_field(race, result)
    out += render_corners(result)
    out += render_mistakes(result)
    out += render_rivals(result)
    out += render_setup(race, result)
    out += render_tyres(result)
    out += render_energy(result)
    out += render_pit(race, result)
    out += render_events(race)
    out += render_quality(race, result)
    return "\n".join(out)


def render_overview(race, result) -> List[str]:
    s = race.session
    laps = result["laps"]
    own = race.final.get(race.player)
    out = ["## Summary", ""]
    if own:
        out.append(f"- Result: P{own['position']} from grid P{own['grid']}, laps {own['laps']}, pit stops {own['stops']}, "
                   f"penalty {own['penalty_s']} s, best lap {lap_time(own['best_ms'])}.")
    else:
        out.append(f"- No final classification: the session is not finished or the bridge was closed earlier. Last position P{laps[-1]['position']}.")
    if s.get("kind") in RACE_SESSIONS and len(laps) >= 1:
        reaction = race.first_move - race.lights_out if race.first_move is not None and race.lights_out is not None else None
        grid = own["grid"] if own else None
        out.append(f"- Start: reaction {plain(reaction, 2)} s, after lap 1 P{laps[0]['position']}" + (f" (started from P{grid})." if grid else "."))
    if result.get("best"):
        best = result["best"]
        out.append(f"- Best clean lap: {lap_time(best['ms'])} on lap {best['lap']}. Clean laps: {len(result['clean'])} of {len(laps)}.")
    summaries = result.get("summaries") or []
    if summaries:
        ideal = sum(item["best_ms"] for item in summaries)
        out.append(f"- Sum of best segments: {lap_time(ideal)}. Corners found: {len(summaries)}, numbered in our own order, not the official numbers.")
    out.append(f"- Lap: {s.get('length')} m, total laps {s.get('total_laps')}, AI {s.get('ai')}, {'online' if s.get('network') else 'offline'}.")
    tc, abs_on = race.assists
    gearbox = {1: "manual", 2: "manual with suggested gear", 3: "automatic"}.get(s.get("gearbox"), "?")
    out.append(f"- Assists: TC {tc} (0 off, 1 medium, 2 full), ABS {abs_on}, gearbox {gearbox}, braking {s.get('braking_assist')}, "
               f"ERS {s.get('ers_assist')}, DRS {s.get('drs_assist')}, racing line {s.get('racing_line')}, equal cars {s.get('equal_cars')}.")
    if abs_on:
        out.append("- ABS is on: there should be no lock-ups, their absence in the report means nothing.")
    if tc:
        out.append("- TC is on: wheelspin is limited by the assist, its absence means nothing.")
    if race.setup:
        v = main_setup(race)
        out.append(f"- Setup (see the Setup section): wings {v[0]}/{v[1]}, diff {v[2]}/{v[3]}, brake pressure {v[14]}, brake bias {v[15]}, "
                   f"fuel {plain(v[22], 1)}.")
    weather = race.weather_log
    if weather:
        parts = [f"lap {lap}: {WEATHER.get(kind, '?')}, track {track}°, air {air}°" for lap, _, kind, track, air in weather[:8]]
        out.append("- Weather: " + "; ".join(parts) + ".")
    return out + [""]


def render_laps(result) -> List[str]:
    rows = []
    for row in result["laps"]:
        notes = []
        if not row["valid"]:
            notes.append("invalid")
        if row["pit"]:
            notes.append("pits")
        if row["sc"]:
            notes.append("SC")
        if row["flashbacks"]:
            notes.append(f"flashback x{row['flashbacks']}")
        if row["collisions"]:
            notes.append(f"contact x{row['collisions']}")
        rows.append((
            row["lap"], lap_time(row["ms"]), plain(row["s1"] / 1000, 3), plain(row["s2"] / 1000, 3), plain(row["s3"] / 1000, 3),
            f"P{row['position']}", f"{row['compound']}{row['age']}", signed_s(row.get("to_field_ms")), row["traffic_pct"],
            "yes" if row["clean"] else "", ", ".join(notes),
        ))
    return ["## Laps", "", '"To field": difference from the rivals\' median on this lap. "Traffic": share of the lap within 1 s of the car ahead, %.', ""] + table(
        ("Lap", "Time", "S1", "S2", "S3", "Pos.", "Tyres", "To field", "Traffic", "Clean", "Notes"), rows)


def render_stints(result) -> List[str]:
    if not result.get("stints"):
        return []
    rows = []
    for item in result["stints"]:
        wear = item["wear_per_lap"]
        rows.append((
            f"{item['first']}-{item['last']}", f"{item['compound']} to {item['age']} laps", item["clean"], lap_time(item["median_ms"]),
            signed(item["slope_ms"]), signed(item["field_slope_ms"]),
            " ".join(plain(value, 2) for value in wear) if wear else "-",
        ))
    return ["## Tyre stints", "", "The lap time slope is understated by fuel burn. The slope to the field does not have this flaw: "
            "above zero means you are losing to the rivals over the stint.", ""] + table(
        ("Laps", "Tyres", "Clean", "Median", "Slope, ms/lap", "To field, ms/lap", "Wear per lap FL FR RL RR, %"), rows)


def render_field(race, result) -> List[str]:
    rows = result["field"]["rows"]
    if not rows:
        return []
    cells = [(i, "**PLAYER**" if row["car"] == race.player else row["name"], lap_time(row["median_ms"]), lap_time(row["best_ms"]),
              row["laps"], " ".join(row["stints"])) for i, row in enumerate(rows, 1)]
    return ["## Field pace", "", "Clean laps of each car. Comparison with bots is approximate.", ""] + table(
        ("#", "Car", "Median", "Best", "Laps", "Tyres and stint end"), cells)


def render_corners(result) -> List[str]:
    summaries = result.get("summaries") or []
    if not summaries:
        return []
    ordered = sorted(summaries, key=lambda item: -item["loss_ms"])
    rows = []
    for item in ordered:
        c = item["corner"]
        rows.append((
            f"T{c.number}", f"{item['apex_dist']:.0f} m", f"{item['min_speed_ms']:.0f}", plain(item["median_ms"] / 1000, 3),
            f"{item['loss_ms'] / 1000:.3f}", f"{item['total_loss_ms'] / 1000:.2f}", f"{item['spread_ms'] / 1000:.3f}", list_laps(item["lockups"]), list_laps(item["wheelspins"]), list_laps(item["offs"]),
        ))
    out = ["## Corners: where time is lost", "",
           "Loss: average segment time minus the fastest 10% of passes, clean laps only. The most expensive corners are on top. "
           "Part of the loss is explained by fuel and tyres, so comparing corners with each other matters more.", ""]
    out += table(("Corner", "Apex", "Min km/h", "Median, s", "Loss per lap, s", "Per race, s", "Spread, s", "Lock-ups, laps", "Wheelspin, laps", "Offs, laps"), rows)
    out += ["### Fast and slow passes", "", "The best third of passes against the worst third: what changed. "
            "Braking and throttle: how many metres later (+) or earlier (-) in the slow passes.", ""]
    cells = []
    for item in ordered[:6]:
        fast, slow = item["fast"], item["slow"]
        cells.append((
            f"T{item['corner'].number}", signed(difference(slow["brake_dist"], fast["brake_dist"])),
            signed(difference(slow["entry_speed"], fast["entry_speed"])),
            signed(difference(slow["min_speed"], fast["min_speed"])), signed(difference(slow["throttle_dist"], fast["throttle_dist"])),
            signed(difference(slow["exit_speed"], fast["exit_speed"])), signed(difference(slow["min_gear"], fast["min_gear"])),
            list_laps(item["fast_laps"]), list_laps(item["slow_laps"]),
        ))
    return out + table(("Corner", "Brake, m", "Entry speed", "Min speed", "Full throttle, m", "Exit, km/h", "Gear", "Fast laps", "Slow laps"), cells)


def list_laps(laps: Sequence[int], limit: int = 8) -> str:
    if not laps:
        return ""
    text = ",".join(map(str, laps[:limit]))
    return text + (f" +{len(laps) - limit} more" if len(laps) > limit else "")


def render_mistakes(result) -> List[str]:
    found = result.get("mistakes") or []
    if not found:
        return []
    rows = []
    for item in found[:15]:
        marks = [name for key, name in (("lockup", "lock-up"), ("wheelspin", "wheelspin"), ("off_track", "off track"),
                                        ("traffic", "traffic"), ("flashback", "flashback")) if item[key]]
        rows.append((item["lap"], f"T{item['corner']}", f"{item['lost_ms'] / 1000:.3f}", signed(item["brake_m"]), signed(item["min_speed"]), ", ".join(marks)))
    return ["## One-off losses", "", f"More than 0.3 s against the usual pass, {len(found)} in total, the most expensive are shown. "
            "Braking and speed are relative to the fast passes.", ""] + table(
        ("Lap", "Corner", "Loss, s", "Brake, m", "Min speed", "What else happened"), rows)


def render_rivals(result) -> List[str]:
    out = []
    best = result.get("best")
    for item in result.get("comparisons") or []:
        out += [f"## Comparison: {item['label']}, {item['name']}", "",
                f"Your best lap {lap_time(best['ms'])} (lap {best['lap']}) against {lap_time(item['ms'])} (lap {item['lap']}). "
                "A plus in time means you are slower. Fuel load and tyres may differ.", ""]
        rows = [(f"T{row['corner']}", signed_s(row["delta_ms"]), signed(row["brake_m"]), signed(row["min_speed"]), signed(row["throttle_m"]),
                 signed(row["exit_speed"]), plain(row["line_m"], 1)) for row in sorted(item["rows"], key=lambda r: -r["delta_ms"])]
        out += table(("Corner", "Time, s", "Brake, m", "Min speed", "Full throttle, m", "Exit, km/h", "Apex offset, m"), rows)
    return out


def setup_named(setup: Optional[Sequence]) -> Optional[dict]:
    """The player's setup as {field: value}, see SETUP_FIELDS."""
    if not setup:
        return None
    return {name: round(value, 2) if isinstance(value, float) else value for name, value in zip(SETUP_FIELDS, setup)}


def setup_periods(race: Race) -> List[dict]:
    """Each setup the car had and how long, with the lap and metre where it started. The first one is often the garage
    default; the differential and the brake bias are changed on the move, the front wing at a pit stop."""
    if not race.setup_log:
        return []
    trace = race.trace
    times = trace.t
    end = times[-1] if len(trace) else race.setup_log[-1][0]
    periods = []
    for i, (start, setup) in enumerate(race.setup_log):
        stop = race.setup_log[i + 1][0] if i + 1 < len(race.setup_log) else max(end, start)
        k = bisect.bisect_left(times, start) if len(trace) else 0
        on_track = len(trace) and k < len(trace) and start >= times[0]
        lo, hi = (bisect.bisect_left(times, start), bisect.bisect_left(times, stop)) if len(trace) else (0, 0)
        moving = sum(1 for j in range(lo, hi) if trace.speed[j] > 90)  # above the pit lane limit
        periods.append({"start": start, "seconds": max(stop - start, 0.0), "moving_frames": moving, "setup": setup,
                        "lap": int(trace.lap[k]) if on_track else None, "dist": round(trace.dist[k]) if on_track else None})
    return periods


def main_setup(race: Race) -> Optional[Tuple]:
    """The setup the car was driven with longest (time in the garage does not count)."""
    periods = setup_periods(race)
    key = "moving_frames" if any(period["moving_frames"] for period in periods) else "seconds"
    totals: Dict[Tuple, float] = {}
    for period in periods:
        totals[period["setup"]] = totals.get(period["setup"], 0.0) + period[key]
    return max(totals, key=totals.get) if totals else race.setup


def speed_trap(race: Race, result: dict) -> Optional[dict]:
    """Top speed on the player's best lap against the top speed on the rivals' best laps."""
    best = result.get("best")
    if not best or not race.rival_best:
        return None
    rivals = sorted(((max(item["trace"].speed), car) for car, item in race.rival_best.items() if len(item["trace"])), reverse=True)
    if not rivals:
        return None
    return {"mine": best["top_speed"], "median": statistics.median(speed for speed, _ in rivals),
            "max": rivals[0][0], "max_car": rivals[0][1], "count": len(rivals)}


def render_setup(race: Race, result: dict) -> List[str]:
    v = setup_named(main_setup(race))
    if not v:
        return []
    def pair(a, b, digits=0):
        return f"{plain(v[a], digits)} / {plain(v[b], digits)}"
    rows = [
        ("Wings, front / rear", pair("front_wing", "rear_wing")),
        ("Differential, on / off throttle, %", pair("diff_on_throttle", "diff_off_throttle")),
        ("Camber, front / rear", pair("front_camber", "rear_camber", 2)),
        ("Toe, front / rear", pair("front_toe", "rear_toe", 2)),
        ("Suspension, front / rear", pair("front_suspension", "rear_suspension")),
        ("Anti-roll bar, front / rear", pair("front_anti_roll_bar", "rear_anti_roll_bar")),
        ("Ride height, front / rear", pair("front_ride_height", "rear_ride_height")),
        ("Brake pressure, %", plain(v["brake_pressure"])),
        ("Brake bias, % front", plain(v["brake_bias"])),
        ("Engine braking, %", plain(v["engine_braking"])),
        ("Tyre pressure front, psi (FL / FR)", pair("pressure_fl", "pressure_fr", 1)),
        ("Tyre pressure rear, psi (RL / RR)", pair("pressure_rl", "pressure_rr", 1)),
        ("Ballast", plain(v["ballast"])),
        ("Fuel, kg", plain(v["fuel"], 1)),
    ]
    out = ["## Setup", "", "The setup the car was driven with longest (garage time does not count). Compare sessions with `tools/setup_history.py`.", ""]
    out += table(("Parameter", "Value"), rows)
    periods = setup_periods(race)
    if len(periods) > 1:
        out += ["", "Changes during the session (the differential and the brake bias change on the move, the front wing at a pit stop):", ""]
        change_rows = []
        for before, after in zip(periods, periods[1:]):
            old, new = setup_named(before["setup"]), setup_named(after["setup"])
            changed = ", ".join(f"{name} {plain(old[name], 2 if isinstance(old[name], float) else 0)} → "
                                f"{plain(new[name], 2 if isinstance(new[name], float) else 0)}" for name in SETUP_FIELDS if old[name] != new[name])
            where = f"lap {after['lap']}, {after['dist']} m" if after["lap"] is not None else "before driving"
            change_rows.append((f"{after['start']:.0f}", where, changed, f"{after['seconds']:.0f}"))
        out += table(("Time, s", "Where", "Changed", "Kept for, s"), change_rows)
    parc = race.session.get("parc_ferme")
    if parc is not None:
        out += ["", f"- Parc ferme rules: {'on' if parc else 'off'}."]
    trap = speed_trap(race, result)
    if trap:
        out.append(f"- Top speed on your best lap {trap['mine']} km/h; on the rivals' best laps: median {trap['median']:.0f}, "
                   f"maximum {trap['max']:.0f} ({car_name(race, trap['max_car'])}), {trap['count']} cars. "
                   "A single best lap is noisy: a lap in clean air has no DRS or slipstream. A gap of 8 km/h or more that repeats in "
                   "qualifying and on the clean race laps (Vmax in the ERS table) points to wings or ERS rather than driving.")
    out.append("")
    return out


def render_tyres(result) -> List[str]:
    rows = [(row["lap"], f"{row['compound']}{row['age']}", " ".join(plain(value, 1) for value in row["wear"]),
             " ".join(map(str, row["surface"])), " ".join(map(str, row["surface_peak"])), " ".join(map(str, row["inner"])),
             " ".join(map(str, row["brake_peak"]))) for row in result["laps"]]
    return ["## Tyres and brakes", "", "Wheel order FL FR RL RR. Wear at the end of the lap, temperatures in °C.", ""] + table(
        ("Lap", "Tyres", "Wear, %", "Surface, mean", "Surface, peak", "Inner, mean", "Brakes, peak"), rows)


def render_energy(result) -> List[str]:
    rows = []
    for row in result["laps"]:
        rows.append((
            row["lap"], f"{100 * row['ers_start'] / ERS_MAX_JOULES:.0f}", f"{100 * row['ers_end'] / ERS_MAX_JOULES:.0f}",
            plain(row["deployed"] / 1e6, 2), plain(row["harvested"] / 1e6, 2), shares(row["ers_modes"]), plain(row["fuel"], 1),
            signed(row["fuel_laps"], 2), shares(row["fuel_mix"]), row["top_speed"],
        ))
    return ["## ERS and fuel", "", "Fuel: kg and margin in laps at the end of the lap, as on the MFD.", ""] + table(
        ("Lap", "ERS start, %", "ERS end, %", "Deployed, MJ", "Harvested, MJ", "Modes", "Fuel", "Margin", "Mix", "Vmax"), rows)


def render_pit(race, result) -> List[str]:
    stops = [row for row in result["laps"] if row["pit"]]
    if not stops:
        return []
    by_lap = {row["lap"]: row for row in result["laps"]}
    rows = []
    for row in stops:
        before = by_lap.get(row["lap"] - 1)
        after = by_lap.get(row["lap"] + 2)
        rows.append((row["lap"], plain(row["pit_lane_ms"] / 1000, 1), plain(row["pit_stop_ms"] / 1000, 1),
                     f"P{before['position']}" if before else "-", f"P{after['position']}" if after else "-"))
    ideal, latest = race.pit_window
    note = f"The game's window at the end of the recording: ideal lap {ideal}, latest {latest}. Both the in-lap and the out-lap may be marked." if ideal else ""
    return ["## Pit stops", "", note, ""] + table(("Lap", "In pit lane, s", "Stationary, s", "Before", "2 laps later"), rows)


def render_events(race) -> List[str]:
    rows = []
    player = race.player
    for item in race.flashbacks:
        erased = [event["code"] for event in item["erased"] if event["code"] in ("COLL", "PENA", "RTMT")]
        rows.append((item["lap"], plain(item["dist"]), "Flashback", f"back by {item['seconds']:.1f} s, lap {item['to_lap']}, {item['to_dist']:.0f} m"
                     + (f", undone: {','.join(erased)}" if erased else "")))
    for event in race.events:
        code = event["code"]
        text = None
        if code == "COLL" and player in event["cars"]:
            other = event["cars"][0] if event["cars"][1] == player else event["cars"][1]
            text = ("Contact", car_name(race, other))
        elif code == "OVTK" and player in event["cars"]:
            first, second = event["cars"]
            text = ("Overtake", f"you overtook {car_name(race, second)}") if first == player else ("Overtake", f"{car_name(race, first)} overtook you")
        elif code == "PENA" and event["car"] == player:
            text = ("Penalty", f"{PENALTY_TYPES.get(event['penalty'], event['penalty'])}: {INFRINGEMENTS.get(event['infringement'], event['infringement'])}, {event['seconds']} s")
        elif code == "SCAR":
            kinds = {0: "none", 1: "SC", 2: "VSC", 3: "formation lap SC"}
            stages = {0: "deployed", 1: "returning", 2: "returned", 3: "resume race"}
            text = ("Safety car", f"{kinds.get(event['sc_type'], '?')} {stages.get(event['sc_event'], '?')}")
        elif code == "RDFL":
            text = ("Red flag", "")
        elif code == "RTMT":
            text = ("Retirement", car_name(race, event["car"]))
        elif code == "CHQF":
            text = ("Chequered flag", "")
        if text:
            rows.append((event["lap"], plain(event["dist"]), *text))
    rows.sort(key=lambda row: (row[0], float(row[1]) if row[1] != "-" else 0))
    return (["## Events", ""] + table(("Lap", "Metre", "Event", "Details"), rows)) if rows else []


def render_quality(race, result) -> List[str]:
    duration = (race.last_wall - race.first_wall) if race.first_wall else 0
    out = ["## Data quality", "",
           f"- File: `{race.path.name if race.path else '?'}`, recording {duration / 60:.1f} min, player frames {race.frames}, with Motion Ex {race.motion_ex_frames}.",
           f"- Time gaps longer than {GAP_SECONDS} s: {race.gap_count}, {race.gap_seconds:.1f} s in total.",
           f"- Flashbacks: {len(race.flashbacks)}. Packets of a foreign format: {race.wrong_format}.",
           f"- ERS: maximum in the recording {race.ers_peak / 1e6:.2f} MJ, percentages are computed from {ERS_MAX_JOULES / 1e6:.0f} MJ."]
    slip = result.get("slip")
    if slip:
        out.append(f"- Slip (maximum over the wheels): median {slip['p50']:.3f}, 95% {slip['p95']:.3f}, 99% {slip['p99']:.3f}, "
                   f"maximum {slip['max']:.2f}; above the threshold {SLIP_THRESHOLD} {slip['over_pct']:.1f}% of frames. The threshold was not verified in the game.")
    else:
        out.append("- No slip data: Motion Ex did not arrive, lock-ups and wheelspin are not detected.")
    missing = [name for pid, name in ((PID_HISTORY, "Session History"), (PID_FINAL, "Final Classification"), (PID_SETUPS, "Car Setups"),
                                      (PID_MOTION, "Motion"), (PID_MOTION_EX, "Motion Ex")) if not race.packets[pid]]
    if missing:
        out.append("- Packets that did not arrive: " + ", ".join(missing) + ".")
    return out + [""]


LAP_CSV = ("lap", "ms", "s1", "s2", "s3", "valid", "clean", "position", "compound", "age", "pit", "sc", "traffic_pct", "to_field_ms",
           "ers_start", "ers_end", "deployed", "harvested", "fuel", "fuel_laps", "warnings", "cuts", "penalties", "flashbacks",
           "collisions", "top_speed", "pit_lane_ms", "pit_stop_ms")
WHEEL_COLUMNS = ("wear", "surface", "surface_peak", "inner", "brake_peak")
WHEELS = ("fl", "fr", "rl", "rr")
CORNER_CSV = ("time_ms", "brake_dist", "entry_speed", "min_speed", "apex_dist", "throttle_dist", "exit_speed", "min_gear",
              "lockup", "wheelspin", "off_track", "traffic", "x", "z")


def write_csv(result: dict, directory: pathlib.Path) -> None:
    with open(directory / "laps.csv", "w", newline="", encoding="utf-8") as target:
        writer = csv.writer(target)
        writer.writerow([*LAP_CSV, *(f"{name}_{wheel}" for name in WHEEL_COLUMNS for wheel in WHEELS)])
        for row in result["laps"]:
            writer.writerow([*(row.get(name) for name in LAP_CSV), *(value for name in WHEEL_COLUMNS for value in row[name])])
    with open(directory / "corners.csv", "w", newline="", encoding="utf-8") as target:
        writer = csv.writer(target)
        writer.writerow(("lap", "corner", *CORNER_CSV))
        for (lap, number), metrics in sorted(result.get("per_lap", {}).items()):
            writer.writerow((lap, number, *(metrics[name] for name in CORNER_CSV)))


def peek_session(path) -> Optional[dict]:
    """Session type and track from the first Session packet, without reading the whole file."""
    for _, data in read_packets(path):
        if len(data) == SIZES[PID_SESSION] and HEADER.unpack_from(data)[5] == PID_SESSION:
            o = HEADER.size
            return {"kind": data[o + 6], "track": struct.unpack_from("<b", data, o + 7)[0]}
    return None


def recordings(directory: pathlib.Path) -> List[pathlib.Path]:
    return sorted(directory.glob("*.f1rec.gz"), key=lambda path: path.stat().st_mtime, reverse=True)


def choose(directory: pathlib.Path, any_session: bool) -> Optional[pathlib.Path]:
    for path in recordings(directory):
        info = peek_session(path)
        if info and (any_session or info["kind"] in RACE_SESSIONS):
            return path
    return None


def report_name(race: Race, path: pathlib.Path) -> str:
    """Folder name: track_sessionType_date_HH-MM, for example Las_Vegas_race_2026-11-15_21-30."""
    s = race.session
    track = TRACKS.get(s.get("track"), f"track_{s.get('track')}")
    track = re.sub(r"[^\w-]+", "_", track).strip("_")
    kind = s.get("kind")
    if kind in RACE_SESSIONS:
        session = "race"
    elif kind == 18:
        session = "time_trial"
    elif kind in (1, 2, 3, 4):
        session = "practice"
    elif kind in range(5, 15):
        session = "qualifying"
    else:
        session = "unknown"
    if race.first_wall:
        date = datetime.datetime.fromtimestamp(race.first_wall).strftime("%Y-%m-%d_%H-%M")
    else:
        date = path.name[:16]  # recording names start with YYYY-MM-DD_HH-MM-SS
    return f"{track}_{session}_{date}"


def build(path, directory: Optional[pathlib.Path] = None) -> pathlib.Path:
    path = pathlib.Path(path)
    race = load(path)
    result = analyze(race)
    target = directory or (path.parent / "reports" / report_name(race, path))
    target.mkdir(parents=True, exist_ok=True)
    (target / "report.md").write_text(render(race, result), encoding="utf-8")
    write_csv(result, target)
    return target


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Report on an F1 25 session recording")
    parser.add_argument("path", nargs="?", help=".f1rec.gz file, the last race by default")
    parser.add_argument("--any", action="store_true", help="the last session of any type, not only a race")
    parser.add_argument("--list", action="store_true", help="list recordings")
    parser.add_argument("--dir", default=str(DEFAULT_DIR), help="recordings folder")
    args = parser.parse_args()
    directory = pathlib.Path(args.dir)
    if args.list:
        for path in recordings(directory):
            info = peek_session(path) or {}
            print(f"{path.name}  {path.stat().st_size / 1e6:8.1f} MB  {SESSION_TYPES.get(info.get('kind'), '?'):>14}  "
                  f"{TRACKS.get(info.get('track'), '?')}")
        return
    path = args.path or choose(directory, args.any)
    if path is None:
        raise SystemExit(f"No suitable recordings in {directory}. Run f1_bridge.py and drive a session; for a non-race session add --any.")
    print(f"Analysing {path}")
    print(f"Done: {build(path) / 'report.md'}")


if __name__ == "__main__":
    main()
