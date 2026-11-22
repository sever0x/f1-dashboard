"""Test race without the game: real format 2025 UDP packets built from the spec.

The scenario is sped up: a lap takes 20 seconds. Tyres wear, the strategy pit stop comes after lap 12, damage and
penalties appear along the way. Buttons on the page (f1_web.py --demo) trigger events at once, see DemoRace.ACTIONS.
"""
from __future__ import annotations

import math
import random
import struct
from typing import List

from f1_state import DAMAGE, FORECAST, HEADER, LAP, STATUS, TELEMETRY, TYRE_SET

CAR = 0
CARS = 22
LAP_SECONDS = 20.0
TOTAL_LAPS = 30
SESSION_UID = 0xD3D0_2025

# Visual and actual compound: S/C4, M/C3, H/C2, I, W
COMPOUNDS = {"S": (16, 17), "M": (17, 18), "H": (18, 19), "I": (7, 7), "W": (8, 8)}
SET_PLAN = ["S"] * 5 + ["M"] * 4 + ["H"] * 4 + ["I"] * 4 + ["W"] * 3  # 13 dry + 7 wet
SET_LIFE = {"S": 12, "M": 20, "H": 28, "I": 25, "W": 30}
SET_DELTA_MS = {"S": 0, "M": 700, "H": 1300, "I": 6000, "W": 9000}
WEAR_PER_LAP = {"S": 3.2, "M": 2.2, "H": 1.5, "I": 2.5, "W": 2.0}


def _header(packet_id: int, session_time: float, frame: int) -> bytes:
    return HEADER.pack(2025, 25, 1, 0, 1, packet_id, SESSION_UID, session_time, frame, frame, CAR, 255)


def _cars(record: bytes, size: int) -> bytes:
    """Our car first, the rest zeroed."""
    return record + bytes(size * (CARS - 1))


def _rear_first(front_first_values):
    """FL, FR, RL, RR -> RL, RR, FL, FR, the game order."""
    fl, fr, rl, rr = front_first_values
    return rl, rr, fl, fr


class DemoRace:
    ACTIONS = {
        "damage": "Hit a rival: front wing",
        "heavy": "Crash: floor, diffuser",
        "penalty": "Time penalty +5 s",
        "drive": "Drive-through",
        "stopgo": "Stop & Go",
        "warning": "Corner cutting warning",
        "overheat": "Overheat tyres and brakes",
        "fault": "DRS and ERS fault",
        "pit": "Pit stop now",
        "rain": "Rain in the forecast",
        "reset": "Restart",
    }

    def __init__(self, rng: random.Random = None) -> None:
        self._rng = rng or random.Random(25)
        self.reset()

    def reset(self) -> None:
        self.time = 0.0
        self.frame = 0
        self.lap = 1
        self.position = 6
        self.compound = "S"
        self.tyre_age_start = 0.0
        self.wear = [0.0, 0.0, 0.0, 0.0]
        self.tyre_damage = [0, 0, 0, 0]
        self.blisters = [0, 0, 0, 0]
        self.wings = [0, 0, 0]  # FL, FR, rear
        self.floor = self.diffuser = self.sidepod = 0
        self.gearbox = 6
        self.engine = 3
        self.pu = [14.0, 18.0, 9.0, 21.0, 16.0, 12.0]
        self.faults = False
        self.overheat_until = -1.0
        self.penalty_seconds = 0
        self.warnings = 0
        self.cuts = 0
        self.drive_through = 0
        self.stop_go = 0
        self.pit_stops = 0
        self.pit_until = -1.0
        self.pit_ideal, self.pit_latest = 12, 16
        self.rain = False
        self.fuel = 42.0
        self.fitted_set = 0
        self.used_sets = set()
        self._pending_events: List[bytes] = []
        self._next_scripted = 0

    # --- actions from the page ---

    def act(self, action: str) -> None:
        if action == "damage":
            self.wings[self._rng.choice((0, 1))] = min(100, max(self.wings[0], self.wings[1]) + self._rng.randint(20, 45))
            self.tyre_damage[0] = min(100, self.tyre_damage[0] + 8)
        elif action == "heavy":
            self.floor = min(100, self.floor + 35)
            self.diffuser = min(100, self.diffuser + 25)
            self.sidepod = min(100, self.sidepod + 15)
            self.wings[2] = min(100, self.wings[2] + 20)
            self.gearbox = min(100, self.gearbox + 10)
        elif action == "penalty":
            self.penalty_seconds += 5
            self._penalty(4, 4)
        elif action == "drive":
            self.drive_through += 1
            self._penalty(0, 17)
        elif action == "stopgo":
            self.stop_go += 1
            self._penalty(1, 3)
        elif action == "warning":
            self.warnings += 1
            self.cuts += 1
            self._penalty(5, 7)
        elif action == "overheat":
            self.overheat_until = self.time + 15
        elif action == "fault":
            self.faults = not self.faults
        elif action == "pit":
            self._pit()
        elif action == "rain":
            self.rain = not self.rain
        elif action == "reset":
            self.reset()

    def _penalty(self, p_type: int, infringement: int) -> None:
        details = struct.pack("<7B", p_type, infringement, CAR, 255, 0, self.lap, 0) + bytes(5)
        self._pending_events.append(b"PENA" + details)

    def _pit(self) -> None:
        if self.pit_until > self.time:
            return
        self.pit_until = self.time + 6
        self.pit_stops += 1
        if self.drive_through:
            self.drive_through -= 1
            self._pending_events.append(b"DTSV" + bytes([CAR]) + bytes(11))
        elif self.stop_go:
            self.stop_go -= 1
            self._pending_events.append(b"SGSV" + bytes([CAR]) + bytes(11))
        self.compound = "I" if self.rain else ("M" if self.compound == "S" else "H")
        self.used_sets.add(self.fitted_set)
        self.fitted_set = next((i for i, c in enumerate(SET_PLAN) if c == self.compound and i not in self.used_sets),
                               self.fitted_set)
        self.tyre_age_start = self.time
        self.wear = [0.0] * 4
        self.tyre_damage = [0] * 4
        self.blisters = [0] * 4
        self.wings[0] = self.wings[1] = 0  # new front wing
        if self.lap >= self.pit_ideal:
            self.pit_ideal = self.pit_latest = 0  # strategy done

    # --- time ---

    def step(self, dt: float) -> List[bytes]:
        """Advances the race by dt seconds and returns the packets of this step."""
        self.time += dt
        self.frame += 1
        new_lap = 1 + int(self.time / LAP_SECONDS)
        if new_lap != self.lap:
            self.lap = min(new_lap, TOTAL_LAPS)
            self.fuel = max(0.0, self.fuel - 1.35)
            for i, k in enumerate((1.0, 1.08, 0.86, 0.92)):
                self.wear[i] = min(100.0, self.wear[i] + WEAR_PER_LAP[self.compound] * k)
            if self.wear[1] > 30:
                self.blisters[1] = min(100, self.blisters[1] + 4)
            for i in range(6):
                self.pu[i] = min(100.0, self.pu[i] + 0.3)
            if self.lap == self.pit_ideal + 1 and self.pit_until < 0:
                self._pit()
        self._scripted()

        packets = [self._telemetry(), self._lap_data()]
        if self.frame % 2 == 0:
            packets.append(self._status())
            packets.append(self._damage())
        if self.frame % 10 == 0:
            packets.append(self._session())
            packets.append(self._tyre_sets())
        for event in self._pending_events:
            packets.append(_header(3, self.time, self.frame) + event)
        self._pending_events.clear()
        return packets

    def _scripted(self) -> None:
        """Things happen without clicks too, so every block shows something in the first couple of minutes."""
        script = ((25, "warning"), (45, "damage"), (70, "penalty"), (95, "drive"), (140, "heavy"))
        while self._next_scripted < len(script) and self.time >= script[self._next_scripted][0]:
            self.act(script[self._next_scripted][1])
            self._next_scripted += 1

    # --- packets ---

    def _telemetry(self) -> bytes:
        t = self.time
        braking = max(0.0, math.sin(t * 1.3)) ** 6  # braking zones
        hot = 25 if t < self.overheat_until else 0
        surface = [int(92 + 6 * math.sin(t * 0.7 + i) + hot + (4 if i == 1 else 0)) for i in range(4)]
        inner = [int(98 + 3 * math.sin(t * 0.2 + i) + hot // 2) for i in range(4)]
        brakes = [int(380 + 520 * braking + (i < 2) * 60 + hot * 12) for i in range(4)]
        pressure = [23.1, 23.3, 21.4, 21.5]
        speed = int(180 + 120 * (1 - braking))
        record = TELEMETRY.pack(
            speed, 0.9, 0.0, braking, 0, 7, 11000, 0, 80, 0,
            *_rear_first(brakes), *_rear_first(surface), *_rear_first(inner), 108 + hot // 3,
            *_rear_first(pressure), 0, 0, 0, 0)
        return _header(6, t, self.frame) + _cars(record, TELEMETRY.size) + bytes([255, 255, 0])

    def _status(self) -> bytes:
        visual, actual = COMPOUNDS[self.compound]
        age = int((self.time - self.tyre_age_start) / LAP_SECONDS)
        record = STATUS.pack(1, 0, 1, 56, int(self.pit_until > self.time), self.fuel, 110.0, self.fuel / 1.35 - (TOTAL_LAPS - self.lap),
                             13000, 4000, 8, 0, 0, actual, visual, age, 0, 0.0, 0.0, 2_500_000.0, 1, 0.0, 0.0, 0.0, 0)
        return _header(7, self.time, self.frame) + _cars(record, STATUS.size)

    def _damage(self) -> bytes:
        parts = [*self.wings, self.floor, self.diffuser, self.sidepod, int(self.faults), int(self.faults), self.gearbox,
                 self.engine, *(int(p) for p in self.pu), 0, 0]
        record = DAMAGE.pack(*_rear_first(self.wear), *_rear_first(self.tyre_damage), *_rear_first([0, 0, 0, 0]),
                             *_rear_first(self.blisters), *parts)
        return _header(10, self.time, self.frame) + _cars(record, DAMAGE.size)

    def _lap_data(self) -> bytes:
        in_pit = self.pit_until > self.time
        pit_status = 0 if not in_pit else (2 if self.pit_until - self.time < 4 else 1)
        lap_distance = (self.time % LAP_SECONDS) / LAP_SECONDS * 5000
        record = LAP.pack(
            92_417, int(self.time % LAP_SECONDS * 1000), 0, 0, 0, 0, 1284, 0, 9_812, 0, lap_distance,
            lap_distance + (self.lap - 1) * 5000, 0.0,
            self.position, self.lap, pit_status, self.pit_stops, 0, 0, self.penalty_seconds, self.warnings, self.cuts,
            self.drive_through, self.stop_go, 8, 4, 2, int(in_pit),
            int((6 - (self.pit_until - self.time)) * 1000) if in_pit else 0, 2400 if pit_status == 2 else 0,
            int(self.drive_through > 0 or self.stop_go > 0), 312.0, 3)
        return _header(2, self.time, self.frame) + _cars(record, LAP.size) + bytes([255, 255])

    def _session(self) -> bytes:
        head = struct.pack("<BbbBHBbBHHBBBB", 3 if self.rain else 1, 34, 24, TOTAL_LAPS, 5000, 15, 19, 0,
                           int(TOTAL_LAPS * LAP_SECONDS - self.time) % 65536, 7200, 80, 0, 0, 255)
        body = head + bytes([0, 0]) + bytes(21 * 5)  # sli support, marshal zones
        body += bytes([0, 0])  # safety car, network game
        forecast = []
        for i, minutes in enumerate((0, 5, 10, 15, 30)):
            weather = (3 if i >= 2 else 2) if self.rain else 1
            rain = (min(90, 20 + i * 20)) if self.rain else 5
            forecast.append(FORECAST.pack(15, minutes, weather, 34 - i, 2, 24, 2, rain))
        body += bytes([len(forecast)]) + b"".join(forecast) + bytes(8 * (64 - len(forecast)))
        body += bytes([0, 0]) + bytes(12) + bytes([self.pit_ideal, self.pit_latest, 4 if self.pit_ideal else 0])
        body += bytes(753 - HEADER.size - len(body))
        return _header(1, self.time, self.frame) + body

    def _tyre_sets(self) -> bytes:
        sets = []
        for i, compound in enumerate(SET_PLAN):
            visual, actual = COMPOUNDS[compound]
            fitted = i == self.fitted_set
            used = i in self.used_sets
            wear = int(sum(self.wear) / 4) if fitted else (35 if used else (12 if i in (4, 8) else 0))
            life = SET_LIFE[compound]
            sets.append(TYRE_SET.pack(actual, visual, wear, int(not used), 0, life, max(0, life - wear // 4),
                                      SET_DELTA_MS[compound], int(fitted)))
        return _header(12, self.time, self.frame) + bytes([CAR]) + b"".join(sets) + bytes([self.fitted_set])

