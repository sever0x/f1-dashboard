"""Data for the race review page: everything the charts draw, as one JSON.

    py -3 tools/race_review_data.py [recording.f1rec.gz] [--window 7:4150:4420:"Spin"] -o data.json

Without a file the last race is taken. --window adds the telemetry of a lap segment next to the best clean lap
(can be given several times). The page is built by tools/race_review_page.py, the review procedure: docs/RACE_REVIEW.md.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import race_report as rr  # noqa: E402

TEAMS = {0: "Mercedes", 1: "Ferrari", 2: "Red Bull", 3: "Williams", 4: "Aston Martin", 5: "Alpine", 6: "RB", 7: "Haas",
         8: "McLaren", 9: "Sauber"}
MAP_STEP = 10.0
WINDOW_STEP = 2.0
POINTS_PLACES = 10


def r1(value, digits=1):
    return None if value is None else round(float(value), digits)


def cumulative(history: dict) -> list:
    total, out = 0, []
    for item in history["laps"]:
        if not item["ms"]:
            break
        total += item["ms"]
        out.append(total)
    return out


def positions_by_lap(race) -> dict:
    """Each car's position at the end of a lap by total time: cars with fewer laps are lower."""
    cums = {car: cumulative(h) for car, h in race.history.items()}
    laps = max((len(c) for c in cums.values()), default=0)
    out = {}
    for lap in range(1, laps + 1):
        done = sorted((c[lap - 1], car) for car, c in cums.items() if len(c) >= lap)
        out[lap] = {car: i + 1 for i, (_, car) in enumerate(done)}
    return out


def window(race, views, lap, start, end, ref_lap):
    t = race.trace
    out = {}
    for name, number in (("lap", lap), ("ref", ref_lap)):
        view = views.get(number)
        if not view or not view.dist:
            continue
        rows = []
        d = start
        while d <= end:
            rows.append([round(d)] + [r1(view.value_at(getattr(t, col), d), 3)
                                      for col in ("speed", "throttle", "brake", "steer", "slip", "gear")])
            d += WINDOW_STEP
        out[name] = rows
    return out


def build(path, windows):
    race = rr.load(path)
    result = rr.analyze(race)
    player = race.player
    names = {car: (race.names.get(car) or f"car {car}") for car in race.history}
    final = race.final
    team = race.teams.get(player)
    mates = [car for car, tm in race.teams.items() if tm == team and car != player]
    length = race.session.get("length", 0)
    views = result["views"]
    best = result.get("best")
    ref_lap = best["lap"] if best else None
    ref_view = views.get(ref_lap)

    # Classification
    order = sorted(final.items(), key=lambda kv: kv[1].get("position", 99))
    classification = [{"car": car, "name": names.get(car, str(car)), "team": TEAMS.get(race.teams.get(car), "?"),
                       "pos": f.get("position"), "grid": f.get("grid"), "laps": f.get("laps"), "stops": f.get("stops"),
                       "best_ms": f.get("best_ms"), "total_s": r1(f.get("total"), 3), "status": f.get("status"),
                       "points": f.get("points"), "player": car == player, "mate": car in mates} for car, f in order]

    # Gap at the end of each lap: to the teammate, to the car ahead at the finish, to the last points place
    cums = {car: cumulative(h) for car, h in race.history.items()}
    me = cums.get(player, [])
    my_pos = final.get(player, {}).get("position")
    targets = {}
    for car, f in final.items():
        if car in mates:
            targets[car] = "mate"
    for car, f in final.items():
        if my_pos and f.get("position") == my_pos - 1:
            targets.setdefault(car, "ahead")
        if my_pos and my_pos > POINTS_PLACES and f.get("position") == POINTS_PLACES:
            targets.setdefault(car, "points")
    gaps = []
    for car, kind in targets.items():
        other = cums.get(car, [])
        # The page shows the label for "kind" in the review's language; "places" fills the points label
        gaps.append({"car": car, "name": names.get(car), "kind": kind, "places": POINTS_PLACES,
                     "pos": final.get(car, {}).get("position"),
                     "gap_s": [r1((me[i] - other[i]) / 1000, 2) for i in range(min(len(me), len(other)))]})

    pos_lap = positions_by_lap(race)

    laps = []
    for row in result["laps"]:
        laps.append({
            "lap": row["lap"], "ms": row["ms"], "s1": row.get("s1"), "s2": row.get("s2"), "s3": row.get("s3"),
            "pos": row.get("position"), "compound": row.get("compound"), "age": row.get("age"), "pit": bool(row.get("pit")),
            "clean": bool(row.get("clean")), "to_field_ms": r1(row.get("to_field_ms"), 0),
            "traffic": row.get("traffic_pct"), "contacts": row.get("collisions", 0),
            "ers_end": r1(row["ers_end"] / rr.ERS_MAX_JOULES * 100, 0) if row.get("ers_end") is not None else None,
            "fuel": r1(row.get("fuel")), "fuel_laps": r1(row.get("fuel_laps"), 2),
            "wear": [r1(w) for w in row.get("wear", [])],
            "surface": list(row.get("surface", [])), "inner": list(row.get("inner", [])),
            "warnings": row.get("warnings"), "cuts": row.get("cuts"),
        })

    # Track map from the best lap
    track = []
    corners = []
    if ref_view and length:
        t = race.trace
        d = 0.0
        while d < length:
            track.append([round(ref_view.value_at(t.x, d), 1), round(ref_view.value_at(t.z, d), 1),
                          round(ref_view.value_at(t.speed, d))])
            d += MAP_STEP
        sums = {item["corner"].number: item for item in result.get("summaries", [])}
        for c in result["corners"]:
            s = sums.get(c.number, {})
            corners.append({"n": c.number, "apex": round(c.apex), "start": round(c.start), "end": round(c.end),
                            "x": round(ref_view.value_at(t.x, c.apex), 1), "z": round(ref_view.value_at(t.z, c.apex), 1),
                            "min_speed": round(c.speed), "loss_ms": r1(s.get("loss_ms"), 0),
                            "median_ms": r1(s.get("median_ms"), 0), "spread_ms": r1(s.get("spread_ms"), 0),
                            "wheelspin_laps": s.get("wheelspins", []), "off_laps": s.get("offs", []),
                            "fast": {k: r1(v, 0) for k, v in s.get("fast", {}).items()},
                            "slow": {k: r1(v, 0) for k, v in s.get("slow", {}).items()}})

    def where(dist):
        if not ref_view or dist is None:
            return None, None
        t = race.trace
        return round(ref_view.value_at(t.x, dist), 1), round(ref_view.value_at(t.z, dist), 1)

    events = []
    for e in race.events:
        code = e["code"]
        if code == "COLL" and player in e["cars"]:
            other, kind = e["cars"][0] if e["cars"][1] == player else e["cars"][1], "contact"
            text = names.get(other, str(other))
        elif code == "OVTK" and player in e["cars"]:
            first, second = e["cars"]
            kind = "pass" if first == player else "passed"
            text = names.get(second if first == player else first)
        elif code == "PENA" and e.get("car") == player:
            kind = "penalty"
            text = f"{rr.PENALTY_TYPES.get(e['penalty'], e['penalty'])}: {rr.INFRINGEMENTS.get(e['infringement'], e['infringement'])}"
        else:
            continue
        x, z = where(e.get("dist"))
        events.append({"lap": e.get("lap"), "dist": r1(e.get("dist"), 0), "kind": kind, "text": text, "x": x, "z": z})

    mistakes = [{"lap": m["lap"], "corner": m["corner"], "lost_s": r1(m["lost_ms"] / 1000, 2),
                 "brake_m": r1(m.get("brake_m"), 0), "min_speed": r1(m.get("min_speed"), 0),
                 "lockup": m["lockup"], "wheelspin": m["wheelspin"], "off": m["off_track"], "traffic": m["traffic"]}
                for m in result.get("mistakes", [])[:20]]

    field = [{"name": row["name"], "car": row["car"], "median_ms": row["median_ms"], "best_ms": row["best_ms"],
              "laps": row["laps"], "stints": row["stints"], "player": row["car"] == player, "mate": row["car"] in mates}
             for row in result["field"]["rows"]]

    stints = [{"first": s["first"], "last": s["last"], "compound": s["compound"], "clean": s["clean"],
               "median_ms": s["median_ms"], "slope_ms": r1(s["slope_ms"], 0), "field_slope_ms": r1(s["field_slope_ms"], 0),
               "wear_per_lap": [r1(w, 2) for w in (s["wear_per_lap"] or [])]} for s in result.get("stints", [])]

    win = []
    for spec in windows:
        lap, start, end, *label = spec.split(":", 3)
        win.append({"lap": int(lap), "start": float(start), "end": float(end), "label": label[0] if label else "",
                    "ref_lap": ref_lap, "data": window(race, views, int(lap), float(start), float(end), ref_lap)})

    s = race.session
    return {
        "meta": {
            "file": pathlib.Path(path).name, "track": rr.TRACKS.get(s.get("track"), str(s.get("track"))),
            "session": rr.SESSION_TYPES.get(s.get("kind"), str(s.get("kind"))), "length": length,
            "total_laps": s.get("total_laps"), "ai": s.get("ai"), "online": bool(s.get("network")),
            "team": TEAMS.get(team, str(team)), "mates": [names.get(c) for c in mates],
            "assists": {"tc": race.assists[0], "abs": race.assists[1], "gearbox": s.get("gearbox"),
                        "ers": s.get("ers_assist"), "drs": s.get("drs_assist"), "line": s.get("racing_line"),
                        "equal_cars": s.get("equal_cars")},
            "reaction_s": r1(race.first_move - race.lights_out, 2) if race.first_move and race.lights_out is not None else None,
            "ref_lap": ref_lap, "pit_window": list(race.pit_window),
            "minutes": r1((race.last_wall - race.first_wall) / 60) if race.first_wall else None,
        },
        "classification": classification, "gaps": gaps,
        "positions": {str(lap): {str(car): p for car, p in m.items()} for lap, m in pos_lap.items()},
        "laps": laps, "stints": stints, "field": field, "track": track, "corners": corners,
        "events": events, "mistakes": mistakes, "windows": win,
    }


def main():
    parser = argparse.ArgumentParser(description="Data for the race review page")
    parser.add_argument("path", nargs="?")
    parser.add_argument("--window", action="append", default=[], help="lap:from_m:to_m:label")
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args()
    path = args.path or rr.choose(rr.ROOT / "races", False)
    data = build(path, args.window)
    pathlib.Path(args.out).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Done: {args.out}")


if __name__ == "__main__":
    main()
