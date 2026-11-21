#!/usr/bin/env python3
"""F1 25 web dashboard for a second screen: car condition, temperatures, pit stop, penalties.

Usually started by the bridge (f1_bridge.py prints the address). Standalone, without the game:

    py -3 f1_web.py --demo                 test race, the page gets buttons that trigger events
    py -3 f1_web.py --replay               replay the latest recording from races/
    py -3 f1_web.py --replay FILE --speed 4 --skip 10    a given recording, 4x faster, from minute 10
    py -3 f1_web.py --live                 dashboard only, from the game, without the Nano and recording

Common flags: --port 8025, --host 0.0.0.0 (open from a tablet on the same network), --open (open the browser).
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import pathlib
import socket
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from f1_state import DashboardState

ROOT = pathlib.Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8025
PUSH_INTERVAL = 0.1  # 10 page updates per second
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}


class DashboardServer:
    """Dashboard HTTP server on its own thread. Anyone may update the state, the page receives it over SSE."""

    def __init__(self, state: DashboardState, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, demo=None) -> None:
        self.state = state
        self.demo = demo
        self.demo_lock = threading.Lock()
        self.stopping = threading.Event()
        handler = type("Handler", (_Handler,), {"dashboard": self})
        self._server = ThreadingHTTPServer((host, port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        shown_host = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
        self.url = f"http://{shown_host}:{port}/"

    def start(self) -> "DashboardServer":
        self._thread.start()
        return self

    def close(self) -> None:
        self.stopping.set()
        self._server.shutdown()
        self._server.server_close()

    def info(self) -> dict:
        actions = self.demo.ACTIONS if self.demo is not None else {}
        return {"mode": self.state.mode, "actions": actions}


class _Handler(BaseHTTPRequestHandler):
    dashboard: DashboardServer
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # the bridge console is busy with its status line
        pass

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in STATIC_FILES:
            self._file(WEB_DIR / STATIC_FILES[path])
        elif path == "/state":
            self._json(self.dashboard.state.snapshot())
        elif path == "/info":
            self._json(self.dashboard.info())
        elif path == "/events":
            self._events()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        demo = self.dashboard.demo
        prefix = "/demo/"
        if demo is None or not path.startswith(prefix) or path[len(prefix):] not in demo.ACTIONS:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        with self.dashboard.demo_lock:
            demo.act(path[len(prefix):])
        self._json({"ok": True})

    def _file(self, file: pathlib.Path) -> None:
        try:
            body = file.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (mimetypes.guess_type(file.name)[0] or "text/plain") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # page edits show up on reload without clearing the cache
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while not self.dashboard.stopping.is_set():
                body = json.dumps(self.dashboard.state.snapshot(), ensure_ascii=False)
                self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(PUSH_INTERVAL)
        except (ConnectionError, OSError):  # the page was closed
            return


def start_server(state: DashboardState, host: str, port: int, demo=None) -> Optional[DashboardServer]:
    """Starts the server; if the port is taken, prints why and returns None so the bridge keeps running."""
    try:
        server = DashboardServer(state, host, port, demo).start()
    except OSError as error:
        print(f"Web dashboard not started, port {port} is taken: {error}. Pick another one with --web-port.",
              file=sys.stderr)
        return None
    print(f"Web dashboard: {server.url}")
    return server


# --- modes without the game ---

def run_demo(state: DashboardState, server: DashboardServer) -> None:
    race = server.demo
    print("Test race: a lap takes 20 seconds, events from the Test menu on the page. Quit: Ctrl+C.")
    previous = time.monotonic()
    while True:
        time.sleep(0.05)
        now = time.monotonic()
        with server.demo_lock:
            packets = race.step(now - previous)
        previous = now
        for packet in packets:
            state.update(packet)


def find_recording(name: Optional[str]) -> pathlib.Path:
    from f1_record import DEFAULT_DIR
    if name:
        path = pathlib.Path(name)
        if not path.exists():
            raise SystemExit(f"No such file: {path}")
        return path
    files = sorted(pathlib.Path(DEFAULT_DIR).glob("*.f1rec.gz"))
    if not files:
        raise SystemExit(f"No recordings in {DEFAULT_DIR}. Run the bridge with the game or use --demo.")
    return files[-1]


def run_replay(state: DashboardState, path: pathlib.Path, speed: float, skip_minutes: float) -> None:
    from f1_record import read_packets
    print(f"Replaying {path.name} at x{speed:g}, looped. Quit: Ctrl+C.")
    while True:
        first = None
        started = time.monotonic()
        for received, data in read_packets(path):
            if first is None:
                first = received
            moment = (received - first - skip_minutes * 60) / speed
            if moment < 0:
                state.update(data)  # skipped part: the state catches up without pauses
                continue
            delay = started + moment - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            state.update(data)
        if first is None:
            raise SystemExit(f"{path}: the file has no packets")
        print("Recording finished, starting over.")
        time.sleep(2)


def run_live(state: DashboardState) -> None:
    """Dashboard only: listens to the game itself, no Nano and no recording. The bridge must not hold port 20777."""
    from f1_bridge import UDP_HOST, UDP_PORT, open_udp_socket
    sock = open_udp_socket()
    print(f"Waiting for telemetry on {UDP_HOST}:{UDP_PORT}. Quit: Ctrl+C.")
    try:
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            state.update(data)
    finally:
        sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="F1 25 web dashboard for a second screen")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="test race without the game")
    mode.add_argument("--replay", nargs="?", const="", metavar="FILE", help="replay a recording (the latest by default)")
    mode.add_argument("--live", action="store_true", help="game data, without the Nano and recording")
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed")
    parser.add_argument("--skip", type=float, default=0.0, metavar="MIN", help="start the replay at this minute")
    parser.add_argument("--host", default=DEFAULT_HOST, help="0.0.0.0 to open the page from another device")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    args = parser.parse_args()

    state = DashboardState()
    demo = None
    if args.demo:
        from f1_demo import DemoRace
        demo = DemoRace()
        state.mode = "demo"
    elif args.replay is not None:
        state.mode = "replay"
    server = start_server(state, args.host, args.port, demo)
    if server is None:
        raise SystemExit(1)
    if args.open:
        webbrowser.open(server.url)
    try:
        if args.demo:
            run_demo(state, server)
        elif args.replay is not None:
            run_replay(state, find_recording(args.replay), max(0.1, args.speed), max(0.0, args.skip))
        else:
            run_live(state)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.close()


if __name__ == "__main__":
    main()
