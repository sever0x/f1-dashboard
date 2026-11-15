"""Test of recording and analysis on a simulated race with mistakes planted in advance.

Packets are assembled here from the specification, independently of the analysis: if a field is mixed up, the test will notice.
Run: py -3 tools/test_race_report.py (the game and the Nano are not needed; does not interfere with a running bridge).
"""
import copy
import gzip
import pathlib
import socket
import struct
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import f1_bridge as bridge  # noqa: E402
import f1_record as record  # noqa: E402
import race_report as report  # noqa: E402

HDR = "<HBBBBBQfIIBB"
UID = 0x1234ABCD
PLAYER, RIVAL, THIRD = 0, 1, 2
LENGTH = 2400
HZ = 60
TOTAL_LAPS = 8
PIT_LAP = 6


def header(pid, t, frame, player=PLAYER, uid=UID):
    return struct.pack(HDR, 2025, 25, 1, 0, 1, pid, uid, t, frame, frame, player, 255)


def lap_record(last=0, current=0, dist=0.0, position=1, lap=1, pit=0, invalid=0, gap_ms=0, result=2, grid=1):
    return struct.pack("<IIHBHBHBHBfff15BHHBfB", last, current, 0, 0, 0, 0, gap_ms, 0, 0, 0, dist, dist, 0.0, position, lap,
                       pit, 0, 0, invalid, 0, 0, 0, 0, 0, grid, 4, result, 0, 3000 if pit else 0, 2200 if pit else 0, 0, 300.0, 255)


def telemetry_record(speed=0, throttle=0.0, brake=0.0, gear=4, surface=(90, 91, 80, 81), wheels=(0, 0, 0, 0)):
    # wheels in RL RR FL FR order, temperatures chosen so that the order is checked
    return struct.pack("<HfffBbHBBH4H4B4BH4f4B", speed, throttle, 0.0, brake, 0, gear, 10000, 0, 50, 0,
                       500, 501, 600, 601, *surface, 95, 96, 85, 86, 100, 21.0, 21.0, 21.0, 21.0, *wheels)


def status_record(ers=2_000_000.0, mode=1, fuel=50.0, visual=16, age=1, deployed=1_000_000.0):
    return struct.pack("<BBBBBfffHHBBHBBBbfffBfffB", 0, 0, 1, 56, 0, fuel, 110.0, 2.5, 13000, 4000, 8, 0, 0, 18, visual, age, 0,
                       700000.0, 120000.0, ers, mode, 500000.0, 250000.0, deployed, 0)


def motion_record(x=0.0, z=0.0):
    return struct.pack("<6f6h6f", x, 0.0, z, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 1.0, -2.0, 0.0, 0.0, 0.0, 0.0)


def motion_ex(slip=0.0):
    values = [0.0] * 61
    values[16:20] = [slip * 0.5, slip, slip * 0.1, slip * 0.2]
    return struct.pack("<61f", *values)


def damage_record(wear=(10.0, 11.0, 20.0, 21.0)):
    return struct.pack("<4f4B4B4B18B", *wear, *[0] * 12, 5, 6, 7, 8, *[0] * 14)


def session_body(safety_car=0):
    body = struct.pack("<BbbBHBbBHHBBBB", 0, 35, 25, TOTAL_LAPS, LENGTH, 15, 13, 0, 100, 100, 80, 0, 0, 255)
    body += bytes([0, 0]) + b"\0" * (21 * 5) + bytes([safety_car, 0, 0]) + b"\0" * (64 * 8)
    body += bytes([0, 70]) + b"\0" * 12 + bytes([PIT_LAP, PIT_LAP + 1, 3]) + b"\0" * 68
    assert len(body) == 753 - 29
    return body


def history_packet(car, t, frame, laps, stints):
    data = struct.pack("<7B", car, len(laps), len(stints), 1, 1, 1, 1)
    for ms, valid in laps:
        data += struct.pack("<IHBHBHBB", ms, ms // 3, 0, ms // 3, 0, ms // 3, 0, 15 if valid else 0)
    data += b"\0" * (14 * (100 - len(laps)))
    for stint in stints:
        data += struct.pack("<BBB", *stint)
    data += b"\0" * (3 * (8 - len(stints)))
    packet = header(11, t, frame) + data
    assert len(packet) == 1460
    return packet


def final_packet(t, frame, positions):
    data = bytes([22])
    for car in range(22):
        position, grid, best = positions.get(car, (0, 0, 0))
        data += struct.pack("<7BIdBBB8B8B8B", position, TOTAL_LAPS, grid, 0, 1, 3, 2, best, 400.0, 0, 0, 2,
                            *[18] * 8, *[16] * 8, *[8] * 8)
    packet = header(8, t, frame) + data
    assert len(packet) == 1042
    return packet


def participants_packet(t, frame):
    names = {PLAYER: "Player", RIVAL: "VERSTAPPEN", THIRD: "ALONSO"}
    data = bytes([3])
    for car in range(22):
        name = names.get(car, "").encode().ljust(32, b"\0")
        data += struct.pack("<7B32sBBHBB12x", 1, 1, 1, 1, 0, car, 1, name, 1, 1, 0, 1, 0)
    packet = header(4, t, frame) + data
    assert len(packet) == 1284
    return packet


def setup_packet(t, frame):
    one = struct.pack("<4B4f9B4fBf", 30, 25, 50, 55, -3.0, -1.5, 0.1, 0.2, 20, 5, 6, 7, 30, 60, 95, 56, 50, 22.0, 22.0, 24.0, 24.0, 0, 44.0)
    packet = header(5, t, frame) + one * 22 + struct.pack("<f", 0.0)
    assert len(packet) == 1133
    return packet


def event(code, t, frame, details=b""):
    packet = header(3, t, frame) + code + details.ljust(12, b"\0")
    assert len(packet) == 45
    return packet


# Track: (apex in m, apex speed, braking distance, acceleration distance), speeds in km/h
CORNERS = ((500, 90, 180, 300), (1200, 150, 120, 240), (1900, 110, 150, 280))
TOP = 300


def profile(where, corners=CORNERS):
    """Speed and pedal mode: linear to the apex and away from it."""
    speed, pedal = TOP, "gas"
    for apex, minimum, brake, accel in corners:
        if apex - brake <= where < apex:
            value = TOP + (minimum - TOP) * (where - (apex - brake)) / brake
            if value < speed:
                speed, pedal = value, "brake"
        elif apex <= where < apex + accel:
            value = minimum + (TOP - minimum) * (where - apex) / accel
            if value < speed:
                speed, pedal = value, "lift" if where - apex < 40 else "gas"
    return speed, pedal


def with_corner(corner_index, **changes):
    corners = [list(item) for item in CORNERS]
    if "minimum" in changes:
        corners[corner_index][1] = changes["minimum"]
    if "brake" in changes:
        corners[corner_index][2] = changes["brake"]
    return tuple(tuple(item) for item in corners)


class Car:
    def __init__(self, offset=0.0, pace=1.0):
        self.lap = 1
        self.dist = -5.0 - offset
        self.lap_ms = 0.0
        self.pace = pace
        self.laps = []
        self.last = 0

    def corners(self, plan):
        return plan.get(self.lap, CORNERS)

    def step(self, plan):
        speed, pedal = profile(max(self.dist, 0.0), self.corners(plan))
        speed *= self.pace
        self.dist += speed / 3.6 / HZ
        self.lap_ms += 1000 / HZ
        crossed = self.dist >= LENGTH
        if crossed:
            self.laps.append(round(self.lap_ms))
            self.last = round(self.lap_ms)
            self.dist -= LENGTH
            self.lap += 1
            self.lap_ms = 0.0
        return speed, pedal


# The player's planted mistakes:
# lap 4, T2 brakes much earlier and is slower; on all odd laps T3 is also slower than usual;
# lap 3, T1 lock-up; lap 5, T2 off track; lap 5 contact and flashback at T3; lap 6 pit stop.
PLAYER_PLAN = {
    2: CORNERS,
    3: with_corner(2, minimum=95),
    4: with_corner(1, minimum=110, brake=200),
    5: with_corner(2, minimum=95),
    7: with_corner(2, minimum=95),
}
RIVAL_PLAN = {}  # the rival is faster in T1: faster apex and brakes later
for lap in range(1, TOTAL_LAPS + 2):
    RIVAL_PLAN[lap] = with_corner(0, minimum=130, brake=140)
THIRD_PLAN = {}


def generate():
    """Yields the race packets in order. Time follows frames, a flashback takes it back."""
    player, rival, third = Car(), Car(10, 1.0), Car(20, 0.97)
    t, frame = 0.0, 0
    flashed = False
    snapshot = None
    yield 0.0, header(1, 0.0, 0) + session_body()
    yield 0.0, participants_packet(0.0, 0)
    yield 0.0, setup_packet(0.0, 0)
    yield 0.0, event(b"LGOT", 0.0, 0)
    while player.lap <= TOTAL_LAPS:
        frame += 1
        t = frame / HZ
        speed, pedal = player.step(PLAYER_PLAN)
        rival_speed, rival_pedal = rival.step(RIVAL_PLAN)
        third_speed, third_pedal = third.step(THIRD_PLAN)
        lap, dist = player.lap, player.dist

        if lap == 5 and dist >= 1880 and snapshot is None:
            snapshot = (t, frame, copy.deepcopy((player, rival, third)))
        if lap == 5 and dist >= 1900 and not flashed:  # the contact is undone by a flashback: the game and time go back
            flashed = True
            yield t, event(b"COLL", t, frame, bytes([PLAYER, RIVAL]))
            target_t, target_frame, cars = snapshot
            yield t, event(b"FLBK", t, frame, struct.pack("<If", target_frame, target_t))
            frame = target_frame
            player, rival, third = copy.deepcopy(cars)
            continue

        pit = 1 if lap == PIT_LAP and dist > 2200 else 0
        gap = 400 if lap == 3 else 1500
        slip = 0.4 if lap == 3 and 400 <= dist < 500 else 0.02
        wheels = (7, 7, 7, 7) if lap == 5 and 1150 <= dist < 1250 else (0, 0, 0, 0)
        age = lap if lap <= PIT_LAP else lap - PIT_LAP
        visual = 16 if lap <= PIT_LAP else 17
        wear = (2.0 * age, 2.1 * age, 3.0 * age, 3.1 * age)  # RL RR FL FR

        lap_records = {
            PLAYER: lap_record(player.last, round(player.lap_ms), dist, 2, lap, pit, 0, gap, grid=3),
            RIVAL: lap_record(rival.last, round(rival.lap_ms), rival.dist, 1, rival.lap, 0, 0, 0, grid=1),
            THIRD: lap_record(third.last, round(third.lap_ms), third.dist, 3, third.lap, 0, 0, 2500, grid=2),
        }
        lap_body = b"".join(lap_records.get(car, lap_record(result=0)) for car in range(22))
        yield t, header(2, t, frame) + lap_body + b"\xFF\xFF"

        def tel(s, p, **kw):
            return telemetry_record(int(s), 1.0 if p == "gas" else 0.0, 1.0 if p == "brake" else 0.0, 3 if s < 150 else 7, **kw)

        records = {PLAYER: tel(speed, pedal, wheels=wheels), RIVAL: tel(rival_speed, rival_pedal), THIRD: tel(third_speed, third_pedal)}
        blank = telemetry_record()
        yield t, header(6, t, frame) + b"".join(records.get(car, blank) for car in range(22)) + b"\xFF\xFF\0"

        ers = 3_000_000.0 - 1000.0 * (frame % 500)
        status = status_record(ers, 2 if lap == 4 else 1, 50.0 - lap, visual, age)
        yield t, header(7, t, frame) + status * 22

        positions = {PLAYER: motion_record(dist, 0.0), RIVAL: motion_record(rival.dist, 5.0), THIRD: motion_record(third.dist, 1.0)}
        yield t, header(0, t, frame) + b"".join(positions.get(car, motion_record()) for car in range(22))
        yield t, header(13, t, frame) + motion_ex(slip) + b"\0" * 0

        if frame % 6 == 0:
            yield t, header(10, t, frame) + damage_record(wear) * 22
        if frame % 30 == 0:
            yield t, header(1, t, frame) + session_body()
            yield t, setup_packet(t, frame)
        if frame % 300 == 0:
            yield t, participants_packet(t, frame)
        if frame == 1000:
            yield t, event(b"OVTK", t, frame, bytes([PLAYER, THIRD]))
        if frame == 2000:
            yield t, event(b"PENA", t, frame, bytes([5, 25, PLAYER, 255, 0, lap, 0]))
        if frame % 240 == 0:
            stints = [(PIT_LAP, 18, 16), (255, 19, 17)] if lap > PIT_LAP else [(255, 18, 16)]
            yield t, history_packet(PLAYER, t, frame, [(ms, True) for ms in player.laps], stints)

    t = (frame + 1) / HZ
    finals = {
        RIVAL: (1, 1, min(rival.laps)), PLAYER: (2, 3, min(player.laps[1:])), THIRD: (3, 2, min(third.laps)),
    }
    yield t, event(b"CHQF", t, frame)
    yield t, final_packet(t, frame, finals)
    yield t, history_packet(PLAYER, t, frame, [(ms, True) for ms in player.laps], [(PIT_LAP, 18, 16), (255, 19, 17)])
    yield t, history_packet(RIVAL, t, frame, [(ms, True) for ms in rival.laps], [(255, 18, 16)])
    yield t, history_packet(THIRD, t, frame, [(ms, True) for ms in third.laps], [(255, 18, 16)])


def write_recording(path, packets):
    with gzip.open(path, "wb", compresslevel=1) as target:
        target.write(record.MAGIC)
        for t, data in packets:
            target.write(record.RECORD.pack(1_700_000_000.0 + t, len(data)))
            target.write(data)


tmp = pathlib.Path(tempfile.mkdtemp(prefix="f1report"))

# --- Packet sizes match the specification and the analysis
sample = list(zip(range(30), generate()))
sizes = {}
for _, (_, data) in sample:
    pid = data[6]
    sizes[pid] = len(data)
for pid, size in sizes.items():
    assert report.SIZES.get(pid, size) == size, (pid, size)

# --- Recording: all packets verbatim, a file per session, disk errors do not crash it
recorder = record.Recorder(tmp / "rec", all_sessions=True, full_packets=True)
recorder.start()
first = [header(6, 0.0, i, uid=7) + b"a" * 5 for i in range(1000)]
second = [header(6, 0.0, i, uid=8) + b"b" * 5 for i in range(500)]
for data in first + [b"short"] + second:
    recorder.add(data)
recorder.close()
assert len(recorder.paths) == 2, recorder.paths
assert [d for _, d in record.read_packets(recorder.paths[0])] == first
assert [d for _, d in record.read_packets(recorder.paths[1])] == second
assert recorder.dropped == 0

blocked = tmp / "blocked"
blocked.write_text("this is a file, not a folder")
recorder = record.Recorder(blocked, all_sessions=True, full_packets=True)
recorder.start()
recorder.add(first[0])
recorder.add(first[1])
recorder.close()
assert recorder.dropped == 2, "a disk error does not crash the recording"

# A cut-off file is read up to the break
broken = tmp / "broken.f1rec.gz"
raw = tmp / "whole.f1rec.gz"
source = gzip.GzipFile(raw, "wb")
source.write(record.MAGIC)
for data in first:
    source.write(record.RECORD.pack(1.0, len(data)))
    source.write(data)
source.close()
whole = raw.read_bytes()
broken.write_bytes(whole[:len(whole) // 2])
partial = list(record.read_packets(broken))
assert 0 < len(partial) <= len(first), len(partial)
assert all(data == first[i] for i, (_, data) in enumerate(partial)), "the break does not spoil what was read"

# --- Bridge: serial hung for 2 seconds, and the recording did not lose a single packet
class SlowLink:
    def __init__(self):
        self.lines = []

    def connect(self):
        return True

    def send(self, line):
        self.lines.append(line)
        if len(self.lines) == 3:
            time.sleep(2.0)

    def close(self):
        pass


TEST_PORT = 20998
bridge.UDP_PORT = TEST_PORT
recorder = record.Recorder(tmp / "bridge", all_sessions=True, full_packets=True)
recorder.start()
link = SlowLink()
runner = threading.Thread(target=bridge.run_game, args=(link, recorder), daemon=True)
runner.start()
time.sleep(0.3)
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sent = []
start = time.monotonic()
number = 0
telemetry_body = b"".join(telemetry_record(100, 1.0) for _ in range(22)) + b"\xFF\xFF\0"
while time.monotonic() - start < 3.0:
    number += 1
    packet = header(6, number / 60, number, uid=9) + telemetry_body
    sent.append(packet)
    tx.sendto(packet, ("127.0.0.1", TEST_PORT))
    # a packet the bridge doesn't care about must also get into the recording
    other = header(0, number / 60, number, uid=9) + motion_record() * 22
    sent.append(other)
    tx.sendto(other, ("127.0.0.1", TEST_PORT))
    time.sleep(1 / 120)
time.sleep(0.5)
recorder.close()
stored = [d for path in recorder.paths for _, d in record.read_packets(path)]
assert len(stored) == len(sent), f"recorded {len(stored)} of {len(sent)}"
assert stored == sent, "packets recorded verbatim and in order"
assert sum(1 for l in link.lines if l.startswith(b"$")) > 1, "the dashboard came back after the hang"
print(f"\nbridge: serial hung for 2 s, recorded {len(stored)} of {len(sent)} packets")

# --- Analysis
path = tmp / "race.f1rec.gz"
write_recording(path, generate())
race = report.load(path)
result = report.analyze(race)

assert race.player == PLAYER and race.wrong_format == 0
assert race.session["track"] == 13 and race.session["kind"] == 15 and race.session["length"] == LENGTH
assert race.session["total_laps"] == TOTAL_LAPS and race.session["ai"] == 70 and race.session["pit_speed"] == 80
assert race.names[RIVAL] == "VERSTAPPEN" and race.final[PLAYER]["position"] == 2 and race.final[PLAYER]["grid"] == 3
assert race.setup[0] == 30 and race.setup[14] == 95 and race.setup[15] == 56 and abs(race.setup[22] - 44.0) < 1e-6
assert race.gap_count == 0, race.gap_count
assert report.peek_session(path) == {"kind": 15, "track": 13}

laps = {row["lap"]: row for row in result["laps"]}
assert sorted(laps) == list(range(1, TOTAL_LAPS + 1)), sorted(laps)

# Wheel order, status fields
row = laps[2]
assert row["surface"] == (80, 81, 90, 91), row["surface"]
assert row["inner"] == (85, 86, 95, 96), row["inner"]
assert row["brake_peak"] == (600, 601, 500, 501), row["brake_peak"]
assert abs(row["wear"][0] - 6.0) < 0.01 and abs(row["wear"][2] - 4.0) < 0.01, row["wear"]
assert row["compound"] == "S" and row["age"] == 2
assert laps[PIT_LAP + 1]["compound"] == "M" and laps[PIT_LAP + 1]["age"] == 1
assert abs(row["fuel"] - 48.0) < 0.01 and abs(row["fuel_laps"] - 2.5) < 0.01
assert abs(row["deployed"] - 1e6) < 1 and abs(row["harvested"] - 750000) < 1
assert laps[4]["ers_modes"].get("HOTLAP") == 100 and laps[2]["ers_modes"].get("MEDIUM") == 100
assert row["damage"] == (5, 6, 7, 8), row["damage"]
assert laps[3]["traffic_pct"] == 100 and laps[2]["traffic_pct"] == 0

# Which laps are clean
assert not laps[1]["clean"], "lap 1 does not count"
assert not laps[PIT_LAP]["clean"], "pit stop lap"
assert laps[PIT_LAP]["pit"] and laps[PIT_LAP]["pit_lane_ms"] == 3000 and laps[PIT_LAP]["pit_stop_ms"] == 2200
assert not laps[5]["clean"] and laps[5]["flashbacks"] == 1, "lap with a flashback"
assert laps[2]["clean"] and laps[7]["clean"]

# Flashback: the undone stretch does not stay in the data, but the fact and the contact are kept
assert len(race.flashbacks) == 1 and race.flashbacks[0]["lap"] == 5
assert [event["code"] for event in race.flashbacks[0]["erased"]] == ["COLL"]
assert not [event for event in race.events if event["code"] == "COLL"], "the contact is undone"
times = race.trace.t
assert all(a <= b for a, b in zip(times, times[1:])), "time is monotonic after the flashback"

# Corners are found where they are, and segments add up to the lap time
corners = result["corners"]
assert [round(c.apex, -2) for c in corners] == [500, 1200, 1900], [c.apex for c in corners]
for number in (2, 7):
    total = sum(result["per_lap"][number, c.number]["time_ms"] for c in corners)
    assert abs(total - laps[number]["ms"]) < 25, (number, total, laps[number]["ms"])

# Planted mistakes
metrics = result["per_lap"]
assert metrics[3, 1]["lockup"] and not metrics[2, 1]["lockup"], "lock-up on lap 3, T1"
assert metrics[5, 2]["off_track"] and not metrics[4, 2]["off_track"], "off track on lap 5, T2"
assert metrics[4, 2]["min_speed"] < metrics[2, 2]["min_speed"] - 25, "lap 4, T2 slower"
assert metrics[4, 2]["brake_dist"] < metrics[2, 2]["brake_dist"] - 60, "lap 4, T2 brakes earlier"
assert 1040 < metrics[2, 2]["brake_dist"] < 1100, metrics[2, 2]["brake_dist"]

losses = {item["corner"].number: item for item in result["summaries"]}
# The repeated T3 mistake and the one-off T2 cost more than a clean T1, even if most passes are slow
assert losses[3]["loss_ms"] > 200, losses[3]["loss_ms"]
assert losses[2]["loss_ms"] > 200, losses[2]["loss_ms"]
assert losses[1]["loss_ms"] < 20, losses[1]["loss_ms"]
assert abs(losses[3]["total_loss_ms"] - losses[3]["loss_ms"] * losses[3]["laps"]) < 1
assert abs(losses[1]["min_speed_ms"] - 90) <= 2 and abs(losses[1]["apex_dist"] - 500) <= 5, losses[1]["apex_dist"]
assert race.first_move is not None and race.first_move - race.lights_out < 0.1, "the start reaction is computed"
assert 4 not in result["summaries"][1]["fast_laps"], "the lap 4 mistake did not get into the fast passes"
assert losses[1]["lockups"] == [3], losses[1]["lockups"]
assert 4 in [item["lap"] for item in result["mistakes"] if item["corner"] == 2], result["mistakes"][:3]
assert result["mistakes"][0]["lap"] == 4 and result["mistakes"][0]["corner"] == 2, result["mistakes"][0]
t3 = losses[3]
assert t3["slow"]["min_speed"] < t3["fast"]["min_speed"], "slow T3 passes are really slower"

# Rival: faster exactly in T1, brakes later and goes faster
assert result["comparisons"], "there is a comparison with the rival"
comparison = result["comparisons"][0]
assert comparison["car"] == RIVAL and comparison["name"] == "VERSTAPPEN", comparison["name"]
by_corner = {item["corner"]: item for item in comparison["rows"]}
assert by_corner[1]["delta_ms"] > 300, by_corner[1]
assert by_corner[1]["min_speed"] < -30, by_corner[1]
assert by_corner[1]["brake_m"] < -30, by_corner[1]
assert abs(by_corner[2]["delta_ms"]) < 100 and abs(by_corner[3]["delta_ms"]) < 100, by_corner
assert abs(by_corner[1]["line_m"] - 5.0) < 25, by_corner[1]

# Field
field = {item["car"]: item for item in result["field"]["rows"]}
assert field[THIRD]["median_ms"] > field[RIVAL]["median_ms"]
assert laps[2]["to_field_ms"] is not None
assert field[PLAYER]["stints"] == ["S-L6", "M"], field[PLAYER]["stints"]

# Stints: two sets, and the change is detected even without the age dropping
same_age = [{"lap": i, "age": age, "compound": "S", "clean": False, "ms": 0, "wear": (0, 0, 0, 0)} for i, age in ((1, 1), (2, 2), (3, 5), (4, 6))]
assert [item["first"] for item in report.stint_summary(race, same_age)] == [1, 3], "used tyres of the same compound"
# Stints in the race itself
assert [(item["first"], item["last"], item["compound"]) for item in result["stints"]] == [(1, 6, "S"), (7, 8, "M")], result["stints"]
assert result["stints"][0]["wear_per_lap"] is not None

# The report is built and contains the findings
target = report.build(path, tmp / "out")
text = (target / "report.md").read_text(encoding="utf-8")
assert "reaction - s" not in text
for needle in ("Suzuka", "Race", "P2 from grid P3", "## Laps", "## Corners", "## One-off losses", "VERSTAPPEN", "## ERS and fuel",
               "## Tyres and brakes", "## Pit stops", "Flashback", "undone: COLL", "you overtook ALONSO",
               "Lap invalidated, corner cutting", "Chequered flag", "## Data quality"):
    assert needle in text, needle
assert "None" not in text, [line for line in text.splitlines() if "None" in line][:3]
csv_lines = (target / "corners.csv").read_text(encoding="utf-8").splitlines()
assert csv_lines[0].startswith("lap,corner,time_ms") and len(csv_lines) > 20
assert (target / "laps.csv").read_text(encoding="utf-8").count("\n") == TOTAL_LAPS + 1
print(f"report: {len(text)} characters, {text.count(chr(10))} lines")

# --- The start and the end are detected automatically
def session_packet(uid, kind):
    body = bytearray(session_body())
    body[6] = kind
    return header(1, 0.0, 0, uid=uid) + bytes(body)


record.END_GRACE_SECONDS = 0.2
auto = record.Recorder(tmp / "auto")
auto.start()
before_session = header(6, 0.0, 1, uid=20) + telemetry_body
final = header(8, 5.0, 300, uid=20) + bytes(1042 - 29)
history_after = header(11, 5.1, 301, uid=20) + bytes(1460 - 29)
late = header(1, 6.0, 400, uid=20) + session_body()
auto.add(session_packet(10, 8))  # qualifying
auto.add(header(6, 0.0, 1, uid=10) + telemetry_body)
auto.add(before_session)  # race: telemetry arrived before the session info
auto.add(session_packet(20, 15))
auto.add(final)
auto.add(history_after)
time.sleep(1.5)  # the file closes by itself, without new packets and without stopping the bridge
assert len(auto.paths) == 1, auto.paths
closed_size = auto.paths[0].stat().st_size
stored_auto = list(record.read_packets(auto.paths[0]))
assert stored_auto, "the file is closed and fully readable while the bridge runs"
auto.add(late)
auto.add(session_packet(30, 16))  # the next race
auto.close()
assert len(auto.paths) == 2, auto.paths
assert auto.paths[0].stat().st_size == closed_size, "nothing is appended to the file after the finish"
assert [data for _, data in stored_auto] == [before_session, session_packet(20, 15), final, history_after], "order and early packets"
assert "Race" in report.SESSION_TYPES[report.peek_session(auto.paths[1])["kind"]]

all_mode = record.Recorder(tmp / "auto_all", all_sessions=True)
all_mode.start()
all_mode.add(session_packet(40, 8))
all_mode.close()
assert len(all_mode.paths) == 1, "with the flag qualifying is recorded too"

# --- Filtering: the analysis reaches the same conclusions with much less data
thin = record.PacketFilter()
packets = list(generate())
kept = [(t, thin.apply(data)) for t, data in packets]
kept = [(t, data) for t, data in kept if data is not None]
assert all(len(data) == len(original) for (_, data), (_, original) in zip(
    [(t, d) for t, d in kept if d[6] in (0, 2, 6, 7)], [(t, d) for t, d in packets if d[6] in (0, 2, 6, 7)])), "packet size unchanged"
for pid in record.UNUSED_PACKETS:
    assert thin.apply(header(pid, 1.0, 1) + bytes(100)) is None, pid
assert thin.apply(header(3, 1.0, 1) + b"BUTN" + bytes(12)) is None
assert thin.apply(header(3, 1.0, 1) + b"COLL" + bytes(12)) is not None, "events are needed"
two = record.PacketFilter()
full_a = header(2, 1.0, 1) + b"".join(lap_record(lap=car + 1) for car in range(22)) + b"\xFF\xFF"
full_b = header(2, 1.016, 2) + b"".join(lap_record(lap=car + 1) for car in range(22)) + b"\xFF\xFF"
assert two.apply(full_a) == full_a, "the first frame with rivals"
player_only = two.apply(full_b)
assert player_only != full_b and player_only[29:29 + 57] == full_b[29:29 + 57], "own car in place"
assert player_only[29 + 57:29 + 2 * 57] == bytes(57) and player_only[-2:] == b"\xFF\xFF", "rivals zeroed, tail intact"
spectator = header(2, 1.03, 3, player=255) + b"".join(lap_record(lap=car + 1) for car in range(22)) + b"\xFF\xFF"
assert two.apply(spectator) == spectator, "spectator: unknown which car is own"
hist = record.PacketFilter()
one = header(11, 1.0, 1) + bytes([3]) + bytes(1430)
assert hist.apply(one) is not None and hist.apply(header(11, 2.0, 2) + bytes([3]) + bytes(1430)) is None, "history less often"
assert hist.apply(header(11, 2.0, 2) + bytes([4]) + bytes(1430)) is not None, "each car has its own schedule"
assert hist.apply(header(8, 3.0, 3) + bytes(1013)) is not None
assert hist.apply(header(11, 3.1, 4) + bytes([3]) + bytes(1430)) is not None, "after the finish all the final history"

thin_path = tmp / "thin.f1rec.gz"
write_recording(thin_path, kept)
thin_race = report.load(thin_path)
thin_result = report.analyze(thin_race)
thin_laps = {row["lap"]: row for row in thin_result["laps"]}
assert sorted(thin_laps) == sorted(laps) and all(thin_laps[n]["ms"] == laps[n]["ms"] for n in laps)
assert all(thin_laps[n]["clean"] == laps[n]["clean"] for n in laps)
assert [round(c.apex, -2) for c in thin_result["corners"]] == [500, 1200, 1900]
assert thin_result["mistakes"][0]["lap"] == 4 and thin_result["mistakes"][0]["corner"] == 2
assert {item["corner"].number: item["lockups"] for item in thin_result["summaries"]}[1] == [3], "lock-up found"
assert thin_race.final[PLAYER]["position"] == 2 and thin_race.setup and thin_race.names[RIVAL] == "VERSTAPPEN"
assert thin_race.flashbacks and thin_race.gap_count == 0
thin_rows = {item["corner"]: item for item in thin_result["comparisons"][0]["rows"]}
assert thin_result["comparisons"][0]["car"] == RIVAL and thin_rows[1]["delta_ms"] > 300
assert abs(thin_rows[1]["brake_m"] - by_corner[1]["brake_m"]) <= 10, (thin_rows[1], by_corner[1])
assert abs(thin_rows[1]["delta_ms"] - by_corner[1]["delta_ms"]) < 60, (thin_rows[1], by_corner[1])
assert thin_laps[6]["wear"] == laps[6]["wear"] or abs(thin_laps[6]["wear"][0] - laps[6]["wear"][0]) < 4
print(f"filtering: {path.stat().st_size / 1e6:.1f} MB against {thin_path.stat().st_size / 1e6:.1f} MB on the model")

# --- Race boundaries in the analysis: the formation lap and the lap after the finish do not get into the laps
def frame_packets(t, frame, lap, dist, result=2, speed=100):
    lap_body = b"".join(lap_record(current=int(t * 1000), dist=dist, lap=lap, result=result if car == PLAYER else 0) for car in range(22))
    tel_body = b"".join(telemetry_record(speed, 1.0) for _ in range(22))
    return [header(2, t, frame) + lap_body + b"\xFF\xFF", header(6, t, frame) + tel_body + b"\xFF\xFF\0"]


loader = report.Loader()
loader.feed(0.0, header(1, 0.0, 0) + session_body())
for number in range(1, 40):  # the formation lap covers the same metres
    for data in frame_packets(number / 10, number, 1, number * 50.0):
        loader.feed(0.0, data)
loader.feed(0.0, event(b"LGOT", 5.0, 50))
assert len(loader.race.trace) == 0, "no data remains before the lights"
for data in frame_packets(5.1, 51, 1, 1.0):
    loader.feed(0.0, data)
for data in frame_packets(5.2, 52, 1, 2.0, result=3):
    loader.feed(0.0, data)
finished = loader.finish()
assert len(finished.trace) == 1 and finished.trace.dist[0] == 1.0, "no frames are written after the finish"

# Choosing the last race: qualifying is skipped
pick = tmp / "pick"
pick.mkdir()
race_file = pick / "a.f1rec.gz"
quali_file = pick / "b.f1rec.gz"
quali_body = bytearray(session_body())
quali_body[6] = 8
write_recording(race_file, [(0.0, header(1, 0.0, 0) + session_body())])
time.sleep(0.05)
write_recording(quali_file, [(0.0, header(1, 0.0, 0) + bytes(quali_body))])
assert report.choose(pick, False) == race_file
assert report.choose(pick, True) == quali_file

# A break mid-race, without the finish and history: the report is still built
packets = [item for _, item in zip(range(8000), generate())]
cut = tmp / "cut.f1rec.gz"
write_recording(cut, packets)
text = (report.build(cut, tmp / "cut") / "report.md").read_text(encoding="utf-8")
assert "No final classification" in text and "None" not in text

# A recording without laps
empty = tmp / "empty.f1rec.gz"
write_recording(empty, [(0.0, header(1, 0.0, 0) + session_body())])
text = (report.build(empty, tmp / "empty") / "report.md").read_text(encoding="utf-8")
assert "No lap data in the recording" in text

print("ALL OK")
