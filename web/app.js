"use strict";

/* F1 25 web dashboard. f1_web.py pushes the state over SSE (/events) 10 times per second.
   Every wheel array is ordered FL, FR, RL, RR. */

const COLS = 12;
const ROWS = 8;
const LAYOUT_KEY = "f1dash.layout.v1";
const MIN_W = 2;
const MIN_H = 2;

// Blocks and the default layout: x, y, width, height in cells of the 12 x 8 grid.
const BLOCKS = [
  { id: "car", title: "Car condition", x: 0, y: 0, w: 4, h: 8 },
  { id: "temps", title: "Temperatures", x: 4, y: 0, w: 4, h: 5 },
  { id: "pu", title: "Power unit", x: 4, y: 5, w: 4, h: 3 },
  { id: "pit", title: "Pit stop", x: 8, y: 0, w: 4, h: 4 },
  { id: "penalties", title: "Penalties", x: 8, y: 4, w: 4, h: 4 },
];

// Color thresholds. The game does not report its temperature windows, so these are estimates: tune them here.
// Temperatures: below cold is blue, up to ok green, up to warm yellow, above that red.
const TEMP = {
  surface: { dry: { cold: 80, ok: 105, warm: 115 }, inter: { cold: 60, ok: 95, warm: 110 }, wet: { cold: 45, ok: 85, warm: 100 } },
  inner: { dry: { cold: 85, ok: 110, warm: 120 }, inter: { cold: 65, ok: 100, warm: 112 }, wet: { cold: 50, ok: 90, warm: 105 } },
  brakes: { cold: 100, ok: 900, warm: 1050 },
  engine: { cold: 80, ok: 125, warm: 135 },
};
// Wear and damage, %: up to good green, up to warning yellow, up to serious orange, above that red.
const WEAR = { good: 20, warning: 40, serious: 60 };

const COLOR = {
  good: "var(--good)", warning: "var(--warning)", serious: "var(--serious)", critical: "var(--critical)",
  cold: "var(--cold)", none: "var(--body)",
};
const WHEELS = ["FL", "FR", "RL", "RR"];

// ---------- levels ----------

function wearLevel(value) {
  if (value == null) return "none";
  if (value <= WEAR.good) return "good";
  if (value <= WEAR.warning) return "warning";
  if (value <= WEAR.serious) return "serious";
  return "critical";
}

function tempLevel(value, range) {
  if (value == null || !range) return "none";
  if (value < range.cold) return "cold";
  if (value <= range.ok) return "good";
  if (value <= range.warm) return "warning";
  return "critical";
}

function tyreKind(visual) {
  return visual === "I" ? "inter" : visual === "W" ? "wet" : "dry";
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const pct = (v) => (v == null ? "–" : `${Math.round(v)}%`);
const empty = (text = "No data") => `<div class="empty">${text}</div>`;
const tyreBadge = (v) => `<span class="tyre ${esc(v)}">${esc(v)}</span>`;
const plural = (n, word) => `${n} ${word}${Math.abs(n) === 1 ? "" : "s"}`;

function formatMs(ms) {
  if (!ms) return "–";
  const s = ms / 1000;
  return s >= 60 ? `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}` : `${s.toFixed(1)} s`;
}

// ---------- car diagram (top view, nose up) ----------

// Parts colored by damage. Coordinates are in the car's own viewBox: 200 wide, 400 tall.
function carShape(fill) {
  const f = (key) => fill[key] || COLOR.none;
  return `
  <g>
    <path class="part" fill="${f("floor")}" d="M58 150 L142 150 L150 300 L132 332 L68 332 L50 300 Z"/>
    <path class="part" fill="${f("sidepodL")}" d="M58 168 Q56 160 64 158 L80 158 L80 262 L66 262 Q56 230 58 168 Z"/>
    <path class="part" fill="${f("sidepodR")}" d="M142 168 Q144 160 136 158 L120 158 L120 262 L134 262 Q144 230 142 168 Z"/>
    <path class="body-part" d="M92 36 L108 36 L114 140 L122 160 L122 250 L114 300 L86 300 L78 250 L78 160 L86 140 Z"/>
    <path class="part" fill="${f("engine")}" d="M86 214 L114 214 L114 290 L104 302 L96 302 L86 290 Z"/>
    <ellipse class="body-part" cx="100" cy="176" rx="11" ry="20"/>
    <path class="part" fill="${f("gearbox")}" d="M92 302 L108 302 L107 332 L93 332 Z"/>
    <path class="part" fill="${f("diffuser")}" d="M66 334 L134 334 L128 352 L72 352 Z"/>
    <path class="part" fill="${f("wingFL")}" d="M98 22 L98 40 L44 40 Q32 40 32 30 L34 22 Z"/>
    <path class="part" fill="${f("wingFR")}" d="M102 22 L102 40 L156 40 Q168 40 168 30 L166 22 Z"/>
    <path class="part" fill="${f("wingR")}" d="M58 358 L142 358 L142 380 L58 380 Z"/>
    <line x1="88" y1="92" x2="58" y2="82" stroke="var(--body-line)" stroke-width="3"/>
    <line x1="112" y1="92" x2="142" y2="82" stroke="var(--body-line)" stroke-width="3"/>
    <line x1="80" y1="290" x2="56" y2="312" stroke="var(--body-line)" stroke-width="3"/>
    <line x1="120" y1="290" x2="144" y2="312" stroke="var(--body-line)" stroke-width="3"/>
    <rect class="part" fill="${f("FL")}" x="32" y="56" width="26" height="52" rx="7"/>
    <rect class="part" fill="${f("FR")}" x="142" y="56" width="26" height="52" rx="7"/>
    <rect class="part" fill="${f("RL")}" x="26" y="282" width="30" height="58" rx="7"/>
    <rect class="part" fill="${f("RR")}" x="144" y="282" width="30" height="58" rx="7"/>
  </g>`;
}

// ---------- blocks ----------

function renderCar(s) {
  const d = s.damage;
  if (!d) return empty();
  const fill = {
    floor: COLOR[wearLevel(d.floor)], sidepodL: COLOR[wearLevel(d.sidepod)], sidepodR: COLOR[wearLevel(d.sidepod)],
    engine: COLOR[wearLevel(d.engine)], gearbox: COLOR[wearLevel(d.gearbox)], diffuser: COLOR[wearLevel(d.diffuser)],
    wingFL: COLOR[wearLevel(d.frontLeftWing)], wingFR: COLOR[wearLevel(d.frontRightWing)], wingR: COLOR[wearLevel(d.rearWing)],
  };
  WHEELS.forEach((w, i) => (fill[w] = COLOR[wearLevel(d.tyreWear[i])]));

  // Wheel labels: wear in large type, then tyre damage, blisters and brake damage when they are not zero.
  const label = (i, x, y, anchor) => {
    const extra = [];
    // In the game tyre damage usually equals wear, so it is shown only when it differs (a puncture and so on).
    if (Math.abs(d.tyreDamage[i] - d.tyreWear[i]) > 1) extra.push(`damage ${d.tyreDamage[i]}%`);
    if (d.blisters[i]) extra.push(`blisters ${d.blisters[i]}%`);
    if (d.brakeDamage[i]) extra.push(`brake ${d.brakeDamage[i]}%`);
    return `<text x="${x}" y="${y}" text-anchor="${anchor}" font-size="22" font-weight="700">${pct(d.tyreWear[i])}</text>
      <text x="${x}" y="${y + 15}" text-anchor="${anchor}" font-size="11" class="muted">wear ${WHEELS[i]}</text>
      ${extra.map((t, k) => `<text x="${x}" y="${y + 30 + k * 13}" text-anchor="${anchor}" font-size="11" class="soft">${t}</text>`).join("")}`;
  };
  const svg = `<svg class="fit" viewBox="-80 0 360 400" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Damage diagram">
    ${carShape(fill)}
    ${label(0, 24, 74, "end")}${label(1, 176, 74, "start")}${label(2, 20, 302, "end")}${label(3, 180, 302, "start")}
  </svg>`;

  const rows = [
    ["Front wing L", d.frontLeftWing], ["Front wing R", d.frontRightWing], ["Rear wing", d.rearWing],
    ["Floor", d.floor], ["Diffuser", d.diffuser], ["Sidepods", d.sidepod], ["Gearbox", d.gearbox], ["Engine", d.engine],
  ];
  const faults = [["DRS", d.drsFault], ["ERS", d.ersFault], ["Engine blown", d.engineBlown], ["Engine seized", d.engineSeized]];
  const faultHtml = faults.filter(([, bad], i) => bad || i < 2)
    .map(([name, bad]) => `<span class="badge ${bad ? "bad" : "ok"}">${name}${bad && name.length <= 3 ? " fault" : ""}</span>`).join("");
  return `<div class="car-layout">
    <div class="diagram">${svg}</div>
    <div class="parts">${rows.map(([name, v]) => partRow(name, v)).join("")}<div class="faults">${faultHtml}</div></div>
  </div>`;
}

function partRow(name, value) {
  const level = wearLevel(value);
  return `<div class="part-row"><span>${name}</span><b class="c-${level}">${pct(value)}</b>
    <div class="bar c-${level}"><i style="width:${Math.max(0, Math.min(100, value || 0))}%"></i></div></div>`;
}

function renderTemps(s) {
  const t = s.telemetry;
  if (!t) return empty();
  const kind = tyreKind(s.status?.visual);
  const lv = {
    surface: (i) => tempLevel(t.surface[i], TEMP.surface[kind]),
    inner: (i) => tempLevel(t.inner[i], TEMP.inner[kind]),
    brakes: (i) => tempLevel(t.brakes[i], TEMP.brakes),
  };
  const icon = (type, x, y, level) => {
    const c = COLOR[level];
    if (type === "surface") return `<circle cx="${x}" cy="${y}" r="6" fill="${c}"/>`;
    if (type === "inner") return `<circle cx="${x}" cy="${y}" r="5" fill="none" stroke="${c}" stroke-width="2.4"/>`;
    return `<circle cx="${x}" cy="${y}" r="6" fill="none" stroke="${c}" stroke-width="2.4"/><circle cx="${x}" cy="${y}" r="2" fill="${c}"/>`;
  };
  // Values hug the icons as in the game: text left of the icon on the left side, right of it on the right side.
  const corner = (i, y, left) => {
    const rows = [["surface", t.surface[i]], ["inner", t.inner[i]], ["brakes", t.brakes[i]]];
    const xIcon = left ? 112 : 268;
    const xText = left ? 98 : 282;
    const anchor = left ? "end" : "start";
    const lines = rows.map(([type, v], k) =>
      `${icon(type, xIcon, y + k * 30 - 6, lv[type](i))}<text x="${xText}" y="${y + k * 30}" text-anchor="${anchor}" font-size="20" font-weight="600">${v}°C</text>`);
    lines.push(`<text x="${xText}" y="${y + 86}" text-anchor="${anchor}" font-size="13" class="muted">${t.pressure[i].toFixed(1)} psi</text>`);
    return lines.join("");
  };
  const fill = {};
  WHEELS.forEach((w, i) => (fill[w] = COLOR[lv.surface(i)]));
  fill.engine = COLOR[tempLevel(t.engine, TEMP.engine)];
  const sess = s.session;
  setNote("temps", sess ? `${sess.weather} · track ${sess.trackTemp}° · air ${sess.airTemp}°` : "");
  return `<svg class="fit" viewBox="0 0 380 346" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Temperatures">
    <g transform="translate(120 6) scale(0.7)">${carShape(fill)}</g>
    <text x="190" y="298" text-anchor="middle" font-size="18" font-weight="700">${t.engine}°C</text>
    <text x="190" y="312" text-anchor="middle" font-size="11" class="muted">engine</text>
    ${corner(0, 40, true)}${corner(1, 40, false)}${corner(2, 210, true)}${corner(3, 210, false)}
    <g font-size="12" class="soft">
      <circle cx="16" cy="332" r="5" fill="var(--ink-2)"/><text x="26" y="336" class="soft">surface</text>
      <circle cx="98" cy="332" r="4.5" fill="none" stroke="var(--ink-2)" stroke-width="2"/><text x="108" y="336" class="soft">carcass</text>
      <circle cx="180" cy="332" r="5.5" fill="none" stroke="var(--ink-2)" stroke-width="2"/><circle cx="180" cy="332" r="1.8" fill="var(--ink-2)"/><text x="190" y="336" class="soft">brake</text>
    </g>
  </svg>`;
}

function renderPu(s) {
  const d = s.damage;
  if (!d) return empty();
  const rows = [
    ["Combustion (ICE)", d.pu.ice], ["MGU-H", d.pu.mguh], ["MGU-K", d.pu.mguk], ["Battery (ES)", d.pu.es],
    ["Electronics (CE)", d.pu.ce], ["Turbo (TC)", d.pu.tc], ["Gearbox", d.gearbox], ["Engine damage", d.engine],
  ];
  return `<div class="pu">${rows.map(([n, v]) => partRow(n, v)).join("")}</div>`;
}

function renderPit(s) {
  const lap = s.lap, st = s.status, win = s.pitWindow, sess = s.session;
  if (!lap && !st) return empty();
  const parts = [];

  // The pit window is the game's strategy from the MFD; UDP carries no other information about the plan.
  if (win && win.idealLap) {
    const now = lap?.lap ?? 0;
    const toIdeal = win.idealLap - now;
    let sub;
    if (now >= win.idealLap && now <= (win.latestLap || win.idealLap)) sub = `<b class="c-warning">window open</b>`;
    else if (win.latestLap && now > win.latestLap) sub = `<b class="c-critical">window missed</b>`;
    else sub = `in ${plural(toIdeal, "lap")}`;
    const range = win.latestLap && win.latestLap !== win.idealLap ? `${win.idealLap}–${win.latestLap}` : `${win.idealLap}`;
    parts.push(`<div class="hero"><span class="big">Lap ${range}</span><span class="sub">${sub}${win.rejoinPosition ? ` · rejoin P${win.rejoinPosition}` : ""}</span></div>`);
  } else {
    const why = sess && !/Race/.test(sess.type) ? "not a race" : lap?.pitStops ? "no more planned stops" : "no strategy from the game";
    parts.push(`<div class="hero"><span class="sub">Pit window: ${why}</span></div>`);
  }

  if (lap && (lap.pitStatus || lap.pitLaneActive)) {
    parts.push(`<div class="alert info">${esc(lap.pitStatus || "Pit lane")} · in pit lane ${formatMs(lap.pitLaneMs)}${lap.pitStopMs ? ` · stationary ${formatMs(lap.pitStopMs)}` : ""}</div>`);
  }
  if (lap && lap.servePenaltyAtStop) parts.push(`<div class="alert">A penalty will be served at this stop</div>`);

  const avgWear = s.damage ? s.damage.tyreWear.reduce((a, b) => a + b, 0) / 4 : null;
  const fuel = st ? (st.fuelLaps >= 0 ? `+${st.fuelLaps.toFixed(1)}` : st.fuelLaps.toFixed(1)) : "–";
  const kv = [
    ["Tyres", st ? `${tyreBadge(st.visual)} ${esc(st.actual)} · ${st.age} L` : "–"],
    ["Average wear", pct(avgWear)],
    ["Fuel, laps", st ? `<span class="c-${st.fuelLaps < 0 ? "critical" : "good"}">${fuel}</span> · ${st.fuelKg.toFixed(1)} kg` : "–"],
    ["Pit stops", lap ? lap.pitStops : "–"],
  ];
  parts.push(`<div class="kv">${kv.map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join("")}</div>`);

  // Spare sets grouped by compound: how many are new and the wear of the used ones.
  const sets = s.tyreSets?.sets?.filter((x) => x.available && !x.fitted) ?? [];
  if (s.tyreSets) {
    const groups = new Map();
    for (const x of sets) {
      if (!groups.has(x.visual)) groups.set(x.visual, { fresh: 0, used: [], sample: x });
      const g = groups.get(x.visual);
      if (x.wear) g.used.push(x.wear); else g.fresh += 1;
    }
    const chips = [...groups.entries()].map(([visual, g]) => {
      const text = [g.fresh ? `${g.fresh} new` : "", ...g.used.map((w) => `${w}%`)].filter(Boolean).join(" · ");
      const x = g.sample;
      return `<span class="set" title="${esc(x.actual)}, new set life ${x.lifeSpan} laps, ${x.deltaMs > 0 ? "+" : ""}${(x.deltaMs / 1000).toFixed(1)} s vs fitted">${tyreBadge(visual)}${text}</span>`;
    });
    parts.push(`<div class="section-title">Spare sets</div>`);
    parts.push(`<div class="sets">${chips.length ? chips.join("") : `<span class="c-none">none</span>`}</div>`);
  }

  const fc = sess?.forecast?.filter((f) => f.minutes > 0).slice(0, 4) ?? [];
  if (fc.length) {
    parts.push(`<div class="section-title">Forecast</div><div class="forecast">${fc.map((f) =>
      `<span>+${f.minutes} min · ${esc(f.weather)} · rain ${f.rain}%</span>`).join("")}</div>`);
  }
  return `<div class="stack">${parts.join("")}</div>`;
}

function renderPenalties(s) {
  const lap = s.lap;
  if (!lap) return empty();
  const tile = (value, label, on, mild) =>
    `<div class="tile ${on ? (mild ? "mild" : "on") : "off"}"><b>${value}</b><span>${label}</span></div>`;
  const parts = [`<div class="tiles">
    ${tile(lap.penaltySeconds ? `+${lap.penaltySeconds} s` : "0 s", "time added", lap.penaltySeconds > 0)}
    ${tile(lap.driveThrough, "drive-through", lap.driveThrough > 0)}
    ${tile(lap.stopGo, "stop & go", lap.stopGo > 0)}
    ${tile(lap.warnings, "warnings", lap.warnings > 0, true)}
    ${tile(lap.cornerCutting, "corner cuts", lap.cornerCutting > 0, true)}
  </div>`];
  const unserved = lap.driveThrough + lap.stopGo;
  if (unserved) {
    parts.push(`<div class="alert">Unserved: ${[lap.driveThrough ? `drive-through ×${lap.driveThrough}` : "", lap.stopGo ? `stop & go ×${lap.stopGo}` : ""].filter(Boolean).join(", ")}. Box to serve</div>`);
  }
  const events = s.events || [];
  parts.push(events.length
    ? `<ul class="events">${events.map((e) => `<li class="${e.kind}"><span class="lap">Lap ${e.lap}</span>
        <span class="what"><b>${esc(e.type)}</b>${e.reason ? ` <span>· ${esc(e.reason)}</span>` : ""}</span></li>`).join("")}</ul>`
    : `<div class="c-none">No penalties this session</div>`);
  return `<div class="stack">${parts.join("")}</div>`;
}

function setNote(id, text) {
  const note = elements[id].querySelector(".note");
  if (note.textContent !== text) note.textContent = text;
}

const RENDER = { car: renderCar, temps: renderTemps, pu: renderPu, pit: renderPit, penalties: renderPenalties };

// ---------- top bar ----------

const SAFETY = { 1: "SAFETY CAR", 2: "VIRTUAL SC", 3: "FORMATION" };
const MODES = { live: "LIVE", demo: "DEMO", replay: "REPLAY" };

function renderTop(s) {
  const mode = document.getElementById("mode");
  mode.textContent = MODES[s.mode] || s.mode;
  mode.className = `mode ${s.mode}`;
  const sess = s.session;
  document.getElementById("session").textContent = sess
    ? `${sess.track}${sess.type ? ` · ${sess.type}` : ""}${sess.spectating ? " · spectating" : ""}`
    : "Waiting for the game";
  const lapEl = document.getElementById("lap");
  lapEl.hidden = !s.lap;
  if (s.lap) lapEl.innerHTML = `Lap <b>${s.lap.lap}${sess?.totalLaps ? `/${sess.totalLaps}` : ""}</b>`;
  const posEl = document.getElementById("position");
  posEl.hidden = !s.lap;
  if (s.lap) posEl.innerHTML = `Position <b>P${s.lap.position}</b>`;
  const sc = document.getElementById("safety");
  sc.hidden = !(sess && SAFETY[sess.safetyCar]);
  if (!sc.hidden) sc.textContent = SAFETY[sess.safetyCar];
  const fmt = document.getElementById("format");
  fmt.hidden = !s.wrongFormat;
  if (s.wrongFormat) fmt.textContent = `The game sends format ${s.wrongFormat}, 2025 is needed: Settings > Telemetry > UDP Format`;
  setLink(s.packetAge == null ? "none" : s.packetAge > 2 ? "stale" : "ok",
    s.packetAge == null ? "No data from the game" : s.packetAge > 2 ? `Game silent for ${Math.round(s.packetAge)} s` : "Receiving data");
}

function setLink(cls, text) {
  const link = document.getElementById("link");
  link.className = `link ${cls}`;
  link.querySelector("b").textContent = text;
}

// ---------- layout ----------

const grid = document.getElementById("grid");
const layout = loadLayout();
const elements = {};

function loadLayout() {
  const base = Object.fromEntries(BLOCKS.map((b) => [b.id, { x: b.x, y: b.y, w: b.w, h: b.h }]));
  try {
    const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "null");
    if (saved && BLOCKS.every((b) => valid(saved[b.id])) && !anyOverlap(saved)) return saved;
  } catch (e) { /* storage unavailable: use the default layout */ }
  return base;
}

function saveLayout() {
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch (e) { /* not saved, nothing breaks */ }
}

function valid(r) {
  return r && [r.x, r.y, r.w, r.h].every(Number.isInteger) && r.x >= 0 && r.y >= 0 && r.w >= MIN_W && r.h >= MIN_H
    && r.x + r.w <= COLS && r.y + r.h <= ROWS;
}

const overlap = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

function anyOverlap(l) {
  const ids = Object.keys(l);
  return ids.some((a, i) => ids.slice(i + 1).some((b) => overlap(l[a], l[b])));
}

function place(el, r) {
  el.style.gridColumn = `${r.x + 1} / span ${r.w}`;
  el.style.gridRow = `${r.y + 1} / span ${r.h}`;
}

function buildBlocks() {
  for (const b of BLOCKS) {
    const el = document.createElement("section");
    el.className = "block";
    el.dataset.id = b.id;
    el.innerHTML = `<div class="block-head"><span class="grip">⋮⋮</span>${b.title}<span class="note"></span></div>
      <div class="block-body">${empty("Waiting for data")}</div><div class="resize" title="Drag to resize"></div>`;
    place(el, layout[b.id]);
    grid.appendChild(el);
    elements[b.id] = el;
    el.querySelector(".block-head").addEventListener("pointerdown", (e) => startGesture(e, b.id, "move"));
    el.querySelector(".resize").addEventListener("pointerdown", (e) => startGesture(e, b.id, "resize"));
  }
}

function cellAt(e) {
  const rect = grid.getBoundingClientRect();
  const style = getComputedStyle(grid);
  const pad = parseFloat(style.paddingLeft);
  const w = (rect.width - 2 * pad) / COLS;
  const h = (rect.height - 2 * pad) / ROWS;
  return { col: Math.floor((e.clientX - rect.left - pad) / w), row: Math.floor((e.clientY - rect.top - pad) / h) };
}

/* Move by the header, resize by the corner. Blocks snap to the grid and never overlap.
   A block dropped on another one swaps places with it; if the other one does not fit into the freed space,
   the two blocks swap sizes as well. The ghost frame shows where the block will land, red means not allowed. */
function startGesture(e, id, kind) {
  if (e.button !== 0) return;
  e.preventDefault();
  const start = cellAt(e);
  const origin = { ...layout[id] };
  const el = elements[id];
  const ghost = document.createElement("div");
  ghost.className = "ghost";
  place(ghost, origin);
  grid.appendChild(ghost);
  el.classList.add("dragging");
  let plan = null;

  const move = (ev) => {
    const c = cellAt(ev);
    let r;
    if (kind === "move") {
      r = { ...origin, x: origin.x + c.col - start.col, y: origin.y + c.row - start.row };
      r.x = Math.max(0, Math.min(COLS - r.w, r.x));
      r.y = Math.max(0, Math.min(ROWS - r.h, r.y));
    } else {
      r = { ...origin, w: Math.max(MIN_W, origin.w + c.col - start.col), h: Math.max(MIN_H, origin.h + c.row - start.row) };
      r.w = Math.min(COLS - r.x, r.w);
      r.h = Math.min(ROWS - r.y, r.h);
    }
    plan = planFor(id, r, origin, kind);
    place(ghost, plan ? plan[id] : r);
    ghost.classList.toggle("bad", !plan);
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    ghost.remove();
    el.classList.remove("dragging");
    if (!plan) return;
    Object.assign(layout, plan);
    for (const key of Object.keys(plan)) place(elements[key], layout[key]);
    saveLayout();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
}

// New positions of the affected blocks, or null when the block cannot go there.
function planFor(id, r, origin, kind) {
  const hit = BLOCKS.map((b) => b.id).filter((other) => other !== id && overlap(r, layout[other]));
  if (!hit.length) return { [id]: r };
  if (kind !== "move" || hit.length !== 1) return null;
  const other = hit[0];
  const swapped = { ...layout[other], x: origin.x, y: origin.y };
  const fits = valid(swapped) && !overlap(swapped, r)
    && !BLOCKS.some((b) => b.id !== id && b.id !== other && overlap(swapped, layout[b.id]));
  if (fits) return { [id]: r, [other]: swapped };
  return { [id]: { ...layout[other] }, [other]: { ...origin } };
}

document.getElementById("reset-layout").addEventListener("click", () => {
  for (const b of BLOCKS) {
    layout[b.id] = { x: b.x, y: b.y, w: b.w, h: b.h };
    place(elements[b.id], layout[b.id]);
  }
  saveLayout();
});

// ---------- test mode ----------

async function setupTest() {
  let info;
  try { info = await (await fetch("/info")).json(); } catch (e) { return; }
  const actions = Object.entries(info.actions || {});
  if (!actions.length) return;
  const box = document.getElementById("test");
  const menu = document.getElementById("test-menu");
  const toggle = document.getElementById("test-toggle");
  box.hidden = false;
  menu.innerHTML = `<p>Test race: events show up on the dashboard right away. Without clicks the scenario runs on its own.</p>` +
    actions.map(([key, label]) => `<button type="button" data-action="${key}">${esc(label)}</button>`).join("");
  toggle.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    toggle.setAttribute("aria-expanded", String(!menu.hidden));
  });
  menu.addEventListener("click", (e) => {
    const action = e.target.closest("button")?.dataset.action;
    if (action) fetch(`/demo/${action}`, { method: "POST" });
  });
  document.addEventListener("pointerdown", (e) => {
    if (!box.contains(e.target)) { menu.hidden = true; toggle.setAttribute("aria-expanded", "false"); }
  });
}

// ---------- data ----------

let latest = null;
let scheduled = false;

function render() {
  scheduled = false;
  const s = latest;
  renderTop(s);
  for (const id of Object.keys(RENDER)) {
    let html;
    try { html = RENDER[id](s); } catch (err) { console.error(id, err); html = empty("Render error, see the console"); }
    const body = elements[id].querySelector(".block-body");
    if (body._html !== html) { body.innerHTML = html; body._html = html; }
  }
}

function accept(state) {
  latest = state;
  if (!scheduled) { scheduled = true; requestAnimationFrame(render); }
}

// ?poll: poll /state instead of the event stream, for headless screenshots and old browsers.
async function poll() {
  try { accept(await (await fetch("/state")).json()); } catch (e) { setLink("none", "No connection to the bridge"); }
  setTimeout(poll, 200);
}

function connect() {
  if (new URLSearchParams(location.search).has("poll")) { poll(); return; }
  const source = new EventSource("/events");
  source.onmessage = (e) => accept(JSON.parse(e.data));
  source.onerror = () => setLink("none", "No connection to the bridge");
}

buildBlocks();
setupTest();
connect();
