#!/usr/bin/env python3
"""Best lap video: a lap recorded from the replay, telemetry data on top and a title card with a song at the end.

    py -3 tools/lap_video.py RECORDING.f1rec.gz --clip CLIP.mp4 --lap-start 6.917 --trim 1 \\
        --first FIRST --last LAST --song SONG.mp3

--lap-start: the clip second at which the car crosses the line at the start of the lap, before trimming by --trim.
The lap is the player's best by default, another one is set with --lap. The panels cover the replay interface plates
at 2560x1440. Output: races/reports/<recording>/fastest_lap.mp4. Needs ffmpeg with libass.
"""
from __future__ import annotations

import argparse
import bisect
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from race_report import load  # noqa: E402

WIDTH, HEIGHT, FPS = 2560, 1440, 60
FONT_FILE = pathlib.Path(r"C:\Windows\Fonts\bahnschrift.ttf")
FONT = "Bahnschrift"
CROSSFADE = 0.8
ENDING_FADE = 0.8
HOLD_AFTER_FINISH = 2.0  # this many seconds of video remain after the lap finish
GAME_GAIN_DB = 26  # the game sound in the OBS recording was about -43 dB
SONG_GAIN_DB = -3

TEAMS = {0: "Mercedes", 1: "Ferrari", 2: "Red Bull", 3: "Williams", 4: "Aston Martin", 5: "Alpine", 6: "RB", 7: "Haas",
         8: "McLaren", 9: "Sauber"}
GRANDS_PRIX = {3: "Bahrain", 11: "Italian", 15: "United States", 19: "Mexico City", 20: "Azerbaijan", 26: "Dutch",
               27: "Emilia Romagna"}
# Colours in RGB order, simple tricolours: vertical stripes
FLAGS = {19: ("006847", "FFFFFF", "CE1126"), 11: ("009246", "FFFFFF", "CE2B37"), 27: ("009246", "FFFFFF", "CE2B37")}

WHITE, DIM, PANEL = "FFFFFF", "9AA0B0", "12141F"
PURPLE, GREEN, RED, PINK = "C04BFF", "37D67A", "FF3B3B", "FF87BC"


def bgr(rgb: str) -> str:
    return f"&H{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}&"


def stamp(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def lap_time(ms: int) -> str:
    return f"{ms // 60000}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def rect(x, y, w, h, color, alpha="00", extra="") -> str:
    return (f"{{\\an7\\pos({x},{y})\\bord0\\shad0\\1c{bgr(color)}\\1a&H{alpha}&{extra}\\p1}}"
            f"m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}")


def text(x, y, value, size, color=WHITE, align=7, extra="") -> str:
    return f"{{\\an{align}\\pos({x},{y})\\fs{size}\\bord0\\shad0\\1c{bgr(color)}{extra}}}{value}"


class Ass:
    def __init__(self) -> None:
        self.lines = []

    def add(self, start: float, end: float, layer: int, body: str) -> None:
        self.lines.append(f"Dialogue: {layer},{stamp(start)},{stamp(end)},T,,0,0,0,,{body}")

    def add_changing(self, moments, layer: int, make) -> None:
        """moments: (time, value) pairs. Equal consecutive values are merged into one event."""
        start, value = moments[0]
        for moment, current in moments[1:]:
            if current != value:
                self.add(start, moment, layer, make(value))
                start, value = moment, current
        self.add(start, moments[-1][0], layer, make(value))

    def write(self, path: pathlib.Path) -> None:
        head = [
            "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {WIDTH}", f"PlayResY: {HEIGHT}", "WrapStyle: 2",
            "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding",
            f"Style: T,{FONT},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
            "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        path.write_text("\n".join(head + self.lines) + "\n", encoding="utf-8")


def overlay(race, info: dict, start: float, length: float) -> Ass:
    """Panels over the lap. start: the lap start in seconds of the already trimmed video."""
    ass = Ass()
    trace = race.trace
    ms = info["ms"]
    finish = start + ms / 1000
    frames = [n / FPS for n in range(round(length * FPS) + 1)]

    # Interface plates: lap at the top left, REPLAY at the top right, hints at the bottom right
    ass.add(0, length, 0, rect(44, 44, 410, 184, PANEL))
    ass.add(0, length, 0, rect(2030, 44, 486, 184, PANEL))
    ass.add(0, length, 0, rect(1940, 1232, 576, 192, PANEL))

    def label(done: bool) -> str:
        if done:
            return text(70, 62, info["title"], 40, PURPLE)
        return text(70, 62, f"LAP {info['lap']} / {info['total']}", 40, DIM)

    ass.add_changing([(t, t >= finish) for t in frames], 1, label)

    def timer(value) -> str:
        shown, done = value
        return text(70, 118, lap_time(shown), 84, PURPLE if done else WHITE)

    moments = [(t, (min(ms, max(0, round((t - start) * 1000))), t >= finish)) for t in frames]
    ass.add_changing(moments, 1, timer)

    passed = 0
    for row, sector in enumerate(info["sectors"]):
        y = 60 + row * 54
        passed += sector
        moment = min(start + passed / 1000, finish)
        ass.add(0, length, 1, text(2056, y, f"S{row + 1}", 44, DIM))
        ass.add(0, moment, 1, text(2490, y, "--.---", 44, DIM, align=9))
        ass.add(moment, length, 1, text(2490, y, f"{sector / 1000:.3f}", 44, WHITE, align=9))

    times = trace.t
    lap_rows = [i for i in range(len(trace)) if trace.lap[i] == info["lap"]]
    telemetry_start = times[lap_rows[0]]

    def sample(column, t: float) -> float:
        i = min(bisect.bisect_left(times, telemetry_start + t - start), len(trace) - 1)
        return column[i]

    grid = [n / 30 for n in range(round(length * 30) + 1)]
    ass.add(0, length, 1, text(2240, 1344, "km/h", 36, DIM))
    ass.add(0, length, 1, text(2400, 1254, "GEAR", 32, DIM))
    ass.add_changing([(t, round(sample(trace.speed, t))) for t in grid], 1,
                     lambda value: text(2224, 1248, str(value), 116, WHITE, align=9))
    ass.add_changing([(t, round(sample(trace.gear, t))) for t in grid], 1,
                     lambda value: text(2430, 1292, {0: "N", -1: "R"}.get(value, str(value)), 92, PINK, align=8))

    bar = 500
    ass.add(0, length, 1, rect(1976, 1388, bar, 12, "2A2E3D"))
    ass.add(0, length, 1, rect(1976, 1404, bar, 12, "2A2E3D"))
    ass.add_changing([(t, round(sample(trace.throttle, t) * 50) * 10) for t in grid], 2,
                     lambda value: rect(1976, 1388, value, 12, GREEN) if value else "")
    ass.add_changing([(t, round(sample(trace.brake, t) * 50) * 10) for t in grid], 2,
                     lambda value: rect(1976, 1404, value, 12, RED) if value else "")
    return ass


def card(info: dict, first: str, last: str, duration: float) -> Ass:
    ass = Ass()
    centre = WIDTH // 2
    ass.add(0, duration, 0, rect(0, 0, WIDTH, HEIGHT, "0B0D16", "80"))

    def appear(moment: float, layer: int, body: str) -> None:
        ass.add(moment, duration, layer, "{\\fad(450,0)}" + body)

    if info["flag"]:
        for i, color in enumerate(info["flag"]):
            appear(0.5, 1, rect(centre - 90 + i * 60, 110, 60, 120, color))
    appear(0.5, 1, text(centre, 262, info["gp"].upper(), 84, WHITE, align=8))
    appear(0.5, 1, text(centre, 352, "GRAND PRIX", 84, WHITE, align=8))
    appear(1.2, 1, text(centre, 520, first.upper(), 130, WHITE, align=8,
                        extra=f"\\bord4\\3c{bgr(WHITE)}\\1a&HFF&\\fsp8"))
    appear(1.6, 1, text(centre, 660, last.upper(), 220, WHITE, align=8,
                        extra="\\fscx112\\fscy112\\t(0,450,\\fscx100\\fscy100)"))
    appear(2.2, 1, rect(centre - 620, 925, 1240, 6, PINK))
    appear(2.4, 1, text(centre, 960, info["team"].upper(), 64, PINK, align=8, extra="\\fsp14"))
    appear(3.0, 1, text(centre, 1062, info["title"], 60, WHITE, align=8, extra="\\fsp12"))
    appear(3.4, 1, text(centre, 1136, lap_time(info["ms"]), 210, WHITE, align=8,
                        extra=f"\\bord5\\3c{bgr(PINK)}\\1c{bgr('141019')}\\fscx112\\fscy112\\t(0,450,\\fscx100\\fscy100)"))
    return ass


def lap_info(race, lap: int, gp: str, team: str) -> dict:
    player = race.player
    history = race.history[player]
    lap = lap or history["best_lap"]
    row = history["laps"][lap - 1]
    best = [car["best_ms"] for car in race.final.values() if car["best_ms"]]
    track = race.session.get("track")
    return {
        "lap": lap, "total": race.session.get("total_laps"), "ms": row["ms"], "sectors": (row["s1"], row["s2"], row["s3"]),
        "title": "FASTEST LAP" if best and row["ms"] <= min(best) else "BEST LAP",
        "gp": gp or GRANDS_PRIX.get(track, ""), "flag": FLAGS.get(track),
        "team": team or TEAMS.get(race.teams.get(player), ""),
    }


def duration_of(path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                         check=True, capture_output=True, text=True)
    return float(out.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recording", type=pathlib.Path)
    parser.add_argument("--clip", type=pathlib.Path, required=True)
    parser.add_argument("--lap-start", type=float, required=True)
    parser.add_argument("--trim", type=float, default=0.0, help="how many seconds to cut at the start of the clip")
    parser.add_argument("--lap", type=int, default=0)
    parser.add_argument("--first", required=True)
    parser.add_argument("--last", required=True)
    parser.add_argument("--song", type=pathlib.Path, required=True)
    parser.add_argument("--gp", default="", help="name without the words Grand Prix")
    parser.add_argument("--team", default="")
    parser.add_argument("--background-at", type=float, default=30.0, help="clip second for the title card background")
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args()

    race = load(args.recording)
    info = lap_info(race, args.lap, args.gp, args.team)
    print(f"Lap {info['lap']}: {lap_time(info['ms'])}, {info['title']}, {info['team']}, {info['gp']} Grand Prix")

    name = args.recording.name.replace(".f1rec.gz", "")
    report_dir = args.recording.parent / "reports" / name
    out = (args.out or report_dir / "fastest_lap.mp4").resolve()
    work = report_dir / "video_work"
    (work / "fonts").mkdir(parents=True, exist_ok=True)
    shutil.copy(FONT_FILE, work / "fonts")

    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(args.clip)], check=True, capture_output=True, text=True).stdout.strip()
    if size != f"{WIDTH},{HEIGHT}":
        print(f"Clip is {size}, {WIDTH}x{HEIGHT} is needed: the panels are fitted to the replay plates at this size", file=sys.stderr)
        return 1

    clip_end = duration_of(args.clip)
    song_length = duration_of(args.song)
    finish = args.lap_start + info["ms"] / 1000
    if finish > clip_end:
        print(f"The clip does not contain the end of the lap: {finish:.1f} s needed, {clip_end:.1f} s available", file=sys.stderr)
        return 1
    end = min(clip_end - 0.05, finish + HOLD_AFTER_FINISH)
    length = end - args.trim
    offset = length - CROSSFADE

    overlay(race, info, args.lap_start - args.trim, length).write(work / "lap.ass")
    card(info, args.first, args.last, song_length).write(work / "card.ass")

    clip, song = str(args.clip.resolve()), str(args.song.resolve())
    gradient = (f"gradients=s={WIDTH}x{HEIGHT}:c0=0x0A2A6B:c1=0x8C2F78:n=2:speed=0.00001:"
                f"x0=0:y0=0:x1={WIDTH}:y1={HEIGHT}")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", gradient,
                    "-ss", str(args.background_at), "-i", clip, "-frames:v", "1", "-filter_complex",
                    f"[1:v]crop=2000:1125:280:225,scale={WIDTH}:{HEIGHT},gblur=sigma=10,format=gbrp[f];"  # without the replay plates
                    "[0:v]format=gbrp[g];[g][f]blend=all_mode=normal:all_opacity=0.25,vignette",
                    "bg.png"], check=True, cwd=work)
    graph = ";".join([
        f"[0:v]trim=start={args.trim}:end={end},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p,ass=lap.ass:fontsdir=fonts,settb=AVTB[lap]",
        f"[1:v]fps={FPS},format=yuv420p,ass=card.ass:fontsdir=fonts,fade=t=out:st={song_length - ENDING_FADE}:d={ENDING_FADE},settb=AVTB[card]",
        f"[lap][card]xfade=transition=fade:duration={CROSSFADE}:offset={offset},format=yuv420p[v]",
        f"[0:a]atrim=start={args.trim}:end={end},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,"
        f"volume={GAME_GAIN_DB}dB,afade=t=out:st={offset}:d={CROSSFADE}[game]",
        f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={SONG_GAIN_DB}dB,afade=t=out:st={song_length - ENDING_FADE}:d={ENDING_FADE},"
        f"adelay={round(offset * 1000)}:all=1[song]",
        "[game][song]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.95[a]",
    ])
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", clip,
                    "-loop", "1", "-framerate", str(FPS), "-t", str(song_length), "-i", "bg.png", "-i", song,
                    "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                    # The Windows player does not open H.264 4:4:4, so the profile and the format are set explicitly
                    "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0",
                    "-maxrate", "50M", "-bufsize", "100M", "-profile:v", "high", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(out)],
                   check=True, cwd=work)
    print(f"Done: {out}, {duration_of(out):.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
