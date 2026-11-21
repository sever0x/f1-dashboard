#!/usr/bin/env python3
"""F1 25 -> Arduino Nano bridge.

Receives the game's UDP telemetry (2025 format), assembles the data of the watched car
from several packets and sends it over USB serial as lines:

    $speed,gear,rpm,rev bar %,mask of 8 LEDs                    30 times per second
    #compound,wear FL,FR,RL,RR,temperature FL,FR,RL,RR,laps     5 times per second
    ~screen number and two lines of 16 characters               ready-made screen text
    @                                                           bridge is alive, once per second

For example:  $312,8,11850,97,255   #S,18,21,24,27,92,95,101,104,7
              ~0P03  L12/53  DRS+1.284  1:32.417

The # and ~ lines take turns so as not to overflow the Nano's 64-byte buffer.

Usage:
    pip install -r requirements.txt
    python f1_bridge.py                 the Nano port is detected automatically
    python f1_bridge.py --port COM5     the port is given manually
    python f1_bridge.py --demo          hardware check without the game
    python f1_bridge.py --no-record     no telemetry recording into the races/ folder
    python f1_bridge.py --record-all-sessions    record not only races
    python f1_bridge.py --record-full-packets    record all game packets without filtering

UDP is received in a separate thread: packets for the recording are not lost even if serial hangs or the Nano reconnects.
"""
from __future__ import annotations

import argparse
import queue
import socket
import struct
import sys
import threading
import time
from typing import Callable, Iterator, NamedTuple, Optional, Tuple

import serial
from serial.tools import list_ports

from f1_record import Recorder

UDP_HOST = "127.0.0.1"
UDP_PORT = 20777
BAUD_RATE = 115200
FRAME_INTERVAL = 1.0 / 30  # the game can send up to 120 Hz, the Nano and the LCD don't need that much
SLOW_LINE_INTERVAL = 1.0 / 15  # one of the #, ~ lines at a time
HEARTBEAT_INTERVAL = 1.0
RECONNECT_SECONDS = 3.0
BOOTLOADER_SECONDS = 2.0  # opening the port resets the Nano
NO_DATA_WARNING_SECONDS = 5.0
UDP_RECEIVE_BUFFER = 4 * 1024 * 1024  # the game sends about 380 KB/s
DASHBOARD_QUEUE_PACKETS = 256  # the dashboard needs fresh data: on delay old packets are dropped

# Specification: EA F1 25 UDP specification v3. Everything is little-endian and unaligned.
EXPECTED_FORMAT = 2025
HEADER = struct.Struct("<HBBBBBQfIIBB")  # 29 bytes
HEADER_PACKET_ID = 5
HEADER_SESSION_UID = 6
HEADER_PLAYER_CAR_INDEX = 10
CAR_COUNT = 22

PACKET_ID_SESSION = 1
SESSION_PACKET_SIZE = 753
# m_totalLaps (3) | m_isSpectating (15), m_spectatorCarIndex | m_safetyCarStatus (124)
# | m_pitStopWindowIdealLap (653) | m_pitStopRejoinPosition (655)
SESSION_FIELDS = struct.Struct("<3xB11xBB107xB528xBxB")
SAFETY_CAR_NONE = 0
SAFETY_CAR_FULL = 1
SAFETY_CAR_VIRTUAL = 2

PACKET_ID_LAP_DATA = 2
LAP_DATA_PACKET_SIZE = 1285
LAP_DATA_RECORD_SIZE = 57
# m_lastLapTimeInMS I | ... | m_deltaToCarInFrontMSPart H, MinutesPart B | ...
# | m_carPosition B, m_currentLapNum B, m_pitStatus B
LAP_DATA_FIELDS = struct.Struct("<I10xHB15xBBB")

PACKET_ID_CAR_TELEMETRY = 6
CAR_TELEMETRY_PACKET_SIZE = 1352
CAR_TELEMETRY_RECORD_SIZE = 60
# m_speed H | throttle, steer, brake, clutch: 13x | m_gear b | m_engineRPM H | m_drs x
# | m_revLightsPercent B | m_revLightsBitValue H | m_brakesTemperature: 8x
# | m_tyresSurfaceTemperature 4B
CAR_TELEMETRY_FIELDS = struct.Struct("<H13xbHxBH8x4B")

PACKET_ID_CAR_STATUS = 7
CAR_STATUS_PACKET_SIZE = 1239
CAR_STATUS_RECORD_SIZE = 55
# m_drsAllowed (22) | m_visualTyreCompound (26), m_tyresAgeLaps | m_ersStoreEnergy (37), m_ersDeployMode
CAR_STATUS_FIELDS = struct.Struct("<22xB3xBB9xfB")

PACKET_ID_CAR_DAMAGE = 10
CAR_DAMAGE_PACKET_SIZE = 1041
CAR_DAMAGE_RECORD_SIZE = 46
CAR_DAMAGE_FIELDS = struct.Struct("<4f")  # m_tyresWear

# Which of the game's 15 lamps (bit 0 on the left) our 8 LEDs show; the last one means the limit.
REV_LAMP_BITS = (1, 3, 5, 7, 9, 11, 13, 14)
LED_COUNT = len(REV_LAMP_BITS)
REV_LAMP_COUNT = 15

# m_visualTyreCompound: F1 Modern, F1 Classic (9 dry, 10 wet), F2.
TYRE_LETTERS = {
    16: "S", 17: "M", 18: "H", 7: "I", 8: "W",
    9: "D", 10: "W",
    15: "W", 19: "U", 20: "S", 21: "M", 22: "H",
}
UNKNOWN_TYRE = "?"

# USB chips of Nano boards: CH340 on clones, FTDI on old originals, Arduino SA.
NANO_USB_VENDOR_IDS = {0x1A86, 0x0403, 0x2341}

HEARTBEAT_LINE = b"@\n"

LCD_COLS = 16
SCREEN_RACE = 0
SCREEN_ERS = 1
SCREEN_ALERT = 2  # covers the selected screen while the bridge keeps sending this line
ALERT_SECONDS = 6.0
ERS_MAX_JOULES = 4_000_000  # not stated in the specification, from memory; the bridge prints the maximum from the game
ERS_MODES = {0: "NONE", 1: "MEDIUM", 2: "HOTLAP", 3: "OVERTAKE"}
LCD_BLOCK = "\xFF"  # HD44780 solid block
BAR_STEPS_PER_CELL = 5  # characters 1..4 in the firmware: that many columns filled from the left


def rev_led_mask(rev_bits: int, rev_percent: int) -> int:
    """LED mask: bit 0 is the first on the left. Without lamp data it is computed from the percentage."""
    if rev_bits:
        mask = 0
        for led, lamp in enumerate(REV_LAMP_BITS):
            if rev_bits >> lamp & 1:
                mask |= 1 << led
        return mask
    return (1 << (rev_percent * LED_COUNT + 99) // 100) - 1


class Frame(NamedTuple):
    speed: int  # km/h
    gear: int  # -1 = R, 0 = N, 1..8
    rpm: int
    rev_percent: int  # 0..100, the game's own rev lights bar
    rev_bits: int  # m_revLightsBitValue, 15 lamps

    def encode(self) -> bytes:
        mask = rev_led_mask(self.rev_bits, self.rev_percent)
        return f"${self.speed},{self.gear},{self.rpm},{self.rev_percent},{mask}\n".encode("ascii")


class Tyres(NamedTuple):
    compound: str  # compound letter
    wear: Tuple[int, int, int, int]  # percent: FL, FR, RL, RR
    temperature: Tuple[int, int, int, int]  # surface, °C: FL, FR, RL, RR
    age_laps: int

    def encode(self) -> bytes:
        numbers = (*self.wear, *self.temperature, self.age_laps)
        return f"#{self.compound},{','.join(map(str, numbers))}\n".encode("ascii")


class TextScreen(NamedTuple):
    """A screen whose text is fully prepared by the bridge. The characters $ # @ ~ are not allowed in it: they start a line."""

    number: int
    top: str
    bottom: str

    def encode(self) -> bytes:
        return f"~{self.number}{self.top:<{LCD_COLS}}{self.bottom:<{LCD_COLS}}\n".encode("latin-1")


def format_lap_time(ms: int) -> str:
    if ms <= 0:
        return "-:--.---"
    if ms >= 600_000:  # nothing above 9:59.999 fits into 8 characters
        return "9:59.999"
    minutes, rest = divmod(ms, 60_000)
    return f"{minutes}:{rest // 1000:02d}.{rest % 1000:03d}"


def format_gap(ms: int) -> str:
    """A gap of at most 6 characters: the larger it is, the fewer decimal places."""
    if ms <= 0:
        return "--"
    if ms < 10_000:
        return f"+{ms // 1000}.{ms % 1000:03d}"
    if ms < 100_000:
        return f"+{ms // 1000}.{ms % 1000 // 10:02d}"
    if ms < 1_000_000:
        return f"+{ms // 1000}.{ms % 1000 // 100}"
    return "+999.9"


def race_screen(position: int, lap: int, total_laps: int, drs: bool, gap_ms: int, last_lap_ms: int) -> TextScreen:
    laps = f"L{lap:02d}/{total_laps:02d}" if total_laps else f"L{lap:02d}"
    top = f"P{position:02d}  {laps}".ljust(LCD_COLS - 3) + ("DRS" if drs else "")
    return TextScreen(SCREEN_RACE, top, f"{format_gap(gap_ms):<8}{format_lap_time(last_lap_ms):>8}")


def ers_screen(joules: float, mode: int) -> TextScreen:
    percent = min(100, max(0, round(joules * 100 / ERS_MAX_JOULES)))
    label = ERS_MODES.get(mode, "?")
    steps = percent * LCD_COLS * BAR_STEPS_PER_CELL // 100
    full, partial = divmod(steps, BAR_STEPS_PER_CELL)
    bar = LCD_BLOCK * full + (chr(partial) if partial else "")
    return TextScreen(SCREEN_ERS, f"ERS {percent:3d}%{label:>8}", bar)


def alert(top: str, bottom: str) -> TextScreen:
    return TextScreen(SCREEN_ALERT, top, bottom)


def pit_alert(ideal_lap: int, rejoin_position: int) -> TextScreen:
    rejoin = f" rejoin P{rejoin_position:02d}" if rejoin_position else ""
    return alert("BOX BOX", f"L{ideal_lap:02d}{rejoin}")


SAFETY_CAR_ALERTS = {
    SAFETY_CAR_FULL: alert("SAFETY CAR", "Deployed"),
    SAFETY_CAR_VIRTUAL: alert("VIRTUAL SC", "Slow down"),
}
TRACK_CLEAR_ALERT = alert("TRACK CLEAR", "Racing resumes")


def front_first(rear_first: Tuple) -> Tuple:
    """Wheel arrays in the game go RL, RR, FL, FR; on screen FL, FR, RL, RR."""
    return rear_first[2], rear_first[3], rear_first[0], rear_first[1]


def packet_format(data: bytes) -> Optional[int]:
    """Returns the packet's m_packetFormat or None if the packet is too short."""
    if len(data) < HEADER.size:
        return None
    return HEADER.unpack_from(data)[0]


class CarTracker:
    """Assembles the data of one car from different packets: the player's or the one they are spectating."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._alert: Optional[TextScreen] = None
        self._alert_until = 0.0
        self._session_uid: Optional[int] = None
        self._safety_car = SAFETY_CAR_NONE
        self._pit_called_for_lap: Optional[int] = None
        self._pit_status = 0
        self._spectated_car: Optional[int] = None
        self._wear: Optional[Tuple[int, int, int, int]] = None
        self._temperature: Optional[Tuple[int, int, int, int]] = None
        self._compound: Optional[str] = None
        self._age_laps = 0
        self._drs_allowed = False
        self._ers_joules: Optional[float] = None
        self._ers_mode = 0
        self.ers_peak_joules = 0.0
        self._total_laps = 0
        self._lap: Optional[Tuple[int, int, int, int]] = None  # last lap, gap, position, lap number
        self._next_slow_line = 0

    def update(self, data: bytes) -> Optional[Frame]:
        """Takes a packet into account. For a telemetry packet returns a dashboard frame."""
        if len(data) < HEADER.size:
            return None
        header = HEADER.unpack_from(data)
        if header[0] != EXPECTED_FORMAT:
            return None
        if header[HEADER_SESSION_UID] != self._session_uid:
            self._start_session(header[HEADER_SESSION_UID])
        packet_id = header[HEADER_PACKET_ID]
        if packet_id == PACKET_ID_SESSION and len(data) == SESSION_PACKET_SIZE:
            self._read_session(data)
            return None

        car = self._spectated_car if self._spectated_car is not None else header[HEADER_PLAYER_CAR_INDEX]
        if car >= CAR_COUNT:  # the index is not known yet or is invalid
            return None
        if packet_id == PACKET_ID_CAR_TELEMETRY and len(data) == CAR_TELEMETRY_PACKET_SIZE:
            return self._read_telemetry(data, car)
        if packet_id == PACKET_ID_CAR_STATUS and len(data) == CAR_STATUS_PACKET_SIZE:
            self._read_status(data, car)
        elif packet_id == PACKET_ID_CAR_DAMAGE and len(data) == CAR_DAMAGE_PACKET_SIZE:
            self._read_damage(data, car)
        elif packet_id == PACKET_ID_LAP_DATA and len(data) == LAP_DATA_PACKET_SIZE:
            self._read_lap(data, car)
        return None

    def tyres(self) -> Optional[Tyres]:
        """Tyre data, once all the needed packets have arrived."""
        if self._wear is None or self._temperature is None or self._compound is None:
            return None
        return Tyres(self._compound, self._wear, self._temperature, self._age_laps)

    def race(self) -> Optional[TextScreen]:
        if self._lap is None:
            return None
        last_lap_ms, gap_ms, position, lap = self._lap
        return race_screen(position, lap, self._total_laps, self._drs_allowed, gap_ms, last_lap_ms)

    def ers(self) -> Optional[TextScreen]:
        if self._ers_joules is None:
            return None
        return ers_screen(self._ers_joules, self._ers_mode)

    def active_alert(self) -> Optional[TextScreen]:
        return self._alert if self._clock() < self._alert_until else None

    def next_slow_line(self) -> Optional[bytes]:
        """The next slow line in turn among those that already have data."""
        sources = (self.tyres, self.race, self.ers, self.active_alert)
        for _ in sources:
            item = sources[self._next_slow_line]()
            self._next_slow_line = (self._next_slow_line + 1) % len(sources)
            if item is not None:
                return item.encode()
        return None

    def _start_session(self, uid: Optional[int]) -> None:
        self._session_uid = uid
        self._alert = None
        self._safety_car = SAFETY_CAR_NONE
        self._pit_called_for_lap = None
        self._lap = None
        self._pit_status = 0
        self._wear = None
        self._temperature = None
        self._compound = None
        self._age_laps = 0
        self._ers_joules = None
        self._ers_mode = 0
        self._total_laps = 0

    def _show_alert(self, screen: TextScreen) -> None:
        self._alert = screen
        self._alert_until = self._clock() + ALERT_SECONDS

    def _read_session(self, data: bytes) -> None:
        self._total_laps, spectating, car, safety_car, ideal_lap, rejoin = SESSION_FIELDS.unpack_from(data, HEADER.size)
        self._spectated_car = car if spectating else None
        self._check_safety_car(safety_car)
        if not spectating:  # the pit stop window in the packet only applies to the player
            self._check_pit_call(ideal_lap, rejoin)

    def _check_safety_car(self, status: int) -> None:
        if status == self._safety_car:
            return
        if status in SAFETY_CAR_ALERTS:
            self._show_alert(SAFETY_CAR_ALERTS[status])
        elif status == SAFETY_CAR_NONE and self._safety_car in SAFETY_CAR_ALERTS:
            self._show_alert(TRACK_CLEAR_ALERT)
        self._safety_car = status

    def _check_pit_call(self, ideal_lap: int, rejoin: int) -> None:
        """The game does not report the engineer's call, so we call to the pits on the strategy's ideal lap."""
        if self._lap is None or ideal_lap == 0 or ideal_lap == self._pit_called_for_lap:
            return
        current_lap = self._lap[3]
        if current_lap != ideal_lap or self._pit_status != 0 or self.active_alert() is not None:
            return  # while another message is shown, the call waits for the next packet
        self._pit_called_for_lap = ideal_lap
        self._show_alert(pit_alert(ideal_lap, rejoin))

    def _read_telemetry(self, data: bytes, car: int) -> Frame:
        offset = HEADER.size + car * CAR_TELEMETRY_RECORD_SIZE
        speed, gear, rpm, percent, bits, *surface = CAR_TELEMETRY_FIELDS.unpack_from(data, offset)
        self._temperature = front_first(tuple(surface))
        return Frame(speed, gear, rpm, percent, bits)

    def _read_status(self, data: bytes, car: int) -> None:
        offset = HEADER.size + car * CAR_STATUS_RECORD_SIZE
        drs, compound, age, joules, mode = CAR_STATUS_FIELDS.unpack_from(data, offset)
        self._drs_allowed = drs == 1
        self._compound = TYRE_LETTERS.get(compound, UNKNOWN_TYRE)
        self._age_laps = age
        self._ers_joules = joules
        self._ers_mode = mode
        self.ers_peak_joules = max(self.ers_peak_joules, joules)

    def _read_damage(self, data: bytes, car: int) -> None:
        offset = HEADER.size + car * CAR_DAMAGE_RECORD_SIZE
        wear = CAR_DAMAGE_FIELDS.unpack_from(data, offset)
        self._wear = front_first(tuple(min(100, max(0, round(value))) for value in wear))


    def _read_lap(self, data: bytes, car: int) -> None:
        offset = HEADER.size + car * LAP_DATA_RECORD_SIZE
        last_lap_ms, gap_ms, gap_minutes, position, lap, pit_status = LAP_DATA_FIELDS.unpack_from(data, offset)
        self._lap = (last_lap_ms, gap_minutes * 60_000 + gap_ms, position, lap)
        self._pit_status = pit_status


def find_nano_port() -> str:
    ports = list(list_ports.comports())
    candidates = [p for p in ports if p.vid in NANO_USB_VENDOR_IDS]
    if len(candidates) == 1:
        return candidates[0].device
    listing = "\n".join(f"  {p.device}: {p.description}" for p in ports) or "  (no ports)"
    if candidates:
        raise LookupError(f"Several matching boards, choose one with --port:\n{listing}")
    raise LookupError(
        "Nano not found. Check the USB cable and the CH340 driver, or pass --port.\n"
        f"Available ports:\n{listing}"
    )


class NanoLink:
    """Serial connection to the Nano that reconnects after the cable is unplugged."""

    def __init__(self, port: Optional[str]) -> None:
        self._requested_port = port
        self._serial: Optional[serial.Serial] = None
        self._next_attempt = 0.0
        self._last_error = ""

    def connect(self) -> bool:
        if self._serial is not None:
            return True
        if time.monotonic() < self._next_attempt:
            return False
        self._next_attempt = time.monotonic() + RECONNECT_SECONDS
        try:
            port = self._requested_port or find_nano_port()
            self._serial = serial.Serial(port, BAUD_RATE, timeout=0, write_timeout=0.2)
        except (LookupError, serial.SerialException) as error:
            self._report(f"No connection to the Nano: {error}")
            return False
        print(f"Nano connected: {port}")
        self._last_error = ""
        time.sleep(BOOTLOADER_SECONDS)
        return True

    def send(self, line: bytes) -> None:
        if not self.connect():
            return
        try:
            self._serial.write(line)
        except (serial.SerialException, OSError) as error:
            self._report(f"Connection to the Nano lost: {error}")
            self.close()

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except (serial.SerialException, OSError):
                pass
            self._serial = None

    def _report(self, message: str) -> None:
        if message != self._last_error:  # don't repeat the same error on every attempt
            print(message, file=sys.stderr)
            self._last_error = message


def open_udp_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Without a timeout, recvfrom on Windows does not let Ctrl+C interrupt the script.
    sock.settimeout(0.5)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RECEIVE_BUFFER)
    try:
        sock.bind((UDP_HOST, UDP_PORT))
    except OSError as error:
        sock.close()
        raise SystemExit(
            f"Could not bind UDP port {UDP_PORT}: {error}\n"
            "Most likely another program is already listening on it (SimHub, a second run of the script)."
        )
    return sock


def receive_packets(
    sock: socket.socket,
    packets: "queue.Queue[bytes]",
    recorder: Optional[Recorder],
    stop: threading.Event,
) -> None:
    """Only receives: the recording gets every packet, the dashboard queue drops the oldest ones when full."""
    while not stop.is_set():
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:  # on Windows this is how ICMP "port unreachable" and socket closing show up
            if stop.is_set():
                return
            continue
        if recorder is not None:
            recorder.add(data)
        while True:
            try:
                packets.put_nowait(data)
                break
            except queue.Full:
                try:
                    packets.get_nowait()
                except queue.Empty:
                    pass


def run_game(link: NanoLink, recorder: Optional[Recorder] = None) -> None:
    sock = open_udp_socket()
    print(f"Waiting for telemetry on {UDP_HOST}:{UDP_PORT}. Exit: Ctrl+C.")
    packets: "queue.Queue[bytes]" = queue.Queue(DASHBOARD_QUEUE_PACKETS)
    stop = threading.Event()
    receiver = threading.Thread(target=receive_packets, args=(sock, packets, recorder, stop), daemon=True)
    receiver.start()
    link.connect()

    tracker = CarTracker()
    last_frame = last_slow = last_heartbeat = last_status = 0.0
    peak_rev_percent = 0
    last_packet = time.monotonic()
    warned_no_data = False
    warned_formats = set()

    try:
        while True:
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                last_heartbeat = now
                link.send(HEARTBEAT_LINE)
            if not warned_no_data and now - last_packet > NO_DATA_WARNING_SECONDS:
                warned_no_data = True
                print(
                    "\nNo packets. In the game: Settings > Telemetry Settings: UDP Telemetry = On, "
                    f"UDP IP = {UDP_HOST}, UDP Port = {UDP_PORT}, UDP Format = {EXPECTED_FORMAT}."
                )

            try:
                data = packets.get(timeout=0.5)
            except queue.Empty:
                continue

            now = time.monotonic()
            last_packet = now
            if warned_no_data:
                print("\nConnection to the game restored.")
                tracker._start_session(None)
            warned_no_data = False

            fmt = packet_format(data)
            if fmt is not None and fmt != EXPECTED_FORMAT and fmt not in warned_formats:
                warned_formats.add(fmt)
                print(
                    f"\nThe game sends format {fmt}, but {EXPECTED_FORMAT} is needed. "
                    f"Set Settings > Telemetry Settings > UDP Format = {EXPECTED_FORMAT} in the game."
                )

            frame = tracker.update(data)

            if now - last_slow >= SLOW_LINE_INTERVAL:
                line = tracker.next_slow_line()
                if line is not None:
                    last_slow = now
                    link.send(line)

            if frame is None:
                continue
            peak_rev_percent = max(peak_rev_percent, frame.rev_percent)
            if now - last_frame < FRAME_INTERVAL:
                continue
            last_frame = now
            link.send(frame.encode())

            if now - last_status >= 1.0:
                last_status = now
                print(
                    f"\r{frame.speed:3d} km/h | gear {frame.gear:2d} | "
                    f"{frame.rpm:5d} rpm | rev bar {frame.rev_percent:3d}% "
                    f"(max {peak_rev_percent:3d}%) | lamps {frame.rev_bits:015b} "
                    f"| ERS max {tracker.ers_peak_joules / 1e6:.2f} MJ   ",
                    end="",
                    flush=True,
                )
    finally:
        stop.set()
        receiver.join(timeout=2.0)
        sock.close()


def demo_frames() -> Iterator[Frame]:
    """Endless acceleration through the gears: 3 seconds per gear, the limit at the end."""
    steps = round(3 / FRAME_INTERVAL)
    while True:
        for gear in range(1, 9):
            for step in range(steps + 1):
                phase = step / steps
                yield Frame(
                    speed=int(40 * gear + 35 * phase),
                    gear=gear,
                    rpm=int(5000 + 7500 * phase),
                    rev_percent=int(100 * phase),
                    rev_bits=(1 << int(REV_LAMP_COUNT * phase)) - 1,
                )


def demo_tyres(seconds: float) -> Tyres:
    """Tyres wear over time and heat up in a wave so that updates are visible."""
    wear = int(seconds / 2) % 100
    heat = 90 + int(seconds) % 20
    return Tyres(
        compound="S",
        wear=(wear, min(100, wear + 3), min(100, wear + 6), wear),
        temperature=(heat, heat + 2, heat + 8, heat + 10),
        age_laps=int(seconds / 20) % 30,
    )


def demo_race(seconds: float) -> TextScreen:
    return race_screen(
        position=3,
        lap=1 + int(seconds / 20) % 53,
        total_laps=53,
        drs=int(seconds / 5) % 2 == 0,
        gap_ms=800 + int(seconds * 100) % 15000,
        last_lap_ms=92417,
    )


def demo_ers(seconds: float) -> TextScreen:
    phase = seconds / 10 % 2
    level = phase if phase < 1 else 2 - phase  # the charge smoothly rises and falls
    return ers_screen(level * ERS_MAX_JOULES, int(seconds / 5) % 4)


def demo_alert(seconds: float) -> Optional[TextScreen]:
    """Every 20 seconds a message for ALERT_SECONDS: pit stop and safety car in turn."""
    if seconds % 20 >= ALERT_SECONDS:
        return None
    return pit_alert(24, 5) if int(seconds / 20) % 2 else SAFETY_CAR_ALERTS[SAFETY_CAR_FULL]


def run_demo(link: NanoLink) -> None:
    print("Demo without the game: the Nano shows acceleration through the gears. Exit: Ctrl+C.")
    start = time.monotonic()
    last_slow = 0.0
    turn = 0
    for frame in demo_frames():
        now = time.monotonic()
        link.send(frame.encode())
        if now - last_slow >= SLOW_LINE_INTERVAL:
            last_slow = now
            seconds = now - start
            sources = [demo_tyres(seconds), demo_race(seconds), demo_ers(seconds)]
            demo_message = demo_alert(seconds)
            if demo_message is not None:
                sources.append(demo_message)
            link.send(sources[turn % len(sources)].encode())
            turn += 1
        time.sleep(FRAME_INTERVAL)


def main() -> None:
    parser = argparse.ArgumentParser(description="F1 25 telemetry bridge to an Arduino Nano")
    parser.add_argument("--port", help="Nano COM port, for example COM5 (detected automatically by default)")
    parser.add_argument("--demo", action="store_true", help="check the hardware without the game")
    parser.add_argument("--no-record", action="store_true", help="do not record telemetry into the races/ folder")
    parser.add_argument("--record-all-sessions", action="store_true", help="record not only races")
    parser.add_argument("--record-full-packets", action="store_true", help="record all game packets without filtering")
    args = parser.parse_args()

    link = NanoLink(args.port)
    recorder = None
    if not args.demo and not args.no_record:
        recorder = Recorder(all_sessions=args.record_all_sessions, full_packets=args.record_full_packets)
    if recorder is not None:
        recorder.start()
    try:
        if args.demo:
            run_demo(link)
        else:
            run_game(link, recorder)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        link.close()
        if recorder is not None:
            recorder.close()


if __name__ == "__main__":
    main()
