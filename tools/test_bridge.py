"""Bridge test: real UDP packets in, a fake serial out.

The structures are written out in full from the EA F1 25 UDP specification v3, independently of the offsets in the bridge.
Run: py -3 tools/test_bridge.py (needs pyserial; the game and the Nano are not needed; does not interfere with a running bridge).
"""
import pathlib
import re
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import f1_bridge as b

HDR = "<HBBBBBQfIIBB"
TELEMETRY = "<HfffBbHBBH4H4B4BH4f4B"
STATUS = "<BBBBBfffHHBBHBBBbfffBfffB"
DAMAGE = "<4f4B4B4B18B"
SESSION = "<BbbBHBbBHHBBBB"
LAP = "<IIHBHBHBHBfff15BHHBfB"
assert struct.calcsize(HDR) == 29
assert struct.calcsize(TELEMETRY) == 60
assert struct.calcsize(STATUS) == 55
assert struct.calcsize(DAMAGE) == 46
assert struct.calcsize(LAP) == 57


def header(pid, player, fmt=2025):
    return struct.pack(HDR, fmt, 25, 1, 0, 1, pid, 1, 1.0, 1, 1, player, 255)


def telemetry_car(speed=0, gear=0, rpm=0, pct=0, bits=0, surface=(0, 0, 0, 0)):
    return struct.pack(TELEMETRY, speed, 0.5, 0.0, 0.0, 0, gear, rpm, 0, pct, bits,
                       1, 2, 3, 4, *surface, 77, 78, 79, 80, 99, 21.0, 21.0, 21.0, 21.0, 0, 0, 0, 0)


def telemetry(car, target, fmt=2025, pid=6, player=None):
    other = telemetry_car(99, 2, 7777, 11, 3, (1, 1, 1, 1))
    cars = b"".join(target if i == car else other for i in range(22))
    return header(pid, car if player is None else player, fmt) + cars + b"\0\0\0"


def status_car(visual=0, age=0, drs=0, joules=0.0, mode=0):
    return struct.pack(STATUS, 1, 1, 1, 50, 0, 10.0, 110.0, 5.0, 13000, 4000, 8, drs, 300, 18, visual, age, 0,
                       1.0, 1.0, joules, mode, 0.0, 0.0, 0.0, 0)


def status(car, target):
    cars = b"".join(target if i == car else status_car(18, 44) for i in range(22))
    return header(7, car) + cars


def damage_car(wear=(0.0, 0.0, 0.0, 0.0)):
    return struct.pack(DAMAGE, *wear, *[0] * 12, *[0] * 18)


def damage(car, target):
    cars = b"".join(target if i == car else damage_car((50.0, 50.0, 50.0, 50.0)) for i in range(22))
    return header(10, car) + cars


def lap_car(last=0, gap_ms=0, gap_min=0, position=0, lap=0, pit=0):
    return struct.pack(LAP, last, 1111, 2222, 0, 3333, 0, gap_ms, gap_min, 4444, 1, 100.0, 200.0, 0.5, position, lap, pit,
                       *[7] * 12, 5555, 6666, 1, 300.0, 9)


def lap_data(car, target):
    cars = b"".join(target if i == car else lap_car(1, 2, 3, 19, 40) for i in range(22))
    return header(2, car) + cars + b"\xFF\xFF"


def session(spectating, car, player=0, total_laps=50, safety_car=0, ideal=0, latest=0, rejoin=0):
    """The packet is assembled from the specification fields: 21 zones of 5 bytes, 64 forecasts of 8 bytes, a 68-byte tail."""
    body = struct.pack(SESSION, 0, 30, 25, total_laps, 5000, 10, 0, 0, 100, 100, 80, 0, spectating, car)
    body += bytes([0, 0]) + b"\xAA" * (21 * 5) + bytes([safety_car, 0, 0]) + b"\xBB" * (64 * 8)
    body += bytes([0, 0]) + b"\xCC" * 12 + bytes([ideal, latest, rejoin]) + b"\xDD" * 68
    return header(1, player) + body


assert len(telemetry(0, telemetry_car())) == 1352
assert len(status(0, status_car())) == 1239
assert len(damage(0, damage_car())) == 1041
assert len(lap_data(0, lap_car())) == 1285
assert len(session(0, 0)) == 753

# LED mask
ALL = (1 << 15) - 1
assert b.rev_led_mask(ALL, 100) == 0xFF
assert b.rev_led_mask(1 << 14, 100) == 0x80, "the last lamp lights only the limit"
assert b.rev_led_mask((1 << 14) - 1, 90) == 0x7F, "bits 0-13: seven LEDs, no limit"
assert b.rev_led_mask(0b11, 10) == 0x01
assert b.rev_led_mask(0b1, 10) == 0x00
assert b.rev_led_mask(0, 0) == 0
assert b.rev_led_mask(0, 50) == 0x0F, "without a lamp mask compute from the percentage"
assert b.rev_led_mask(0, 100) == 0xFF

# Telemetry
t = b.CarTracker()
frame = t.update(telemetry(3, telemetry_car(312, 8, 11850, 97, 0x3FFF, (92, 95, 101, 104))))
assert frame == (312, 8, 11850, 97, 0x3FFF), frame
assert frame.encode() == b"$312,8,11850,97,127\n", frame.encode()
assert t.update(telemetry(21, telemetry_car(0, -1, 3500))).gear == -1
assert t.update(telemetry(255, telemetry_car(1, 1, 1))) is None, "invalid index"
assert t.update(telemetry(0, telemetry_car(1, 1, 1), pid=5)) is None, "foreign packet"
assert t.update(telemetry(0, telemetry_car(1, 1, 1), fmt=2024)) is None, "foreign format"
assert t.update(b"\0" * 10) is None, "short"

# Tyres: the game's wheel order is RL, RR, FL, FR
t = b.CarTracker()
assert t.tyres() is None
t.update(telemetry(5, telemetry_car(surface=(101, 104, 92, 95))))
assert t.tyres() is None, "no compound and wear yet"
t.update(status(5, status_car(16, 7)))
assert t.tyres() is None, "no wear yet"
t.update(damage(5, damage_car((24.4, 26.6, 17.6, 21.0))))
tyres = t.tyres()
assert tyres == ("S", (18, 21, 24, 27), (92, 95, 101, 104), 7), tyres
assert tyres.encode() == b"#S,18,21,24,27,92,95,101,104,7\n", tyres.encode()

for visual, letter in {16: "S", 17: "M", 18: "H", 7: "I", 8: "W", 0: "?", 200: "?"}.items():
    t.update(status(5, status_car(visual, 1)))
    assert t.tyres().compound == letter, visual
t.update(damage(5, damage_car((-3.0, 180.0, 0.0, 100.0))))
assert t.tyres().wear == (0, 100, 0, 100), "wear within 0..100"

# Race screen
assert b.format_lap_time(92417) == "1:32.417"
assert b.format_lap_time(60000) == "1:00.000"
assert b.format_lap_time(59999) == "0:59.999"
assert b.format_lap_time(0) == "-:--.---"
assert b.format_lap_time(3_600_000) == "9:59.999"
assert b.format_gap(0) == "--", "leader or no data"
assert b.format_gap(1284) == "+1.284"
assert b.format_gap(9999) == "+9.999"
assert b.format_gap(12345) == "+12.34"
assert b.format_gap(123456) == "+123.4"
assert b.format_gap(5_000_000) == "+999.9"
for gap in (1, 999, 9999, 10000, 99999, 100000, 999999, 1000000):
    assert len(b.format_gap(gap)) <= 6, gap

t = b.CarTracker()
assert t.race() is None
t.update(session(0, 255, total_laps=53))
t.update(status(4, status_car(16, 1, drs=1)))
t.update(lap_data(4, lap_car(92417, 1284, 0, 3, 12)))
assert t.race().encode() == b"~0P03  L12/53  DRS+1.284  1:32.417\n", t.race().encode()
t.update(status(4, status_car(16, 1, drs=0)))
t.update(lap_data(4, lap_car(92417, 500, 1, 3, 12)))
assert t.race().encode() == b"~0P03  L12/53     +60.50  1:32.417\n", "gap minutes taken into account, no DRS"
t.update(session(0, 255, total_laps=0))
assert t.race().top.rstrip() == "P03  L12", "no total laps in practice"

# ERS screen
screen = b.ers_screen(2_880_000.0, 2)
assert screen.top == "ERS  72%  HOTLAP", screen.top
assert screen.bottom == "\xFF" * 11 + "\x02", repr(screen.bottom)
assert b.ers_screen(4_000_000.0, 3).bottom == "\xFF" * 16
assert b.ers_screen(9_000_000.0, 3).top == "ERS 100%OVERTAKE"
assert b.ers_screen(-5.0, 0).bottom == ""
assert b.ers_screen(0.0, 99).top == "ERS   0%       ?"

t = b.CarTracker()
assert t.ers() is None and t.next_slow_line() is None
t.update(status(2, status_car(16, 1, joules=2_000_000.0, mode=1)))
assert t.ers().top == "ERS  50%  MEDIUM", t.ers().top
assert t.ers_peak_joules == 2_000_000.0
assert t.next_slow_line().startswith(b"~1"), "only ERS so far"

# Messages
class Clock:
    now = 100.0

    def __call__(self):
        return self.now


clock = Clock()
t = b.CarTracker(clock)
assert t.active_alert() is None

t.update(session(0, 255, safety_car=1))
assert t.active_alert().top == "SAFETY CAR" and t.active_alert().number == 2
line = t.active_alert().encode()
assert line == b"~2SAFETY CAR      Deployed        \n", line
clock.now += b.ALERT_SECONDS - 0.1
assert t.active_alert() is not None
t.update(session(0, 255, safety_car=1))
clock.now += 0.2
assert t.active_alert() is None, "the message goes away by itself and does not repeat while the status is the same"
t.update(session(0, 255, safety_car=1))
assert t.active_alert() is None

t.update(session(0, 255, safety_car=2))
assert t.active_alert().top == "VIRTUAL SC"
clock.now += 10
t.update(session(0, 255, safety_car=0))
assert t.active_alert().top == "TRACK CLEAR"
clock.now += 10
t.update(session(0, 255, safety_car=3))
assert t.active_alert() is None, "the formation lap does not count"
t.update(session(0, 255, safety_car=0))
assert t.active_alert() is None, "nor does its end"

# Pit call: on the ideal lap, once
t = b.CarTracker(clock)
t.update(lap_data(0, lap_car(position=3, lap=23)))
t.update(session(0, 255, ideal=24, rejoin=5))
assert t.active_alert() is None, "not the right lap yet"
t.update(lap_data(0, lap_car(position=3, lap=24)))
t.update(session(0, 255, ideal=24, rejoin=5))
assert t.active_alert().encode() == b"~2BOX BOX         L24 rejoin P05  \n", t.active_alert().encode()
clock.now += 10
t.update(session(0, 255, ideal=24, rejoin=5))
assert t.active_alert() is None, "no second call"
t.update(lap_data(0, lap_car(position=3, lap=40)))
t.update(session(0, 255, ideal=40, rejoin=0))
assert t.active_alert().bottom == "L40", "a new window after the stop, position unknown"
clock.now += 10

t = b.CarTracker(clock)
t.update(lap_data(0, lap_car(position=3, lap=24, pit=1)))
t.update(session(0, 255, ideal=24))
assert t.active_alert() is None, "already in the pits"
t.update(session(0, 255, ideal=0))
assert t.active_alert() is None, "no window"

t = b.CarTracker(clock)
t.update(lap_data(0, lap_car(position=3, lap=24)))
t.update(session(1, 3, ideal=24))
assert t.active_alert() is None, "no call while spectating a bot"

t = b.CarTracker(clock)
t.update(session(0, 255, safety_car=1))
t.update(lap_data(0, lap_car(position=3, lap=24)))
t.update(session(0, 255, safety_car=1, ideal=24))
assert t.active_alert().top == "SAFETY CAR", "the call does not interrupt the safety car"
clock.now += 10
t.update(session(0, 255, safety_car=1, ideal=24))
assert t.active_alert().top == "BOX BOX", "and is shown right after"
clock.now += 10

# A new session resets the memory of the call and of the safety car
t = b.CarTracker(clock)
t.update(lap_data(0, lap_car(position=3, lap=24)))
t.update(session(0, 255, safety_car=1, ideal=24))
clock.now += 10
new_session = bytearray(session(0, 255, safety_car=1, ideal=24))
new_session[7] = 99  # m_sessionUID
t.update(bytes(new_session))
assert t.active_alert().top == "SAFETY CAR", "in a new session we report again"

# The message joins the slow line queue
t = b.CarTracker(clock)
t.update(status(0, status_car(16, 1)))
t.update(session(0, 255, safety_car=1))
kinds = {t.next_slow_line()[:2] for _ in range(4)}
assert kinds == {b"~1", b"~2"}, kinds

# All lines fit the firmware buffer, the text is exactly 33 characters and has no line-start characters
for screen in (b.race_screen(22, 99, 99, True, 999999, 599999), b.ers_screen(4e6, 3), b.ers_screen(2e6, 1),
               b.pit_alert(78, 22), b.TRACK_CLEAR_ALERT, *b.SAFETY_CAR_ALERTS.values()):
    line = screen.encode()
    assert len(line) == 35 and len(line) < 48, line
    body = line[1:-1]
    assert not set(body) & set(b"$#@~\n\0"), line

# Slow line queue
t = b.CarTracker()
t.update(telemetry(1, telemetry_car()))
t.update(status(1, status_car(16, 1)))
t.update(damage(1, damage_car()))
t.update(lap_data(1, lap_car(1, 1, 0, 1, 1)))
kinds = [t.next_slow_line()[:2] for _ in range(6)]
assert kinds == [b"#S", b"~0", b"~1"] * 2, kinds

# Spectator mode: the player index in the header is ignored
t = b.CarTracker()
target = telemetry_car(250, 7)
spectated = b"".join(target if i == 9 else telemetry_car(99, 2) for i in range(22))
packet_255 = header(6, 255) + spectated + b"\0\0\0"
assert t.update(packet_255) is None, "without Session the index 255 is invalid"
t.update(session(1, 9, player=255))
assert t.update(packet_255).speed == 250, "show the spectated car"
t.update(session(0, 255))
assert t.update(telemetry(2, telemetry_car(100, 3))).speed == 100, "the player's car again"
t.update(session(1, 200))
assert t.update(telemetry(2, telemetry_car(100, 3))) is None, "spectated car outside the array"

# The firmware understands the protocol: the same formats as parsed on the Nano
for line in (b.Frame(999, -1, 65535, 100, ALL).encode(), b.Tyres("S", (100,) * 4, (255,) * 4, 255).encode()):
    assert len(line) < 48, line
assert re.fullmatch(rb"\$\d+,-?\d+,\d+,\d+,\d+\n", b.Frame(999, -1, 65535, 100, ALL).encode())


class FakeLink:
    def __init__(self):
        self.lines = []

    def connect(self):
        return True

    def send(self, line):
        self.lines.append(line)

    def close(self):
        pass


TEST_UDP_PORT = 20999  # not 20777: a real bridge may be running there
b.UDP_PORT = TEST_UDP_PORT
link = FakeLink()
threading.Thread(target=b.run_game, args=(link,), daemon=True).start()
time.sleep(1.2)
assert link.lines.count(b.HEARTBEAT_LINE) >= 1, "the bridge heartbeat goes even without the game"
assert not [l for l in link.lines if l != b.HEARTBEAT_LINE], "without the game only the heartbeat is sent"

tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
addr = ("127.0.0.1", TEST_UDP_PORT)
tx.sendto(telemetry(0, telemetry_car(1, 1, 1), fmt=2024), addr)
tx.sendto(session(0, 255, total_laps=53), addr)
tx.sendto(b"xx", addr)
target_car = telemetry_car(312, 8, 11850, 97, ALL, (101, 104, 92, 95))
link.lines.clear()
start = time.monotonic()
sent = 0
while time.monotonic() - start < 2.0:  # 120 Hz, the game's highest rate
    tx.sendto(telemetry(5, target_car), addr)
    tx.sendto(status(5, status_car(16, 7, 1, 2_880_000.0, 2)), addr)
    tx.sendto(damage(5, damage_car((24.0, 27.0, 18.0, 21.0))), addr)
    tx.sendto(lap_data(5, lap_car(92417, 1284, 0, 3, 12)), addr)
    sent += 1
    time.sleep(1 / 120)
time.sleep(0.1)
frames = [l for l in link.lines if l.startswith(b"$")]
tyre_lines = [l for l in link.lines if l.startswith(b"#")]
text_lines = [l for l in link.lines if l.startswith(b"~")]
beats = link.lines.count(b.HEARTBEAT_LINE)
print(f"\nsent {sent} sets: {len(frames)} frames, {len(tyre_lines)} tyre, {len(text_lines)} text, {beats} heartbeats")
assert set(frames) == {b"$312,8,11850,97,255\n"}, set(frames)
assert set(tyre_lines) == {b"#S,18,21,24,27,92,95,101,104,7\n"}, set(tyre_lines)
assert 40 <= len(frames) <= 62, len(frames)
assert 7 <= len(tyre_lines) <= 11, len(tyre_lines)
assert set(text_lines) == {b.race_screen(3, 12, 53, True, 1284, 92417).encode(), b.ers_screen(2_880_000.0, 2).encode()}
assert 14 <= len(text_lines) <= 22, len(text_lines)
assert 1 <= beats <= 3, beats
bytes_per_second = sum(map(len, link.lines)) / 2.0
assert bytes_per_second < 11520 * 0.2, bytes_per_second

# NanoLink without a port does not crash and quietly retries
real = b.find_nano_port
b.find_nano_port = lambda: (_ for _ in ()).throw(LookupError("no nano"))
b.NanoLink(None).send(b"@\n")
b.find_nano_port = real

# The demo reaches the limit and stays within the protocol ranges
frames = [f for _, f in zip(range(1000), b.demo_frames())]
assert max(f.rev_percent for f in frames) == 100
assert any(b.rev_led_mask(f.rev_bits, f.rev_percent) == 0xFF for f in frames)
assert all(0 <= f.rev_percent <= 100 and 1 <= f.gear <= 8 and 0 <= f.rev_bits <= ALL for f in frames)
for seconds in (0, 1.5, 150, 199, 1000, 100000):
    d = b.demo_tyres(seconds)
    assert all(0 <= v <= 100 for v in d.wear) and all(0 <= v <= 255 for v in d.temperature), seconds
    assert len(b.demo_race(seconds).encode()) == 35 and len(b.demo_ers(seconds).encode()) == 35, seconds
assert b.demo_alert(1.0).top == "SAFETY CAR" and b.demo_alert(21.0).top == "BOX BOX" and b.demo_alert(10.0) is None

# Width of the tyre screens at extreme values
for wear, temp, age in ((100, 255, 99), (0, 0, 0), (18, 92, 7), (9, 101, 10)):
    middle = f" S{age} " if age < 10 else f"S{age} "
    for row in (f"FL{wear:3d}%    FR{wear:3d}%", f"RL{wear:3d}%{middle}RR{wear:3d}%",
                f"FL{temp:4d}    FR{temp:4d}", f"RL{temp:4d}{middle}RR{temp:4d}"):
        assert len(row) == 16, row

print("ALL OK")
