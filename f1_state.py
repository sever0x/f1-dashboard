"""Car state for the web dashboard, assembled from F1 25 UDP packets (format 2025).

The car is the same one the Nano shows: the player, or the spectated car in spectator mode. Wheels are always ordered
FL, FR, RL, RR (the game sends RL, RR, FL, FR). Fields follow the EA F1 25 UDP specification v3, little-endian, packed.

Standard library only. Thread-safe: update() runs on the receiving thread, snapshot() on the web server threads.
"""
from __future__ import annotations

import struct
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Sequence

FORMAT = 2025
CARS = 22
HEADER = struct.Struct("<HBBBBBQfIIBB")  # 29 bytes
H_FORMAT, H_PACKET_ID, H_SESSION_UID, H_SESSION_TIME, H_PLAYER = 0, 5, 6, 7, 10

PID_SESSION, PID_LAP, PID_EVENT = 1, 2, 3
PID_TELEMETRY, PID_STATUS, PID_DAMAGE, PID_TYRE_SETS = 6, 7, 10, 12
SIZES = {PID_SESSION: 753, PID_LAP: 1285, PID_EVENT: 45, PID_TELEMETRY: 1352, PID_STATUS: 1239, PID_DAMAGE: 1041,
         PID_TYRE_SETS: 231}

LAP = struct.Struct("<IIHBHBHBHBfff15BHHBfB")  # 57
TELEMETRY = struct.Struct("<HfffBbHBBH4H4B4BH4f4B")  # 60
STATUS = struct.Struct("<BBBBBfffHHBBHBBBbfffBfffB")  # 55
DAMAGE = struct.Struct("<4f4B4B4B18B")  # 46
TYRE_SET = struct.Struct("<7BhB")  # 10
FORECAST = struct.Struct("<BBBbbbbB")  # 8

# Session: offsets from the end of the header
SESSION_HEAD = struct.Struct("<BbbBHBbBHHBBBB")  # m_weather ... m_spectatorCarIndex
SESSION_SAFETY_CAR_OFFSET = 124
SESSION_FORECAST_COUNT_OFFSET = 126
SESSION_FORECAST_OFFSET = 127
SESSION_FORECAST_MAX = 64
SESSION_PIT_WINDOW_OFFSET = 653  # ideal lap, latest lap, rejoin position

PENALTY = struct.Struct("<7B")

TRACKS = {
    0: "Melbourne", 2: "Shanghai", 3: "Sakhir", 4: "Catalunya", 5: "Monaco", 6: "Montreal", 7: "Silverstone",
    9: "Hungaroring", 10: "Spa", 11: "Monza", 12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "Texas",
    16: "Brazil", 17: "Austria", 19: "Mexico", 20: "Baku", 26: "Zandvoort", 27: "Imola", 29: "Jeddah", 30: "Miami",
    31: "Las Vegas", 32: "Losail", 39: "Silverstone (Reverse)", 40: "Austria (Reverse)", 41: "Zandvoort (Reverse)",
}
SESSION_TYPES = {
    0: "", 1: "P1", 2: "P2", 3: "P3", 4: "Short Practice", 5: "Q1", 6: "Q2", 7: "Q3", 8: "Short Q",
    9: "One-Shot Q", 10: "SQ1", 11: "SQ2", 12: "SQ3", 13: "Short SQ", 14: "One-Shot SQ", 15: "Race", 16: "Race 2",
    17: "Race 3", 18: "Time Trial",
}
WEATHER = {0: "Clear", 1: "Light cloud", 2: "Overcast", 3: "Light rain", 4: "Heavy rain", 5: "Storm"}
VISUAL_COMPOUNDS = {16: "S", 17: "M", 18: "H", 7: "I", 8: "W", 9: "D", 10: "W", 15: "W", 19: "U", 20: "S", 21: "M", 22: "H"}
ACTUAL_COMPOUNDS = {16: "C5", 17: "C4", 18: "C3", 19: "C2", 20: "C1", 21: "C0", 22: "C6", 7: "Inter", 8: "Wet",
                    9: "Dry", 10: "Wet", 11: "Super Soft", 12: "Soft", 13: "Medium", 14: "Hard", 15: "Wet"}
PIT_STATUS = {0: "", 1: "Pitting", 2: "In pit area"}
PENALTY_TYPES = {
    0: "Drive-through", 1: "Stop & Go", 2: "Grid penalty", 3: "Penalty reminder", 4: "Time penalty", 5: "Warning",
    6: "Disqualified", 7: "Removed from formation lap", 8: "Parked too long", 9: "Tyre regulations",
    10: "Lap invalidated", 11: "This and next lap invalidated", 12: "Lap invalidated",
    13: "This and next lap invalidated", 14: "This and previous lap invalidated",
    15: "This and previous lap invalidated", 16: "Retired", 17: "Black flag timer",
}
INFRINGEMENTS = {
    0: "Blocking by slow driving", 1: "Blocking by wrong way driving", 2: "Reversing off the start line",
    3: "Big collision", 4: "Small collision", 5: "Failed to hand back position", 6: "Failed to hand back positions",
    7: "Corner cutting, time gained", 8: "Corner cutting overtake", 9: "Corner cutting overtakes",
    10: "Crossed pit exit lane", 11: "Ignoring blue flags", 12: "Ignoring yellow flags", 13: "Ignoring drive-through",
    14: "Too many drive-throughs", 15: "Drive-through reminder", 16: "Drive-through reminder",
    17: "Pit lane speeding", 18: "Parked too long", 19: "Ignoring tyre regulations", 20: "Too many penalties",
    21: "Multiple warnings", 22: "Approaching disqualification", 23: "Tyre regulations", 24: "Tyre regulations",
    25: "Lap invalidated, corner cutting", 26: "Lap invalidated, running wide", 27: "Ran wide, minor time gain",
    28: "Ran wide, significant time gain", 29: "Ran wide, extreme time gain", 30: "Lap invalidated, wall riding",
    31: "Lap invalidated, flashback", 32: "Lap invalidated, reset to track", 33: "Blocking the pit lane",
    34: "Jump start", 35: "Safety car collision", 36: "Safety car illegal overtake", 37: "Safety car pace exceeded",
}
# Penalties that must be served or add time; the rest (invalidated laps) do not belong in the list.
NOTABLE_PENALTIES = {0, 1, 2, 4, 5, 6, 8, 9, 17}
PENALTY_EVENTS_KEPT = 12
SERVED_EVENTS = {b"DTSV": "Drive-through served", b"SGSV": "Stop & Go served"}

PU_PARTS = ("mguh", "es", "ce", "ice", "mguk", "tc")


def front_first(values: Sequence) -> List:
    """RL, RR, FL, FR -> FL, FR, RL, RR."""
    return [values[2], values[3], values[0], values[1]]


class DashboardState:
    """Latest known state of one car. Packets of another format or size are skipped."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.mode = "live"
        self._reset_session(None)
        self._last_packet: Optional[float] = None
        self._wrong_format: Optional[int] = None

    # --- input ---

    def update(self, data: bytes) -> None:
        if len(data) < HEADER.size:
            return
        header = HEADER.unpack_from(data)
        with self._lock:
            self._last_packet = self._clock()
            if header[H_FORMAT] != FORMAT:
                self._wrong_format = header[H_FORMAT]
                return
            self._wrong_format = None
            if header[H_SESSION_UID] != self._session_uid:
                self._reset_session(header[H_SESSION_UID])
            pid = header[H_PACKET_ID]
            if SIZES.get(pid) != len(data):
                return
            self._player = header[H_PLAYER]
            if pid == PID_SESSION:
                self._read_session(data)
                return
            car = self.car_index()
            if car is None:
                return
            if pid == PID_TELEMETRY:
                self._read_telemetry(data, car)
            elif pid == PID_STATUS:
                self._read_status(data, car)
            elif pid == PID_DAMAGE:
                self._read_damage(data, car)
            elif pid == PID_LAP:
                self._read_lap(data, car)
            elif pid == PID_EVENT:
                self._read_event(data, car, header[H_SESSION_TIME])
            elif pid == PID_TYRE_SETS:
                self._read_tyre_sets(data, car)

    def car_index(self) -> Optional[int]:
        car = self._spectated if self._spectated is not None else self._player
        return car if car is not None and car < CARS else None

    def _reset_session(self, uid: Optional[int]) -> None:
        self._session_uid = uid
        self._player: Optional[int] = None
        self._spectated: Optional[int] = None
        self._session: Optional[dict] = None
        self._pit_window: Optional[dict] = None
        self._telemetry: Optional[dict] = None
        self._status: Optional[dict] = None
        self._damage: Optional[dict] = None
        self._lap: Optional[dict] = None
        self._tyre_sets: Optional[dict] = None
        self._events: deque = deque(maxlen=PENALTY_EVENTS_KEPT)

    def _read_session(self, data: bytes) -> None:
        base = HEADER.size
        (weather, track_temp, air_temp, total_laps, _length, session_type, track, _formula, time_left, _duration,
         _pit_speed, _paused, spectating, spectator_car) = SESSION_HEAD.unpack_from(data, base)
        self._spectated = spectator_car if spectating else None
        forecast = []
        count = min(data[base + SESSION_FORECAST_COUNT_OFFSET], SESSION_FORECAST_MAX)
        for i in range(count):
            f_type, offset, f_weather, f_track, _, f_air, _, rain = FORECAST.unpack_from(
                data, base + SESSION_FORECAST_OFFSET + i * FORECAST.size)
            if f_type == session_type:
                forecast.append({"minutes": offset, "weather": WEATHER.get(f_weather, "?"), "rain": rain,
                                 "trackTemp": f_track, "airTemp": f_air})
        ideal, latest, rejoin = data[base + SESSION_PIT_WINDOW_OFFSET:base + SESSION_PIT_WINDOW_OFFSET + 3]
        self._session = {
            "type": SESSION_TYPES.get(session_type, ""), "track": TRACKS.get(track, f"Track {track}"),
            "totalLaps": total_laps, "weather": WEATHER.get(weather, "?"), "trackTemp": track_temp,
            "airTemp": air_temp, "timeLeft": time_left, "safetyCar": data[base + SESSION_SAFETY_CAR_OFFSET],
            "spectating": bool(spectating), "forecast": forecast[:6],
        }
        if not spectating:  # the pit window in the packet belongs to the player only
            self._pit_window = {"idealLap": ideal, "latestLap": latest, "rejoinPosition": rejoin}

    def _read_telemetry(self, data: bytes, car: int) -> None:
        v = TELEMETRY.unpack_from(data, HEADER.size + car * TELEMETRY.size)
        self._telemetry = {
            "speed": v[0], "gear": v[5], "rpm": v[6],
            "brakes": front_first(v[10:14]), "surface": front_first(v[14:18]), "inner": front_first(v[18:22]),
            "engine": v[22], "pressure": [round(p, 1) for p in front_first(v[23:27])],
        }

    def _read_status(self, data: bytes, car: int) -> None:
        v = STATUS.unpack_from(data, HEADER.size + car * STATUS.size)
        self._status = {
            "fuelKg": round(v[5], 2), "fuelCapacity": round(v[6], 1), "fuelLaps": round(v[7], 2),
            "actual": ACTUAL_COMPOUNDS.get(v[13], ""), "visual": VISUAL_COMPOUNDS.get(v[14], "?"), "age": v[15],
            "ersJoules": v[19],
        }

    def _read_damage(self, data: bytes, car: int) -> None:
        v = DAMAGE.unpack_from(data, HEADER.size + car * DAMAGE.size)
        parts = v[16:]
        self._damage = {
            "tyreWear": [round(min(100.0, max(0.0, w)), 1) for w in front_first(v[0:4])],
            "tyreDamage": front_first(v[4:8]), "brakeDamage": front_first(v[8:12]), "blisters": front_first(v[12:16]),
            "frontLeftWing": parts[0], "frontRightWing": parts[1], "rearWing": parts[2], "floor": parts[3],
            "diffuser": parts[4], "sidepod": parts[5], "drsFault": bool(parts[6]), "ersFault": bool(parts[7]),
            "gearbox": parts[8], "engine": parts[9], "pu": dict(zip(PU_PARTS, parts[10:16])),
            "engineBlown": bool(parts[16]), "engineSeized": bool(parts[17]),
        }

    def _read_lap(self, data: bytes, car: int) -> None:
        v = LAP.unpack_from(data, HEADER.size + car * LAP.size)
        (position, lap, pit_status, pit_stops, _sector, invalid, penalties, warnings, cuts, drive_through, stop_go,
         _grid, _driver, result, pit_timer) = v[13:28]
        self._lap = {
            "position": position, "lap": lap, "pitStatus": PIT_STATUS.get(pit_status, ""), "pitStops": pit_stops,
            "invalid": bool(invalid), "penaltySeconds": penalties, "warnings": warnings, "cornerCutting": cuts,
            "driveThrough": drive_through, "stopGo": stop_go, "resultStatus": result,
            "pitLaneActive": bool(pit_timer), "pitLaneMs": v[28], "pitStopMs": v[29], "servePenaltyAtStop": bool(v[30]),
        }

    def _read_event(self, data: bytes, car: int, session_time: float) -> None:
        code = data[HEADER.size:HEADER.size + 4]
        details = HEADER.size + 4
        lap = self._lap["lap"] if self._lap else 0
        if code == b"PENA":
            # The spec describes the time field as time gained, not the length of the penalty, so it is not shown:
            # the total of time penalties comes in Lap Data.
            p_type, infringement, vehicle, _other, _time, p_lap, _gained = PENALTY.unpack_from(data, details)
            if vehicle != car or p_type not in NOTABLE_PENALTIES:
                return
            self._events.appendleft({
                "kind": "penalty", "type": PENALTY_TYPES.get(p_type, f"Penalty {p_type}"), "typeId": p_type,
                "reason": INFRINGEMENTS.get(infringement, f"Infringement {infringement}"),
                "lap": p_lap, "time": round(session_time, 1),
            })
        elif code in SERVED_EVENTS and data[details] == car:
            self._events.appendleft({"kind": "served", "type": SERVED_EVENTS[code], "reason": "",
                                     "lap": lap, "time": round(session_time, 1)})

    def _read_tyre_sets(self, data: bytes, car: int) -> None:
        base = HEADER.size
        if data[base] != car:
            return
        sets = []
        for i in range(20):
            actual, visual, wear, available, _recommended, life_span, usable, delta, fitted = TYRE_SET.unpack_from(
                data, base + 1 + i * TYRE_SET.size)
            sets.append({"index": i, "actual": ACTUAL_COMPOUNDS.get(actual, ""), "visual": VISUAL_COMPOUNDS.get(visual, "?"),
                         "wear": wear, "available": bool(available), "lifeSpan": life_span, "usableLife": usable,
                         "deltaMs": delta, "fitted": bool(fitted)})
        self._tyre_sets = {"sets": sets, "fitted": data[base + 1 + 20 * TYRE_SET.size]}

    # --- output ---

    def snapshot(self) -> Dict:
        with self._lock:
            age = None if self._last_packet is None else round(self._clock() - self._last_packet, 1)
            return {
                "mode": self.mode, "packetAge": age, "wrongFormat": self._wrong_format, "car": self.car_index(),
                "session": self._session, "pitWindow": self._pit_window, "telemetry": self._telemetry,
                "status": self._status, "damage": self._damage, "lap": self._lap, "tyreSets": self._tyre_sets,
                "events": list(self._events),
            }
