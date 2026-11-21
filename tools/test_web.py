"""Web dashboard test: packet parsing, the test race and the HTTP server.

Packets are built from the EA F1 25 UDP specification v3 with formats written here again, independently of f1_state.py.
Run: py -3 tools/test_web.py (no game, Nano or pyserial needed).
"""
import json
import pathlib
import socket
import struct
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import f1_demo  # noqa: E402
import f1_state  # noqa: E402
import f1_web  # noqa: E402

HDR = "<HBBBBBQfIIBB"
TELEMETRY = "<HfffBbHBBH4H4B4BH4f4B"
STATUS = "<BBBBBfffHHBBHBBBbfffBfffB"
DAMAGE = "<4f4B4B4B18B"
LAP = "<IIHBHBHBHBfff15BHHBfB"
TYRE_SET = "<BBBBBBBhB"
UID = 77


def header(pid, player=0, fmt=2025, uid=UID, t=12.5):
    return struct.pack(HDR, fmt, 25, 1, 0, 1, pid, uid, t, 1, 1, player, 255)


def per_car(pid, car, record, filler, tail=b"", player=None):
    cars = b"".join(record if i == car else filler for i in range(22))
    return header(pid, car if player is None else player) + cars + tail


def telemetry(car, brakes=(0,) * 4, surface=(0,) * 4, inner=(0,) * 4, engine=0, pressure=(0.0,) * 4, player=None):
    rec = struct.pack(TELEMETRY, 250, 1.0, 0.0, 0.0, 0, 7, 11000, 0, 50, 0, *brakes, *surface, *inner, engine, *pressure,
                      0, 0, 0, 0)
    other = struct.pack(TELEMETRY, *([1] * 10), *[999] * 4, *[1] * 8, 999, *[1.0] * 4, 0, 0, 0, 0)
    return per_car(6, car, rec, other, b"\xFF\xFF\x00", player)


def status(car, fuel=20.0, fuel_laps=1.5, actual=18, visual=17, age=4):
    rec = struct.pack(STATUS, 0, 0, 1, 55, 0, fuel, 110.0, fuel_laps, 13000, 4000, 8, 0, 0, actual, visual, age, 0,
                      0.0, 0.0, 1e6, 1, 0.0, 0.0, 0.0, 0)
    other = struct.pack(STATUS, *[0] * 5, 99.0, 99.0, 99.0, 0, 0, 0, 0, 0, 16, 16, 99, 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0)
    return per_car(7, car, rec, other)


def damage(car, wear=(0.0,) * 4, tyre_damage=(0,) * 4, brakes=(0,) * 4, blisters=(0,) * 4, parts=(0,) * 18):
    rec = struct.pack(DAMAGE, *wear, *tyre_damage, *brakes, *blisters, *parts)
    other = struct.pack(DAMAGE, 90.0, 90.0, 90.0, 90.0, *[90] * 30)
    return per_car(10, car, rec, other)


def lap_data(car, position=3, lap=7, pit_status=0, pit_stops=1, penalties=0, warnings=0, cuts=0, dt=0, sg=0,
             pit_timer=0, pit_lane_ms=0, pit_stop_ms=0, serve=0):
    rec = struct.pack(LAP, 90000, 1000, 0, 0, 0, 0, 0, 0, 0, 0, 100.0, 100.0, 0.0,
                      position, lap, pit_status, pit_stops, 0, 0, penalties, warnings, cuts, dt, sg, 5, 4, 2, pit_timer,
                      pit_lane_ms, pit_stop_ms, serve, 300.0, 2)
    other = struct.pack(LAP, *[0] * 10, 0.0, 0.0, 0.0, *[9] * 15, 0, 0, 1, 0.0, 0)
    return per_car(2, car, rec, other, b"\xFF\xFF")


def session(spectating=0, spectator_car=255, ideal=0, latest=0, rejoin=0, safety_car=0, forecasts=(), player=0):
    """21 marshal zones of 5 bytes, 64 forecasts of 8 bytes, a 68 byte tail; 724 bytes after the header."""
    body = struct.pack("<BbbBHBbBHHBBBB", 3, 31, 22, 53, 5000, 15, 19, 0, 3600, 7200, 80, 0, spectating, spectator_car)
    body += bytes([0, 0]) + b"\xAA" * (21 * 5) + bytes([safety_car, 0])
    samples = [struct.pack("<BBBbbbbB", *f) for f in forecasts]
    body += bytes([len(samples)]) + b"".join(samples) + b"\xBB" * (8 * (64 - len(samples)))
    body += bytes([0, 0]) + b"\xCC" * 12 + bytes([ideal, latest, rejoin]) + b"\xDD" * 68
    assert len(body) == 724, len(body)
    return header(1, player) + body


def event(code, details, player=4):
    return header(3, player) + code + details + bytes(12 - len(details))


def tyre_sets(car, fitted_index=2):
    sets = b""
    for i in range(20):
        sets += struct.pack(TYRE_SET, 18, 16 if i < 8 else 7, i, int(i % 3 != 0), 0, 20, 15, -300, int(i == fitted_index))
    return header(12, 4) + bytes([car]) + sets + bytes([fitted_index])


assert len(telemetry(0)) == 1352 and len(status(0)) == 1239 and len(damage(0)) == 1041
assert len(lap_data(0)) == 1285 and len(session()) == 753 and len(event(b"PENA", b"")) == 45 and len(tyre_sets(0)) == 231

# --- parsing ---


class Clock:
    now = 50.0

    def __call__(self):
        return self.now


clock = Clock()
s = f1_state.DashboardState(clock)
snap = s.snapshot()
assert snap["packetAge"] is None and snap["damage"] is None and snap["events"] == []

# The game orders wheels RL, RR, FL, FR; the dashboard FL, FR, RL, RR.
s.update(telemetry(4, brakes=(301, 302, 303, 304), surface=(81, 82, 83, 84), inner=(91, 92, 93, 94), engine=112,
                   pressure=(21.0, 21.5, 23.0, 23.5)))
t = s.snapshot()["telemetry"]
assert t["brakes"] == [303, 304, 301, 302], t
assert t["surface"] == [83, 84, 81, 82] and t["inner"] == [93, 94, 91, 92] and t["engine"] == 112
assert t["pressure"] == [23.0, 23.5, 21.0, 21.5]

s.update(damage(4, wear=(10.4, 20.6, 30.0, 140.0), tyre_damage=(1, 2, 3, 4), brakes=(5, 6, 7, 8), blisters=(9, 10, 11, 12),
                parts=(21, 22, 23, 24, 25, 26, 1, 0, 27, 28, 31, 32, 33, 34, 35, 36, 0, 1)))
d = s.snapshot()["damage"]
assert d["tyreWear"] == [30.0, 100.0, 10.4, 20.6], d["tyreWear"]
assert d["tyreDamage"] == [3, 4, 1, 2] and d["brakeDamage"] == [7, 8, 5, 6] and d["blisters"] == [11, 12, 9, 10]
assert (d["frontLeftWing"], d["frontRightWing"], d["rearWing"], d["floor"], d["diffuser"], d["sidepod"]) == (21, 22, 23, 24, 25, 26)
assert d["drsFault"] is True and d["ersFault"] is False and d["gearbox"] == 27 and d["engine"] == 28
assert d["pu"] == {"mguh": 31, "es": 32, "ce": 33, "ice": 34, "mguk": 35, "tc": 36}, d["pu"]
assert d["engineBlown"] is False and d["engineSeized"] is True

s.update(status(4, fuel=12.25, fuel_laps=-0.75, actual=18, visual=17, age=9))
st = s.snapshot()["status"]
assert st["visual"] == "M" and st["actual"] == "C3" and st["age"] == 9 and st["fuelLaps"] == -0.75 and st["fuelKg"] == 12.25

s.update(lap_data(4, position=5, lap=11, pit_status=2, pit_stops=1, penalties=10, warnings=3, cuts=2, dt=1, sg=1,
                  pit_timer=1, pit_lane_ms=15000, pit_stop_ms=2400, serve=1))
lap = s.snapshot()["lap"]
assert (lap["position"], lap["lap"], lap["pitStops"]) == (5, 11, 1)
assert lap["pitStatus"] == "In pit area" and lap["pitLaneActive"] and lap["pitLaneMs"] == 15000 and lap["pitStopMs"] == 2400
assert (lap["penaltySeconds"], lap["warnings"], lap["cornerCutting"], lap["driveThrough"], lap["stopGo"]) == (10, 3, 2, 1, 1)
assert lap["servePenaltyAtStop"] is True

s.update(session(ideal=14, latest=18, rejoin=7, safety_car=2, forecasts=[(15, 0, 1, 31, 2, 22, 2, 5), (15, 10, 3, 29, 1, 21, 1, 60),
                                                                         (5, 10, 4, 20, 1, 20, 1, 90)], player=4))
snap = s.snapshot()
assert snap["pitWindow"] == {"idealLap": 14, "latestLap": 18, "rejoinPosition": 7}, snap["pitWindow"]
sess = snap["session"]
assert sess["track"] == "Mexico" and sess["type"] == "Race" and sess["totalLaps"] == 53 and sess["safetyCar"] == 2
assert sess["trackTemp"] == 31 and sess["airTemp"] == 22 and sess["weather"] == "Light rain"
assert [f["minutes"] for f in sess["forecast"]] == [0, 10], "forecast only for the current session"
assert sess["forecast"][1]["rain"] == 60

# Penalties: only ours and only notable types
s.update(event(b"PENA", bytes([4, 4, 4, 9, 3, 11, 0])))  # time penalty for us
s.update(event(b"PENA", bytes([4, 4, 7, 4, 3, 11, 0])))  # another car
s.update(event(b"PENA", bytes([10, 25, 4, 255, 0, 11, 0])))  # lap invalidated: not listed
s.update(event(b"DTSV", bytes([4])))
s.update(event(b"SGSV", bytes([3])))  # another car
events = s.snapshot()["events"]
assert [e["kind"] for e in events] == ["served", "penalty"], events
assert events[1]["type"] == "Time penalty" and events[1]["reason"] == "Small collision" and events[1]["lap"] == 11
for i in range(20):
    s.update(event(b"PENA", bytes([5, 7, 4, 255, 0, i, 0])))
assert len(s.snapshot()["events"]) == f1_state.PENALTY_EVENTS_KEPT

# Tyre sets: another car is skipped
s.update(tyre_sets(9))
assert s.snapshot()["tyreSets"] is None
s.update(tyre_sets(4, fitted_index=2))
sets = s.snapshot()["tyreSets"]
assert sets["fitted"] == 2 and len(sets["sets"]) == 20
assert sets["sets"][2]["fitted"] and sets["sets"][3]["available"] is False and sets["sets"][3]["wear"] == 3
assert sets["sets"][0]["visual"] == "S" and sets["sets"][10]["visual"] == "I" and sets["sets"][1]["deltaMs"] == -300

# Packet age, wrong format, short and wrong size packets
clock.now += 3.0
assert s.snapshot()["packetAge"] == 3.0
s.update(telemetry(4)[:-1])
assert s.snapshot()["telemetry"]["engine"] == 112, "a packet of the wrong size is skipped"
s.update(b"\0" * 5)
s.update(header(6, fmt=2024) + bytes(1323))
assert s.snapshot()["wrongFormat"] == 2024
s.update(telemetry(4, engine=99))
assert s.snapshot()["wrongFormat"] is None and s.snapshot()["telemetry"]["engine"] == 99

# Spectator mode: the car comes from Session, the player's pit window is not overwritten
s.update(session(spectating=1, spectator_car=8, ideal=1, latest=2, rejoin=3, player=4))
assert s.car_index() == 8
assert s.snapshot()["pitWindow"]["idealLap"] == 14
s.update(telemetry(8, engine=77, player=4))
assert s.snapshot()["telemetry"]["engine"] == 77

# A new session resets everything
s.update(struct.pack(HDR, 2025, 25, 1, 0, 1, 6, UID + 1, 0.0, 1, 1, 0, 255) + telemetry(0)[29:])
snap = s.snapshot()
assert snap["damage"] is None and snap["events"] == [] and snap["pitWindow"] is None and snap["car"] == 0

# --- test race ---

race = f1_demo.DemoRace()
s = f1_state.DashboardState()
sizes = set()
for _ in range(20 * 60 * 4):  # 4 minutes in 50 ms steps: every scripted event and the pit stop
    for packet in race.step(0.05):
        assert len(packet) == f1_state.SIZES[packet[6]], (packet[6], len(packet))
        sizes.add(packet[6])
        s.update(packet)
assert sizes == set(f1_state.SIZES), sizes
snap = s.snapshot()
assert snap["lap"]["pitStops"] >= 1 and snap["status"]["visual"] == "M", "strategy pit stop on lap 13"
assert snap["pitWindow"]["idealLap"] == 0, "the strategy is done after the stop"
assert snap["lap"]["driveThrough"] == 0 and any(e["kind"] == "served" for e in snap["events"]), "drive-through served in the pits"
assert snap["damage"]["floor"] > 0 and snap["lap"]["penaltySeconds"] == 5
for action in f1_demo.DemoRace.ACTIONS:
    race.act(action)
    for packet in race.step(0.05):
        s.update(packet)
assert s.snapshot()["lap"]["lap"] == 1, "the last action is restart"

# --- server ---


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


state = f1_state.DashboardState()
state.mode = "demo"
demo = f1_demo.DemoRace()
for packet in demo.step(0.05):
    state.update(packet)
port = free_port()
server = f1_web.DashboardServer(state, "127.0.0.1", port, demo).start()
base = f"http://127.0.0.1:{port}"
try:
    for path, kind in (("/", "text/html"), ("/app.js", "javascript"), ("/style.css", "text/css")):
        with urllib.request.urlopen(base + path, timeout=5) as response:
            assert response.status == 200 and kind in response.headers["Content-Type"], (path, response.headers)
    with urllib.request.urlopen(base + "/state", timeout=5) as response:
        assert json.load(response)["mode"] == "demo"
    with urllib.request.urlopen(base + "/info", timeout=5) as response:
        assert "penalty" in json.load(response)["actions"]
    request = urllib.request.Request(base + "/demo/penalty", method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
    assert demo.penalty_seconds == 5
    for bad in ("/demo/nope", "/state"):
        try:
            urllib.request.urlopen(urllib.request.Request(base + bad, method="POST"), timeout=5)
            raise AssertionError(bad)
        except urllib.error.HTTPError as error:
            assert error.code == 404
    try:
        urllib.request.urlopen(base + "/../f1_state.py", timeout=5)
        raise AssertionError("files outside web/ are not served")
    except urllib.error.HTTPError as error:
        assert error.code == 404
    with urllib.request.urlopen(base + "/events", timeout=5) as response:
        line = response.readline().decode("utf-8")
        assert line.startswith("data: ") and json.loads(line[6:])["mode"] == "demo"
finally:
    server.close()

# Without the test race there are no buttons
server = f1_web.DashboardServer(f1_state.DashboardState(), "127.0.0.1", port).start()
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/info", timeout=5) as response:
        assert json.load(response) == {"mode": "live", "actions": {}}
finally:
    server.close()

print("test_web: ALL OK")
