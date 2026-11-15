"""Race recording: the file opens by itself when a race starts and closes after the final classification.

By default only what the review needs is written, see PacketFilter. The packet format does not change, the rest is zeroed
and disappears in compression, so such a recording can be read the same way as a full one.

File format (.f1rec.gz, gzip): the MAGIC line, then records
    receive time (double, Unix seconds) | length (uint16) | packet bytes

Recording runs in its own thread and never stops receiving or the dashboard. Compressed data is regularly
flushed to disk, so if the game or the bridge crashes only the last seconds are lost.
"""
from __future__ import annotations

import collections
import datetime
import gzip
import pathlib
import queue
import struct
import sys
import threading
import time
import zlib
from typing import Iterator, Optional, Tuple

MAGIC = b"F1REC1\n"
RECORD = struct.Struct("<dH")
HEADER_MIN_SIZE = 29
SESSION_UID_OFFSET = 7  # m_packetFormat 2, four 1-byte fields, m_packetId 1
SESSION_UID = struct.Struct("<Q")
TIME_AND_FRAME = struct.Struct("<fI")
TIME_OFFSET = 15
PLAYER_OFFSET = 27
PACKET_ID_OFFSET = 6
HEADER_SIZE = 29
CAR_COUNT = 22

PID_MOTION, PID_SESSION, PID_LAP, PID_EVENT, PID_PARTICIPANTS, PID_SETUPS, PID_TELEMETRY, PID_STATUS = range(8)
PID_FINAL, PID_LOBBY, PID_DAMAGE, PID_HISTORY, PID_TYRE_SETS, PID_MOTION_EX, PID_TIME_TRIAL, PID_LAP_POSITIONS = range(8, 16)
SESSION_PACKET_SIZE = 753
SESSION_TYPE_OFFSET = HEADER_SIZE + 6
RACE_SESSIONS = {15, 16, 17}
END_GRACE_SECONDS = 15.0  # after the final classification the game sends the full history of all cars
PENDING_PACKETS = 2000  # while the session type is unknown; the Session packet arrives 2 times per second

# What PacketFilter skips
UNUSED_PACKETS = {PID_LOBBY, PID_TYRE_SETS, PID_TIME_TRIAL, PID_LAP_POSITIONS}
# Size of a car record and the packet tail
PER_CAR_PACKETS = {PID_MOTION: (60, 0), PID_LAP: (57, 2), PID_TELEMETRY: (60, 3), PID_STATUS: (55, 0)}
RIVAL_INTERVAL = 0.049  # rivals 20 times per second: at 300 km/h a point every 4 m
RIVAL_STATUS_INTERVAL = 1.0
DAMAGE_INTERVAL = 1.0
RARE_INTERVAL = 30.0  # history, setups and participants change rarely, the latest version is what matters

DEFAULT_DIR = pathlib.Path(__file__).resolve().parent / "races"
FLUSH_SECONDS = 5.0
QUEUE_PACKETS = 200_000  # about 10 minutes at 300 packets per second
GZIP_LEVEL = 1  # fast mode: the game must not wait for compression


def session_uid(data: bytes) -> Optional[int]:
    if len(data) < HEADER_MIN_SIZE:
        return None
    return SESSION_UID.unpack_from(data, SESSION_UID_OFFSET)[0]


class PacketFilter:
    """Keeps what the review needs.

    The player's own car goes at full rate. Rivals 20 times per second: position, pedals, speed, lap data; their status and
    damage once per second. After the final classification everything passes: the final history of the cars arrives.
    """

    def __init__(self) -> None:
        self._last: dict = {}
        self._frame: Optional[int] = None
        self._keep_rivals = True
        self._final_seen = False

    def apply(self, data: bytes) -> Optional[bytes]:
        if len(data) < HEADER_SIZE + 5:
            return data
        pid = data[PACKET_ID_OFFSET]
        if pid in UNUSED_PACKETS:
            return None
        t, frame = TIME_AND_FRAME.unpack_from(data, TIME_OFFSET)
        if frame != self._frame:
            self._frame = frame
            self._keep_rivals = self._due("rivals", t, RIVAL_INTERVAL)

        if pid in (PID_MOTION, PID_LAP, PID_TELEMETRY):
            return data if self._keep_rivals else self._player_only(pid, data)
        if pid == PID_STATUS:
            return data if self._due(pid, t, RIVAL_STATUS_INTERVAL) else self._player_only(pid, data)
        if pid == PID_EVENT:
            return None if data[HEADER_SIZE:HEADER_SIZE + 4] == b"BUTN" else data
        if pid == PID_FINAL:
            self._final_seen = True
            return data
        if self._final_seen:
            return data
        if pid == PID_DAMAGE:
            return data if self._due(pid, t, DAMAGE_INTERVAL) else None
        if pid == PID_HISTORY:
            return data if self._due((pid, data[HEADER_SIZE]), t, RARE_INTERVAL) else None
        if pid in (PID_SETUPS, PID_PARTICIPANTS):
            return data if self._due(pid, t, RARE_INTERVAL) else None
        return data

    def _due(self, key, t: float, period: float) -> bool:
        last = self._last.get(key)
        if last is None or t < last or t - last >= period:  # time goes backwards after a flashback
            self._last[key] = t
            return True
        return False

    @staticmethod
    def _player_only(pid: int, data: bytes) -> bytes:
        record, tail = PER_CAR_PACKETS[pid]
        player = data[PLAYER_OFFSET]
        if player >= CAR_COUNT or len(data) != HEADER_SIZE + CAR_COUNT * record + tail:
            return data  # spectator or an unexpected size: leave it alone
        start = HEADER_SIZE + player * record
        return (data[:HEADER_SIZE] + bytes(player * record) + data[start:start + record]
                + bytes((CAR_COUNT - 1 - player) * record) + data[len(data) - tail:len(data)])


class Recorder:
    """Accepts packets from any thread, writes them in its own. Disk errors do not bring the bridge down."""

    def __init__(self, directory: pathlib.Path = DEFAULT_DIR, all_sessions: bool = False, full_packets: bool = False) -> None:
        self._directory = pathlib.Path(directory)
        self._all_sessions = all_sessions
        self._full_packets = full_packets
        self._filter = PacketFilter()
        self._pending: collections.deque = collections.deque(maxlen=PENDING_PACKETS)
        self._closed_uids = set()
        self._end_at: Optional[float] = None
        self._queue: "queue.Queue[Optional[Tuple[float, bytes]]]" = queue.Queue(QUEUE_PACKETS)
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._file: Optional[gzip.GzipFile] = None
        self._uid: Optional[int] = None
        self._last_flush = 0.0
        self._failed = False
        self.dropped = 0
        self.written = 0
        self.paths = []

    def start(self) -> None:
        self._thread.start()

    def add(self, data: bytes) -> None:
        try:
            self._queue.put_nowait((time.time(), data))
        except queue.Full:
            self.dropped += 1

    def close(self) -> None:
        """Writes out the queue and closes the file."""
        self._queue.put(None)
        self._thread.join()
        if self.dropped:
            print(f"Recording: packets lost {self.dropped}", file=sys.stderr)

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                self._flush_if_due()
                self._finish_if_due()
                continue
            if item is None:
                break
            self._write(*item)
            self._flush_if_due()
            self._finish_if_due()
        self._close_file()

    def _write(self, received: float, data: bytes) -> None:
        if self._failed:
            self.dropped += 1
            return
        uid = session_uid(data)
        if not uid or uid in self._closed_uids:  # short packet, menu without a session, not a race, or the race is already recorded
            return
        try:
            if uid != self._uid:
                self._close_file()
                self._uid = uid
                self._pending.clear()
                self._filter = PacketFilter()
                self._end_at = None
            if self._file is None and not self._decide(uid, received, data):
                return
            self._store(received, data)
        except OSError as error:
            self._failed = True
            self.dropped += 1
            print(f"\nTelemetry recording stopped, the dashboard keeps working: {error}", file=sys.stderr)

    def _decide(self, uid: int, received: float, data: bytes) -> bool:
        """Recording starts as soon as it is clear this is a race. Packets that arrived earlier are not lost."""
        if self._all_sessions:
            self._open(uid, received)
            return True
        kind = data[SESSION_TYPE_OFFSET] if len(data) == SESSION_PACKET_SIZE and data[PACKET_ID_OFFSET] == PID_SESSION else 0
        if kind in RACE_SESSIONS:
            self._open(uid, received)
            for item in self._pending:
                self._store(*item)
            self._pending.clear()
            return True
        if kind:  # the session is known and it is not a race
            self._closed_uids.add(uid)
            self._pending.clear()
        else:
            self._pending.append((received, data))
        return False

    def _store(self, received: float, data: bytes) -> None:
        if data[PACKET_ID_OFFSET:PACKET_ID_OFFSET + 1] == bytes([PID_FINAL]):
            self._end_at = time.monotonic() + END_GRACE_SECONDS
        kept = data if self._full_packets else self._filter.apply(data)
        if kept is None:
            return
        self._file.write(RECORD.pack(received, len(kept)))
        self._file.write(kept)
        self.written += 1

    def _finish_if_due(self) -> None:
        if self._end_at is None or time.monotonic() < self._end_at:
            return
        self._end_at = None
        self._closed_uids.add(self._uid)
        path = self.paths[-1] if self.paths else ""
        self._close_file()
        print(f"\nThe race is over, recording saved: {path}")

    def _open(self, uid: int, received: float) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.fromtimestamp(received).strftime("%Y-%m-%d_%H-%M-%S")
        path = self._directory / f"{stamp}_{uid:016x}.f1rec.gz"
        self._file = gzip.open(path, "wb", compresslevel=GZIP_LEVEL)
        self._file.write(MAGIC)
        self._uid = uid
        self.paths.append(path)
        print(f"\nRecording session: {path}")

    def _flush_if_due(self) -> None:
        now = time.monotonic()
        if self._file is None or self._failed or now - self._last_flush < FLUSH_SECONDS:
            return
        self._last_flush = now
        try:
            self._file.flush()
        except OSError as error:
            self._failed = True
            print(f"\nTelemetry recording stopped, the dashboard keeps working: {error}", file=sys.stderr)

    def _close_file(self) -> None:
        if self._file is None:
            return
        try:
            self._file.close()
        except OSError as error:
            print(f"\nThe recording file closed with an error: {error}", file=sys.stderr)
        self._file = None
        self._uid = None


def read_packets(path) -> Iterator[Tuple[float, bytes]]:
    """Yields (receive time, packet). A file cut off by a crash is read up to the break."""
    with gzip.open(path, "rb") as source:
        try:
            if source.read(len(MAGIC)) != MAGIC:
                raise ValueError(f"{path}: this is not a telemetry recording file")
            while True:
                head = source.read(RECORD.size)
                if len(head) < RECORD.size:
                    return
                received, length = RECORD.unpack(head)
                data = source.read(length)
                if len(data) < length:
                    return
                yield received, data
        except (EOFError, OSError, zlib.error):
            return
