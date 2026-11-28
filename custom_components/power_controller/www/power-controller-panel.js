/*
 * Power Controller – super-user panel (sidebar, admin only).
 *
 * Pages, one per functional module:
 *   Overview      – NOW & HISTORY (KPIs, live power per device, phases V/A, ±3 days) and PLAN (ahead)
 *   Price         – price model, all-in price 72 h, grid tariff, Norgespris remaining, Norgespris vs. spot
 *   Plan          – 72 h plan (incl. forecast for uncommitted devices), occupancy 7 days, plan vs. actual
 *   Distribution  – live flow: grid → limiters → distribution → devices, incl. lending
 *   Devices       – the object cards
 *   Priorities    – drag/arrow ordering
 *
 * Data: websocket "power_controller/overview" every 10 s; recorder history/statistics every 60 s.
 * Styling: Home Assistant theme variables only. No external dependencies.
 */
const PCP_VERSION = "2.12.0";
const PCP_IMG = "/power_controller_frontend/img";
const PCP_COLORS = ["#f97316", "#8b5cf6", "#06b6d4", "#22c55e", "#ec4899", "#eab308", "#3b82f6", "#14b8a6", "#ef4444", "#a855f7"];
const PCP_STATUS = {
  running: ["Running", "ok"], lent: ["Idle · lent", "info"], waiting: ["Waiting", "mut"], blocked: ["Blocked", "warn"],
  shedding: ["Shedding", "bad"], priority_now: ["Priority now", "warn"], manual: ["Not controlled", "mut"],
};
const PCP_OCC = {
  home_awake: ["Home & awake", "var(--success-color, #43a047)"], home_office: ["Home office", "#8b5cf6"],
  away_work: ["Away (work)", "var(--divider-color, #ccc)"], sleep: ["Sleeping", "#3b4a8a"], out_of_town: ["Out of town", "#888"],
};
const PCP_TYPE = { climate_heating: "Heating", climate_hotwater: "Hot water", ev_charger: "EV charger", switch: "Switch" };
const PCP_TABS = [["ov", "Overview"], ["pr", "Price"], ["pl", "Plan"], ["di", "Distribution"], ["dv", "Devices"], ["po", "Priorities"], ["se", "Settings"]];
/* ---- help texts for the Settings page (key = entity key) ---- */
const PCP_HELP = {
  price_model: "Which energy price you pay. Decides the energy part of the hourly/15-minute price the planner optimises against. The grid tariff is added on top in all models.",
  norgespris: "Fixed Norgespris energy price, NOK/kWh excl. VAT (0.40 in 2026). Used up to the monthly volume.",
  norgespris_volume: "Monthly volume (kWh) at Norgespris. Consumption above it is billed at spot price.",
  supplier_markup: "Your electricity supplier's mark-up per kWh excl. VAT (spot agreements). Added to the energy price.",
  support_threshold: "Strømstøtte: the state pays a share of the spot price above this level (NOK/kWh excl. VAT).",
  support_rate: "Strømstøtte: the share (%) of the spot price above the threshold that is covered (90 % in 2026).",
  vat: "VAT in percent. All prices you enter excl. VAT are multiplied by (1 + VAT).",
  tariff: "Capacity step (kapasitetsledd) you aim to stay inside. The limiter keeps every hour below this step; if the month's top-3 hours already exceed it, the higher step is shown.",
  shadow_model: "The price model the shadow evaluation compares your bill against. It never controls anything.",
  shadow_extra_fee: "A fixed monthly fee for the alternative agreement (e.g. a spot agreement's fee), added in the comparison.",
  grid_default: "Grid energy charge (energiledd) for all hours that are not covered by a period, e.g. nights and weekends.",
  grid_fee: "Consumption tax (forbruksavgift) + Enova fee per kWh. Added to every hour.",
  grid_period: "Grid energy charge for this period of the active price list. Hours, days and months are edited under Settings → Devices & services → Power Controller → Configure → Grid tariff.",
  workday_wake: "Workday: when you usually get up. Comfort rooms are warm from this time.",
  workday_leave: "Workday: when you usually leave for work. After this the house counts as away (or home office).",
  workday_back: "Workday: when you usually come home. Rooms are pre-heated to be warm at this time.",
  workday_sleep: "Workday: when you usually go to bed. Night temperatures apply after this (may be after midnight).",
  free_day_wake: "Day off (weekend/holiday): when you usually get up.",
  free_day_sleep: "Day off: when you usually go to bed.",
  mode: "Where the household is in the coming days – used for the long-term plan.",
  occupancy_select: "The situation right now. Set it yourself or from an automation; it stays until changed. It decides the current hour, the times above decide later hours.",
  priority_profile: "Temporarily shifts the weights of the plan priority list for groups of devices, without rearranging the list. Only affects planning, not real-time distribution.",
  arrival_time: "When you expect to be home again (with Mode Away/Arriving). Heating starts in time to be warm at arrival.",
  departure_time: "When you plan to leave (with Mode Home). Used for planning only – it never switches anything by itself.",
  baseload_24h: "Consumption that Power Controller does not control and that runs all day (fridge, network, standby), kW.",
  baseload_daytime: "Extra uncontrolled consumption while someone is home and awake (cooking, lights, TV), kW.",
  internal_limit: "Your main fuse per phase (A). The grid limiter never plans or distributes above it.",
  cmd_interval: "Minimum time between two commands to the same device (setpoint or charging current). Reductions that protect the tariff step or a phase are sent at once.",
  regulated_interval: "Minimum time between setpoint changes for heaters regulated by an external room sensor (typically heat pumps). Longer than for other devices to avoid wearing the compressor with frequent up/down changes. Urgent reductions still go out at once.",
  ev_min_step: "The EV charging current is only changed when the new value differs by at least this many amperes (start/stop always allowed).",
  setpoint_min_step: "A climate setpoint is only changed when the new value differs by at least this many degrees.",
  ev_delay_penalty: "For EVs without a deadline. Waiting is free for the first 24 hours (the cheapest hours tonight/tomorrow are simply used). Only waiting beyond 24 h costs this much per kWh and hour, so charging is not postponed for days to save a few øre. 2 øre/kWh/h: waiting 10 h beyond 24 h must save more than 20 øre/kWh.",
};
const PCP_OPT_HELP = {
  price_model: {
    "Norgespris": "Fixed energy price up to the monthly volume, spot price above it. Little price variation – the grid tariff (day/night) and the capacity charge matter most.",
    "Spot + electricity support": "Nord Pool spot price; the state covers a share of the price above the threshold (strømstøtte). Large price variation – moving load in time pays off.",
    "Spot": "Plain Nord Pool spot price without support.",
  },
  shadow_model: {
    "Off": "No comparison.",
    "Spot + electricity support": "Compare with what you would have paid on spot with strømstøtte, planned for spot prices.",
    "Spot": "Compare with plain spot price.",
  },
  mode: {
    "Home": "Living at home; you may be out during the day. Normal schedule and occupancy.",
    "Away": "Out of town. Out-of-town temperatures; the occupancy selector is ignored.",
    "Arriving": "On the way home. Heating starts so the house is warm at the arrival time.",
  },
  occupancy_select: {
    "Home and awake": "Comfort rooms are never lowered, even if the schedule says bedtime.",
    "Sleeping": "Night temperatures now; the morning follows the schedule.",
    "Away (work)": "Lower now; pre-heating for the expected return follows the schedule. After the scheduled return the house is kept warm.",
    "Home office": "Home-office rooms and the living room are warm now.",
  },
  priority_profile: {
    "Normal": "The priority list as it is.",
    "Comfort": "Heating and hot water weigh 10× more: comfort wins over the EV when capacity is short.",
    "EV Boost": "The EV weighs 10× more: the car gets its energy before heating gives way.",
    "Economy": "All weights × 0.3: more willing to accept a slightly cooler room or less charging in expensive hours.",
  },
};
const PCP_THERMAL_HELP = {
  room: "Average of the room temperature sensors of the heaters in this zone.",
  loss: "How much heat the zone loses per degree of difference between indoor and outdoor temperature. Lower = better insulated. Example: 60 W/°C at 21 °C inside and 0 °C outside = 1.26 kW.",
  cap: "How much heat it takes to raise the zone 1 °C (air, furniture, walls). Higher = the room heats up and cools down more slowly, and pre-heating stores more.",
  tau: "Heat capacity ÷ heat loss: how quickly the room drifts towards the outdoor temperature without heating. A long time constant means pre-heating lasts longer.",
  hold: "Heat needed to keep 21 °C at the given outdoor temperature (before the heating coefficient – a heat pump uses less electricity).",
  cool: "How much the room cools in 2 hours without heating, starting at 21 °C with 0 °C outside.",
  err: "How far the model's prediction was from the measured temperature, smoothed. Falling values mean the model is learning; below ~0.1 °C is good.",
  samples: "Number of 15-minute steps the model has learned from. Steps are skipped when the window is open or nothing changes (no heating and little indoor–outdoor difference). Learning: < 30 steps, settling: < 200 or error > 0.3 °C, then stable.",
};
const PCP_HW_HELP = {
  loss: "Heat the tank loses per °C it is warmer than the room (W/°C). Learned from quiet periods (element off, no tapping). Lower = better insulated tank.",
  lossday: "Energy lost in 24 hours if the tank is kept at 65 °C (room 20 °C) – paid whether hot water is used or not.",
  cool: "How fast the tank cools at 65 °C without heating or tapping.",
  heat: "How fast the tank heats with the element on (°C per hour). The planner needs this to know how early to start.",
  use: "Hot water used per day (kWh), learned separately for workdays and days off. The bars show when during the day it is used; the planner heats before those hours.",
  samples: "15-minute steps evaluated. Quiet steps (no heating, slow cooling) teach the standby loss; the rest teach the use profile. Learning: < 3 days, settling: < 10 days, then stable.",
};
const PCP_MIN_VALID_V = 100;     // voltages below this are treated as missing
const PCP_FS_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M5 5h5v2H7v3H5V5m9 0h5v5h-2V7h-3V5m3 9h2v5h-5v-2h3v-3m-7 3v2H5v-5h2v3h3z"/></svg>';
const pE = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pN = (v) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v));
const pF = (v, d = 2, fb = "–") => (pN(v) === null ? fb : Number(v).toFixed(d));
const pHH = (d) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
/* live power text for a device: controlled = live / granted, not controlled = measured only */
const pLive = (x) => (x.committed ? `${pF(x.live_kw)} / ${pF(x.allocated_kw)} kW`
  : (x.has_power_sensor === false ? "no power sensor" : `${pF(x.live_kw)} kW measured`));
const pNok = (v) => (pN(v) === null ? "–" : Math.round(v).toLocaleString("nb-NO"));
const pDay = (d) => d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
const pMs = (v) => (typeof v === "number" ? (v > 1e12 ? v : v * 1000) : Date.parse(v));
const niceMax = (m) => { const s = m <= 1 ? 0.25 : m <= 3 ? 0.5 : m <= 10 ? 1 : m <= 30 ? 5 : 10; return Math.max(s, Math.ceil(m / s) * s); };

/* ---------------------------------------------------------------- chart helpers (SVG) */
/* zoom: o.view = {a, b (visible fraction of the x range), y (vertical magnification)} */
function pcZoomBar(o) {
  const v = o.view; if (!v) return { ...o, off: 0 };
  const N = Math.max(1, o.n), i0 = Math.max(0, Math.min(N - 2, Math.floor(v.a * N))), i1 = Math.max(i0 + 2, Math.min(N, Math.ceil(v.b * N)));
  const sl = (arr) => (arr || []).slice(i0, i1);
  return { ...o, n: i1 - i0, off: i0, times: sl(o.times), bars: (o.bars || []).map((b) => ({ ...b, values: sl(b.values) })),
    lines: (o.lines || []).map((l) => ({ ...l, values: sl(l.values) })), cap: o.cap ? { ...o.cap, values: sl(o.cap.values) } : undefined,
    nowIdx: o.nowIdx === undefined || o.nowIdx === null ? o.nowIdx : o.nowIdx - i0 };
}

function pcBarChart(svg, width, o) {
  o = pcZoomBar(o);
  const yz = Math.max(1, o.view?.y || 1);
  // o: {n, times[], bars:[{id,name,color,values,opacity}], lines:[{name,color,values,axis,dash,step,width}],
  //     cap:{values,label}, nowIdx, focusId, height, yLabel, yrLabel, dimBefore}
  const W = Math.max(280, width), Ht = o.height || (W < 520 ? 230 : 280), L = 40, R = o.lines?.some((l) => l.axis === "r") ? 46 : 12, T = 14, B = 30;
  const iw = W - L - R, ih = Ht - T - B, n = Math.max(1, o.n), bw = iw / n;
  const step = n <= 30 ? (W < 560 ? 3 : 1) : n <= 60 ? (W < 560 ? 6 : 3) : (W < 560 ? 12 : 6);
  let kmax = 0.5;
  for (let t = 0; t < n; t++) {
    let acc = 0; for (const b of o.bars) acc += Math.max(0, pN(b.values[t]) ?? 0);
    kmax = Math.max(kmax, acc, o.cap ? pN(o.cap.values[t]) ?? 0 : 0);
    for (const l of o.lines || []) if (l.axis !== "r") kmax = Math.max(kmax, pN(l.values[t]) ?? 0);
  }
  kmax = niceMax(kmax * 1.05 / yz);
  const rv = (o.lines || []).filter((l) => l.axis === "r").flatMap((l) => l.values.map(pN).filter((x) => x !== null));
  const pmax = rv.length ? niceMax(Math.max(...rv) * 1.1 / yz) : 1;
  const cid = `clip-${svg.id || "c"}`;
  let c = "";   // clipped plot content
  const yk = (v) => T + ih - ih * v / kmax, yp = (v) => T + ih - ih * v / pmax;
  let s = "";
  for (let g = 0; g <= 4; g++) {
    const y = T + ih - ih * g / 4;
    s += `<line x1="${L}" x2="${L + iw}" y1="${y}" y2="${y}" class="gl"/><text x="${L - 6}" y="${y + 4}" text-anchor="end" class="tx">${(kmax * g / 4).toFixed(kmax < 4 ? 1 : 0)}</text>`;
    if (R > 20) s += `<text x="${L + iw + 6}" y="${y + 4}" class="tx">${(pmax * g / 4).toFixed(2)}</text>`;
  }
  if (o.yLabel) s += `<text x="${L}" y="${T - 4}" class="tx">${o.yLabel}</text>`;
  if (o.yrLabel && R > 20) s += `<text x="${L + iw}" y="${T - 4}" text-anchor="end" class="tx">${o.yrLabel}</text>`;
  if (o.nowIdx !== undefined && o.nowIdx !== null && o.nowIdx >= 0) {
    s += `<rect x="${L}" y="${T}" width="${bw * Math.min(n, o.nowIdx)}" height="${ih}" class="past"/>`;
  }
  for (let t = 0; t < n; t++) {
    const ts = o.times[t], x = L + t * bw;
    const whole = ts && ts.getMinutes() === 0;
    if (whole && ts.getHours() === 0) s += `<line x1="${x}" x2="${x}" y1="${T}" y2="${T + ih}" class="dl"/><text x="${x + 4}" y="${T + 11}" class="tx b">${ts.toLocaleDateString(undefined, { weekday: "short" })}</text>`;
    if (whole && ts.getHours() % step === 0) s += `<text x="${x}" y="${Ht - 10}" text-anchor="middle" class="tx">${String(ts.getHours()).padStart(2, "0")}</text>`;
    let acc = 0;
    for (const b of o.bars) {
      const v = Math.max(0, pN(b.values[t]) ?? 0); if (v <= 0) continue;
      const op = (o.focusId && o.focusId !== b.id ? 0.15 : 1) * (b.opacity ?? 0.92);
      c += `<rect x="${x + bw * 0.1}" y="${yk(acc + v)}" width="${Math.max(1, bw * 0.8)}" height="${Math.max(0.5, ih * v / kmax - 0.4)}" rx="1.2" fill="${b.color}" opacity="${op}"${b.hatch ? ' class="hatch"' : ""}/>`;
      acc += v;
    }
  }
  if (o.cap) {
    let d = ""; for (let t = 0; t < n; t++) { const v = pN(o.cap.values[t]); if (v === null) continue; const y = yk(v); d += `${d ? "L" : "M"}${L + t * bw},${y}L${L + (t + 1) * bw},${y}`; }
    if (d) c += `<path d="${d}" class="cap"/><text x="${L + iw - 4}" y="${yk(pN(o.cap.values[n - 1]) ?? 0) - 5}" text-anchor="end" class="tx capt">${o.cap.label}</text>`;
  }
  for (const l of o.lines || []) {
    let d = ""; const yf = l.axis === "r" ? yp : yk;
    for (let t = 0; t < n; t++) {
      const v = pN(l.values[t]); if (v === null) { if (l.gap) d += " "; continue; }
      const x = L + t * bw, y = yf(v);
      d += l.step ? `${d && !d.endsWith(" ") ? "L" : "M"}${x},${y}L${x + bw},${y}` : `${d && !d.endsWith(" ") ? "L" : "M"}${x + bw / 2},${y}`;
    }
    if (d) c += `<path d="${d.replace(/ M/g, "M")}" fill="none" stroke="${l.color}" stroke-width="${l.width || 1.7}"${l.dash ? ` stroke-dasharray="${l.dash}"` : ""}/>`;
  }
  s += `<defs><clipPath id="${cid}"><rect x="${L}" y="${T}" width="${iw}" height="${ih}"/></clipPath></defs><g clip-path="url(#${cid})">${c}</g>`;
  if (o.nowIdx !== undefined && o.nowIdx !== null && o.nowIdx >= 0 && o.nowIdx <= n) {
    const x = L + bw * o.nowIdx;
    s += `<line x1="${x}" x2="${x}" y1="${T}" y2="${T + ih}" class="nowl"/><text x="${x + 3}" y="${T + ih - 4}" class="tx nowt">now</text>`;
  }
  s += `<line class="hv" x1="0" x2="0" y1="${T}" y2="${T + ih}" style="opacity:0"/>`;
  svg.setAttribute("viewBox", `0 0 ${W} ${Ht}`); svg.setAttribute("height", Ht); svg.innerHTML = s;
  return { L, bw, n, W, T, ih, iw, off: o.off || 0, zoomed: !!(o.view && (o.view.a > 0 || o.view.b < 1 || (o.view.y || 1) > 1)) };
}

function pcTsChart(svg, width, o) {
  // o: {t0, t1, series:[{name,color,points:[[ms,v]],axis:'l'|'r',width}], stacked:[{name,color,values[]}] (binned), bins, yLabel, yrLabel, height}
  const W = Math.max(280, width), Ht = o.height || 220, L = 40, R = o.series?.some((x) => x.axis === "r") ? 46 : 12, T = 14, B = 26;
  const bT0 = o.t0, bStep = o.bins ? (o.t1 - o.t0) / o.bins : 0, yz = Math.max(1, o.view?.y || 1);
  if (o.view) { const sp0 = o.t1 - o.t0; o = { ...o, t0: bT0 + o.view.a * sp0, t1: bT0 + o.view.b * sp0 }; }
  const iw = W - L - R, ih = Ht - T - B, span = Math.max(1, o.t1 - o.t0);
  const xs = (ms) => L + iw * (ms - o.t0) / span;
  let lmax = 0.1, rmin = Infinity, rmax = -Infinity;
  if (o.stacked) for (let i = 0; i < o.bins; i++) { let a = 0; for (const st of o.stacked) a += Math.max(0, st.values[i] ?? 0); lmax = Math.max(lmax, a); }
  for (const se of o.series || []) for (const [, v] of se.points) { if (v === null || v === undefined || isNaN(v)) continue; if (se.axis === "r") { rmin = Math.min(rmin, v); rmax = Math.max(rmax, v); } else lmax = Math.max(lmax, v); }
  lmax = niceMax(lmax * 1.05 / yz);
  if (rmin === Infinity) { rmin = 0; rmax = 1; }
  else if (o.rWindow) { rmin = Math.min(o.rWindow[0], Math.floor(rmin - 1)); rmax = Math.max(o.rWindow[1], Math.ceil(rmax + 1)); }
  else { rmin = Math.floor(rmin - 1); rmax = Math.ceil(rmax + 1); }
  if (yz > 1) { const mid = (rmin + rmax) / 2, half = (rmax - rmin) / 2 / yz; rmin = mid - half; rmax = mid + half; }
  const cid = `clip-${svg.id || "t"}`;
  const yl = (v) => T + ih - ih * v / lmax, yr = (v) => T + ih - ih * (v - rmin) / (rmax - rmin);
  let s = "";
  for (let g = 0; g <= 4; g++) {
    const y = T + ih - ih * g / 4;
    s += `<line x1="${L}" x2="${L + iw}" y1="${y}" y2="${y}" class="gl"/><text x="${L - 6}" y="${y + 4}" text-anchor="end" class="tx">${(lmax * g / 4).toFixed(lmax < 4 ? 1 : 0)}</text>`;
    if (R > 20) s += `<text x="${L + iw + 6}" y="${y + 4}" class="tx">${(rmin + (rmax - rmin) * g / 4).toFixed(rmax - rmin < 8 ? 1 : 0)}</text>`;
  }
  if (o.yLabel) s += `<text x="${L}" y="${T - 4}" class="tx">${o.yLabel}</text>`;
  if (o.yrLabel && R > 20) s += `<text x="${L + iw}" y="${T - 4}" text-anchor="end" class="tx">${o.yrLabel}</text>`;
  const ticks = o.tickEveryMs && span > 2 * o.tickEveryMs ? o.tickEveryMs : span <= 1800e3 ? 5 * 60e3 : span <= 3 * 3600e3 ? (W < 500 ? 30 : 15) * 60e3 : span <= 12 * 3600e3 ? (W < 500 ? 3 : 1) * 3600e3 : o.tickEveryMs || (span <= 2 * 3600e3 ? (W < 500 ? 30 : 15) * 60e3 : (W < 500 ? 6 : 3) * 3600e3);
  const dayTicks = ticks >= 86400e3;
  let t0t = Math.ceil(o.t0 / ticks) * ticks;
  if (dayTicks) { const d0 = new Date(o.t0); d0.setHours(24, 0, 0, 0); t0t = d0.getTime(); }   // local midnight
  for (let t = t0t; t <= o.t1; t += ticks) s += `<text x="${xs(t)}" y="${Ht - 8}" text-anchor="middle" class="tx">${dayTicks ? new Date(t).toLocaleDateString(undefined, { weekday: "short" }) : pHH(new Date(t))}</text>`;
  let c = "";
  if (o.stacked && o.bins) {
    const base = new Array(o.bins).fill(0);
    for (const st of o.stacked) {
      let top = "", bot = "";
      for (let i = 0; i < o.bins; i++) {
        const v = Math.max(0, st.values[i] ?? 0), x = xs(bT0 + i * bStep), x2 = xs(bT0 + (i + 1) * bStep);
        top += `${i ? "L" : "M"}${x.toFixed(1)},${yl(base[i] + v).toFixed(1)}L${x2.toFixed(1)},${yl(base[i] + v).toFixed(1)}`;
        bot = `L${x2.toFixed(1)},${yl(base[i]).toFixed(1)}L${x.toFixed(1)},${yl(base[i]).toFixed(1)}` + bot;
        base[i] += v;
      }
      c += `<path d="${top}${bot}Z" fill="${st.color}" opacity=".85"/>`;
    }
  }
  for (const se of o.series || []) {
    if (!se.points.length) continue;
    const yf = se.axis === "r" ? yr : yl;
    // null values break the line (missing/invalid data) instead of drawing to 0
    let d = "", pen = false;
    se.points.forEach(([t, v]) => {
      if (v === null || v === undefined || isNaN(v)) { pen = false; return; }
      if (se.step && pen) d += `L${xs(t).toFixed(1)},${se._ly}`;
      se._ly = yf(v).toFixed(1);
      d += `${pen ? "L" : "M"}${xs(t).toFixed(1)},${se._ly}`; pen = true;
    });
    if (se.step && d && se._ly !== undefined) d += `L${xs(o.t1).toFixed(1)},${se._ly}`;
    if (!d) continue;
    c += `<path d="${d}" fill="none" stroke="${se.color}" stroke-width="${se.width || 1.3}" opacity=".9"${se.step ? ' stroke-linejoin="miter"' : ""}/>`;
  }
  s += `<defs><clipPath id="${cid}"><rect x="${L}" y="${T}" width="${iw}" height="${ih}"/></clipPath></defs><g clip-path="url(#${cid})">${c}</g>`;
  svg.setAttribute("viewBox", `0 0 ${W} ${Ht}`); svg.setAttribute("height", Ht); svg.innerHTML = s;
  return { L, W, T, ih, iw, zoomed: !!(o.view && (o.view.a > 0 || o.view.b < 1 || yz > 1)) };
}

/* value of a history series at time t (step hold) */
function pcHold(points, t) { let v = null; for (const [ts, val] of points) { if (ts > t) break; v = val; } return v; }
function pcBin(points, t0, t1, bins) {
  const out = [], step = (t1 - t0) / bins;
  for (let i = 0; i < bins; i++) {
    const a = t0 + i * step, b = a + step; let sum = 0, cnt = 0;
    for (const [ts, v] of points) if (ts >= a && ts < b) { sum += v; cnt++; }
    out.push(cnt ? sum / cnt : pcHold(points, a + step / 2));
  }
  return out;
}

class PowerControllerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "ov"; this._focus = null; this._hidden = {};
    this._ptype = "plan"; this._both = false; this._order = null; this._cards = {};
    this._data = null; this._error = null; this._hist = {}; this._histTs = 0; this._geo = {}; this._zoom = {};
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) { this._skeleton(); this._fetch(); }
    if (this._menu) this._menu.hass = hass;
    Object.values(this._cards).forEach((c) => { c.hass = hass; });
  }
  set narrow(n) { this._narrow = n; if (this._menu) this._menu.narrow = n; }
  set panel(p) { this._panel = p; }

  connectedCallback() {
    this._timer = setInterval(() => this._fetch(), 10000);
    this._ro = new ResizeObserver(() => this._redrawCharts());
    this._ro.observe(this);
  }
  disconnectedCallback() { clearInterval(this._timer); this._ro?.disconnect(); }

  async _fetch() {
    if (!this._hass) return;
    try {
      this._data = await this._hass.callWS({ type: "power_controller/overview" });
      this._error = null;
    } catch (err) { this._error = err?.message || String(err); }
    if (this._data && Date.now() - this._histTs > 60000) { this._histTs = Date.now(); this._fetchHistory(); }
    else if (this._data && this._hist.hour) {        // append the live values between history fetches
      const he = this._data.history_entities || {}, t = Date.now(), h = this._hist.hour;
      for (const x of this._data.devices) { const id = he.devices?.[x.entry_id]; if (id) (h[id] = h[id] || []).push([t, pN(x.live_kw) / this._scale(id)]); }
      if (he.grid_power) (h[he.grid_power] = h[he.grid_power] || []).push([t, pN(this._data.kpi.grid_power_kw) / this._scale(he.grid_power)]);
      this._hist.t = t;
    }
    this._update();
  }

  async _fetchHistory() {
    const d = this._data, he = d.history_entities || {}, now = Date.now();
    const iso = (ms) => new Date(ms).toISOString();
    const hist = async (ids, ms) => {
      ids = ids.filter(Boolean); if (!ids.length) return {};
      try {
        const r = await this._hass.callWS({ type: "history/history_during_period", start_time: iso(now - ms), end_time: iso(now),
          entity_ids: ids, minimal_response: true, no_attributes: true, significant_changes_only: false });
        const out = {};
        for (const [id, arr] of Object.entries(r || {})) {
          out[id] = arr.map((x) => [pMs(x.lu ?? x.last_updated ?? x.last_changed), pN(x.s ?? x.state)]).filter((p) => p[1] !== null && !isNaN(p[0]));
        }
        return out;
      } catch (e) { return {}; }
    };
    const devIds = Object.values(he.devices || {});
    const hour = await hist([he.grid_power, ...devIds], 3600e3);
    // phases: 5-minute means from the recorder statistics (smooth, no "filled band" from fast
    // voltage swings); raw history only for sensors without statistics
    const phIds = [...Object.values(he.currents || {}), ...Object.values(he.voltages || {})].filter(Boolean);
    let day = {};
    if (phIds.length) {
      try {
        const r5 = await this._hass.callWS({ type: "recorder/statistics_during_period", start_time: iso(now - 24 * 3600e3), end_time: iso(now),
          statistic_ids: phIds, period: "5minute", types: ["mean"] });
        for (const id of phIds) {
          const rows = (r5?.[id] || []).map((x) => [pMs(x.start) + 150e3, pN(x.mean)]).filter((p) => p[1] !== null);
          if (rows.length) day[id] = rows;
        }
      } catch (e) { day = {}; }
      const missing = phIds.filter((id) => !day[id]);
      if (missing.length) Object.assign(day, await hist(missing, 24 * 3600e3));
    }
    let stats = [];
    if (he.grid_power) {
      try {
        const r = await this._hass.callWS({ type: "recorder/statistics_during_period", start_time: iso(now - 72 * 3600e3), end_time: iso(now),
          statistic_ids: [he.grid_power], period: "hour", types: ["mean"] });
        stats = (r?.[he.grid_power] || []).map((x) => [pMs(x.start), pN(x.mean)]).filter((p) => p[1] !== null);
      } catch (e) { stats = []; }
    }
    this._hist = { hour, day, stats, t: now };
    this._update();
  }

  _call(domain, service, data) {
    return this._hass.callService(domain, service, data).then(() => setTimeout(() => this._fetch(), 1200))
      .catch((err) => this._say(`${domain}.${service} failed: ${err?.message || err}`));
  }
  _say(m) { const l = this.shadowRoot.getElementById("live"); if (l) l.textContent = m; }
  _color(i) { return PCP_COLORS[i % PCP_COLORS.length]; }
  _devColor(eid) { const i = (this._data?.devices || []).findIndex((x) => x.entry_id === eid); return this._color(Math.max(0, i)); }
  _scale(id) {
    const u = String(this._hass?.states?.[id]?.attributes?.unit_of_measurement || "").trim();
    return { W: 0.001, MW: 1000, mA: 0.001 }[u] || 1;
  }

  // ------------------------------------------------------------------ skeleton
  _skeleton() {
    const r = this.shadowRoot;
    const img = (n, alt) => `<details class="dia"><summary>How this module works</summary><img src="${PCP_IMG}/${n}.svg?v=${PCP_VERSION}" alt="${alt}" loading="lazy"></details>`;
    r.innerHTML = `${this._css()}
      <div class="tbar"><span id="menu"></span><div class="brand"><div class="logo"><ha-icon icon="mdi:transmission-tower"></ha-icon></div>
        <div><h1>Power Controller</h1><div class="sub" id="sub">Loading…</div></div></div><div class="pills" id="pills"></div></div>
      <div class="wrap">
        <div class="bar2"><div class="seg tabs" role="tablist" aria-label="Pages">${PCP_TABS.map(([k, l], i) =>
          `<button type="button" role="tab" data-tab="${k}" aria-selected="${i === 0}">${l}</button>`).join("")}</div>
          <span class="hint" id="err" role="alert"></span></div>

        <section id="v-ov">
          <h3 class="sec"><span>Now &amp; history</span><small>measured · updates every 10 s · charts: scroll/pinch to zoom, drag to pan, scroll on the left axis to stretch it, double-click to reset</small></h3>
          <div id="safe"></div>
          <div class="kpis" id="kpis"></div>
          <div class="row half">
            <div class="card"><div class="hd"><div><h2>Power per device · last hour</h2><div class="hint">kW, measured, stacked by plan priority (1 at the bottom). "Other" = grid − controllable devices. Live values in the legend.</div></div></div>
              <div class="chartw"><svg id="svg-live" role="img" aria-label="Power per device last hour"></svg></div><div class="legend static" id="lg-live"></div></div>
            <div class="card"><div class="hd"><div><h2>Phases · last 24 h</h2><div class="hint">Current [A] left · voltage [V] right</div></div><div class="vals" id="phv"></div></div>
              <div class="chartw"><svg id="svg-ph" role="img" aria-label="Phase currents and voltages last 24 hours"></svg></div><div class="legend static" id="lg-ph"></div></div>
          </div>
          <div class="card mb"><div class="hd"><div><h2>Consumption · 3 days back and 3 days ahead</h2><div class="hint">kWh per hour: measured (history) and planned (ahead), all-in price NOK/kWh</div></div></div>
            <div class="chartw" id="cw-hist"><svg id="svg-hist" role="img" aria-label="History and plan"></svg><div class="tip" id="tip-hist"></div></div>
            <div class="legend static" id="lg-hist"></div></div>
          <h3 class="sec"><span>Plan</span><small>ahead · recalculated every hour and on changes</small></h3>
          <div class="card mb"><div class="hd"><div><h2>Energy plan · next 24 h</h2><div class="hint">Select a device to highlight it. Full 72 h on the Plan page.</div></div><span class="hint" id="sel">All devices</span></div>
            <div class="chartw" id="cw-ov"><svg id="svg-ov" role="img" aria-label="Energy plan"></svg><div class="tip" id="tip-ov"></div></div>
            <div class="dev h"><span>Device</span><span>Status</span><span class="x">Now</span><span class="x">Next</span><span class="x">Next 24 h (kWh/h)</span><span class="x">Prio</span></div><div id="devs"></div></div>
        </section>

        <section id="v-pr" hidden>
          ${img("price", "Price module diagram")}
          <div class="kpis" id="kpis-pr"></div>
          <div class="row">
            <div class="card"><div class="hd"><div><h2>All-in price · 72 h</h2><div class="hint">NOK/kWh incl. VAT: energy after price model + grid energy charge + fees. Dashed: spot · <span id="prres"></span></div></div></div>
              <div class="chartw" id="cw-pr"><svg id="svg-pr" role="img" aria-label="Price 72 hours"></svg><div class="tip" id="tip-pr"></div></div><div class="legend static" id="lg-pr"></div></div>
            <div class="card"><div class="hd"><h2>Norgespris volume</h2><span class="hint" id="npm"></span></div><div id="np"></div></div>
          </div>
          <div class="row3">
            <div class="card"><div class="hd"><h2>Grid tariff</h2><span class="hint" id="tfn"></span></div><div id="tf"></div></div>
            <div class="card"><div class="hd"><h2>Norgespris vs. spot</h2><span class="hint" id="shm">Shadow evaluation</span></div><div id="sh"></div></div>
          </div>
        </section>

        <section id="v-pl" hidden>
          ${img("plan", "Plan module diagram")}
          <div class="card mb"><div class="hd"><div><h2>Energy plan · 72 h</h2><div class="hint">kWh per hour per device. Light bars = devices that are not controlled (forecast, never shifted). Red dashed = hour allowance (capacity step)</div></div>
            <div class="seg" id="hz"><button type="button" data-h="24" aria-pressed="false">24 h</button><button type="button" data-h="72" aria-pressed="true">72 h</button></div></div>
            <div class="legend" id="lg-pl"></div>
            <div class="chartw" id="cw-pl"><svg id="svg-pl" role="img" aria-label="72 hour energy plan"></svg><div class="tip" id="tip-pl"></div></div></div>
          <div class="row">
            <div class="card"><div class="hd"><div><h2>Occupancy · 7 days</h2><div class="hint">Schedule from the Power Controller times. The selector decides the current hour(s).</div></div></div>
              <div id="occsel"></div><div id="occ"></div>
              <div class="keys">${["home_awake", "home_office", "away_work", "sleep"].map((k) => `<span><i class="sw" style="background:${PCP_OCC[k][1]}"></i> ${PCP_OCC[k][0]}</span>`).join("")}</div></div>
            <div class="card"><div class="hd"><div><h2>Plan vs. actual · last 7 days</h2><div class="hint">kWh per hour. Bars: measured · line: what the plan expected</div></div><span class="hint" id="pva"></span></div>
              <div class="chartw" id="cw-pva"><svg id="svg-pva" role="img" aria-label="Plan versus actual"></svg><div class="tip" id="tip-pva"></div></div></div>
          </div>
        </section>

        <section id="v-di" hidden>
          ${img("distribution", "Distribution module diagram")}
          <div class="card mb"><div class="hd"><div><h2>Live power flow</h2><div class="hint">Every 10 s: grid → limiters → distribution → devices. Dashed arrows: idle reservations lent to other devices</div></div><span class="hint" id="flowt"></span></div>
            <div class="chartw" id="cw-flow"><svg id="svg-flow" role="img" aria-label="Live power flow"></svg></div></div>
          <div class="row">
            <div class="card"><div class="hd"><h2>Phases · 230 V IT</h2><span id="phchip"></span></div><div id="ph"></div>
              <div class="hint">Overload acceptance: small overloads are tolerated for a while, then the limit ramps to the rated current.</div></div>
            <div class="card"><div class="hd"><h2>Distribution decisions</h2></div><div id="dtab"></div></div>
          </div>
        </section>

        <section id="v-se" hidden>
          <div class="card mb"><div class="hd"><div><h2>Settings</h2><div class="hint">The values you change most often. Changes are saved and the plan is recalculated at once.
            Sensors, devices, zones and the full grid-tariff editor (hours, days, capacity steps, new price lists) are under Settings → Devices &amp; services.</div></div>
            <a class="btn" href="/config/integrations/integration/power_controller">Open integration settings</a></div></div>
          <div class="setg" id="setg"></div>
          <div class="hd" style="margin:18px 0 10px"><div><h2>Thermal models</h2><div class="hint">What Power Controller has learned about each zone (room). The planner uses this to pre-heat at the right time. Values improve over days to weeks.</div></div></div>
          <div class="setg tzg" id="thz"></div>
          <div class="hd" style="margin:18px 0 10px"><div><h2>Hot-water models</h2><div class="hint">Learned per water heater from the tank temperature and the element's power: standby loss and when hot water is used. The planner uses these instead of fixed defaults.</div></div></div>
          <div class="setg tzg" id="hwm"></div>
          <div class="hd" style="margin:18px 0 10px"><div><h2>Notifications</h2><div class="hint">Errors and early warnings to one notify service (set under Configure → Sensors and general settings). Quiet hours: only errors. The same message at most once per hour.</div></div>
            <button type="button" class="btn" id="ntest">Send test</button></div>
          <div class="card" id="nlog"></div>
        </section>

        <section id="v-dv" hidden><div class="hd"><div><h2>Devices</h2><div class="hint">The object cards for everyday dashboards (custom:power-controller-object-card)</div></div></div><div class="dcs" id="cards"></div></section>

        <section id="v-po" hidden><div class="row">
          <div class="card"><div class="hd"><div><h2>Priority order</h2><div class="hint" id="prh"></div></div>
            <div class="seg" id="pt"><button type="button" data-t="plan" aria-pressed="true">Planning</button><button type="button" data-t="dist" aria-pressed="false">Distribution</button></div></div>
            <ol class="pl" id="pol"></ol>
            <div class="hd" style="margin:12px 0 0"><button type="button" class="tg" role="switch" id="both" aria-checked="false">Apply same order to both<span class="k"></span></button>
              <div style="display:flex;gap:8px"><button type="button" class="btn" id="reset" disabled>Reset</button><button type="button" class="btn pri" id="apply" disabled>Apply order</button></div></div>
            <div class="toast" id="toast" aria-live="polite"></div></div>
          <div class="card"><div class="hd"><h2>Service call</h2><span class="chip c-mut" id="dirty">No changes</span></div><pre id="yaml"></pre>
            <div class="hint" style="margin-top:10px">Drag rows or use the arrows. Priority now on a device always overrides this order.</div></div>
        </div></section>
        <div class="foot">Power Controller ${PCP_VERSION}</div>
      </div><span class="sr" aria-live="polite" id="live"></span>`;
    this._menu = document.createElement("ha-menu-button");
    this._menu.hass = this._hass; this._menu.narrow = this._narrow;
    r.getElementById("menu").appendChild(this._menu);
    r.querySelectorAll("[role=tab]").forEach((b) => b.addEventListener("click", () => {
      this._tab = b.dataset.tab;
      r.querySelectorAll("[role=tab]").forEach((x) => x.setAttribute("aria-selected", x === b ? "true" : "false"));
      PCP_TABS.forEach(([k]) => { r.getElementById(`v-${k}`).hidden = k !== this._tab; });
      this._update();
    }));
    this._horizon = 72;
    r.querySelectorAll("#hz button").forEach((b) => b.addEventListener("click", () => {
      this._horizon = Number(b.dataset.h);
      r.querySelectorAll("#hz button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
      this._renderPlan(this._data);
    }));
    for (const k of ["hist", "ov", "pr", "pl", "pva"]) {
      const svg = r.getElementById(`svg-${k}`);
      svg.addEventListener("pointermove", (e) => this._tipAt(k, e));
      svg.addEventListener("pointerdown", (e) => this._tipAt(k, e));
      svg.addEventListener("pointerleave", () => { r.getElementById(`tip-${k}`).style.display = "none"; const hv = svg.querySelector(".hv"); if (hv) hv.style.opacity = 0; });
    }
    r.querySelectorAll("#pt button").forEach((b) => b.addEventListener("click", () => {
      this._ptype = b.dataset.t; this._order = null;
      r.querySelectorAll("#pt button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
      this._renderPrio();
    }));
    r.getElementById("both").addEventListener("click", (e) => { this._both = !this._both; e.currentTarget.setAttribute("aria-checked", this._both); this._renderPrio(); });
    r.getElementById("reset").addEventListener("click", () => { this._order = null; this._renderPrio(); r.getElementById("toast").textContent = ""; });
    r.getElementById("apply").addEventListener("click", () => this._applyPrio());
    this._bindPrioList();
    this._addFullscreenButtons();
    for (const k of ["live", "ph", "hist", "ov", "pr", "pl", "pva"]) this._addZoom(k);
  }

  _redrawCharts() {
    if (!this._data) return;
    if (this._tab === "ov") this._renderOverview(this._data);
    if (this._tab === "pr") this._renderPrice(this._data);
    if (this._tab === "pl") this._renderPlan(this._data);
    if (this._tab === "di") this._renderDistribution(this._data);
  }

  // ------------------------------------------------------------------ update
  _update() {
    const r = this.shadowRoot;
    if (!r.getElementById("kpis")) return;
    r.getElementById("err").textContent = this._error ? `Could not load data: ${this._error}` : "";
    const d = this._data;
    if (!d) return;
    const now = new Date(d.now);
    r.getElementById("sub").textContent = `Home energy management · ${now.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })} · ${pHH(now)}`;
    const occ = PCP_OCC[d.occupancy] || [d.occupancy, ""];
    const solved = d.plan.solved ? pHH(new Date(d.plan.solved)) : "–";
    r.getElementById("pills").innerHTML = (d.kpi.safe_mode ? `<span class="pill bad"><span class="dot" style="background:var(--error-color)"></span><b>SAFE MODE</b></span>` : "")
      + (d.kpi.step_locked ? `<span class="pill warn">Step locked <b>${pF(d.kpi.active_step_kw, 0)} kW</b></span>` : "") + `<span class="pill"><span class="dot" style="background:${d.kpi.warning ? "var(--warning-color)" : "var(--success-color)"}"></span>Mode <b>${pE(d.mode)}</b></span>
      <span class="pill">Occupancy <b>${pE(occ[0])}</b></span><span class="pill">Price model <b>${pE(d.price_model)}</b></span>
      <span class="pill">Plan <b>${solved}</b> · ${pE((d.plan.solver || "").toUpperCase())} ${pF(d.plan.solve_ms, 0)} ms</span>`;
    this._redrawCharts();
    if (this._tab === "dv") this._renderCards(d);
    if (this._tab === "po") this._renderPrio();
    if (this._tab === "se") this._renderSettings(d);
  }

  _w(id) { return this.shadowRoot.getElementById(id)?.clientWidth || 600; }

  /* chart height: taller when its card is shown full screen */
  _fsH(svgId, h) {
    const fs = this.shadowRoot.fullscreenElement;
    const el = this.shadowRoot.getElementById(svgId);
    return fs && el && fs.contains(el) ? Math.max(h, window.innerHeight - 170) : h;
  }

  /* zoom & pan: wheel / pinch = zoom time, drag = pan, wheel or drag on the left axis (or
     Shift+wheel) = stretch the value axis, double-click / reset button = back to full view */
  _addZoom(k) {
    const r = this.shadowRoot, svg = r.getElementById(`svg-${k}`), id = `svg-${k}`;
    if (!svg || svg._zoomBound) return;
    svg._zoomBound = true;
    svg.classList.add("zoomable");
    const card = svg.closest(".card");
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "zrs"; btn.textContent = "Reset zoom"; btn.hidden = true;
    btn.addEventListener("click", () => { delete this._zoom[id]; this._redrawSoon(); });
    if (card) card.appendChild(btn);
    const view = () => this._zoom[id] || (this._zoom[id] = { a: 0, b: 1, y: 1 });
    const geo = () => this._geo[k] || { L: 40, iw: Math.max(1, (svg.viewBox.baseVal?.width || 600) - 52), W: svg.viewBox.baseVal?.width || 600 };
    const pos = (ev) => { const b = svg.getBoundingClientRect(), g = geo(); const x = (ev.clientX - b.left) * ((g.W || b.width) / b.width); return { x, fx: Math.max(0, Math.min(1, (x - g.L) / g.iw)), axis: x < g.L }; };
    const zoomX = (v, fx, f) => {
      const span = v.b - v.a, ns = Math.max(0.02, Math.min(1, span * f)), c = v.a + fx * span;
      v.a = Math.max(0, Math.min(1 - ns, c - fx * ns)); v.b = v.a + ns;
    };
    svg.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const p = pos(ev), v = view(), f = ev.deltaY < 0 ? 0.8 : 1.25;
      if (p.axis || ev.shiftKey) v.y = Math.max(1, Math.min(20, v.y / f)); else zoomX(v, p.fx, f);
      this._redrawSoon();
    }, { passive: false });
    const pts = new Map(); let drag = null;
    svg.addEventListener("pointerdown", (ev) => {
      pts.set(ev.pointerId, ev.clientX);
      const p = pos(ev), v = view();
      drag = { x: ev.clientX, y: ev.clientY, a: v.a, b: v.b, vy: v.y, axis: p.axis, moved: false,
               dist: pts.size === 2 ? Math.abs([...pts.values()][0] - [...pts.values()][1]) : null };
    });
    svg.addEventListener("pointermove", (ev) => {
      if (!drag || !pts.has(ev.pointerId)) return;
      pts.set(ev.pointerId, ev.clientX);
      const v = view(), b = svg.getBoundingClientRect(), g = geo(), wpx = b.width * g.iw / (g.W || b.width);
      if (pts.size === 2 && drag.dist) {                         // pinch
        const vals = [...pts.values()], dist = Math.abs(vals[0] - vals[1]) || 1;
        const mid = ((vals[0] + vals[1]) / 2 - b.left) * ((g.W || b.width) / b.width);
        v.a = drag.a; v.b = drag.b; zoomX(v, Math.max(0, Math.min(1, (mid - g.L) / g.iw)), drag.dist / dist);
        drag.moved = true; this._redrawSoon(); return;
      }
      const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) < 5 && Math.abs(dy) < 5) return;
      drag.moved = true;
      if (drag.axis) v.y = Math.max(1, Math.min(20, drag.vy * Math.exp(-dy / 80)));
      else if (ev.pointerType !== "touch" || Math.abs(dx) > Math.abs(dy)) {
        const span = drag.b - drag.a, sh = -dx / wpx * span;
        v.a = Math.max(0, Math.min(1 - span, drag.a + sh)); v.b = v.a + span;
      }
      this._redrawSoon();
    });
    const end = (ev) => { pts.delete(ev.pointerId); if (!pts.size) drag = null; };
    svg.addEventListener("pointerup", end); svg.addEventListener("pointercancel", end);
    svg.addEventListener("dblclick", () => { delete this._zoom[id]; this._redrawSoon(); });
  }

  _redrawSoon() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = null; this._redrawCharts();
      const r = this.shadowRoot;
      for (const k of ["live", "ph", "hist", "ov", "pr", "pl", "pva"]) {
        const btn = r.getElementById(`svg-${k}`)?.closest(".card")?.querySelector(".zrs");
        if (btn) btn.hidden = !this._geo[k]?.zoomed;
      }
    });
  }

  _addFullscreenButtons() {
    const r = this.shadowRoot;
    r.querySelectorAll(".card").forEach((card) => {
      if (!card.querySelector(".chartw") || card.querySelector(".fsb")) return;
      const b = document.createElement("button");
      b.type = "button"; b.className = "fsb"; b.title = "Full screen"; b.setAttribute("aria-label", "Show chart full screen");
      b.innerHTML = PCP_FS_ICON;
      b.addEventListener("click", () => {
        if (r.fullscreenElement === card) document.exitFullscreen?.();
        else card.requestFullscreen?.().catch(() => this._say("Full screen is not available in this browser."));
      });
      card.classList.add("fsable");
      card.prepend(b);
    });
    this.shadowRoot.addEventListener("fullscreenchange", () => setTimeout(() => this._redrawCharts(), 60));
    document.addEventListener("fullscreenchange", () => setTimeout(() => this._redrawCharts(), 60));
  }

  // ------------------------------------------------------------------ shared builders
  _kpiHtml(items) {
    return items.map(([l, v, u, s, b, c, cls]) => `<div class="card"><div class="k-l">${l}</div><div class="k-v num ${cls || ""}">${v}<small>${u}</small></div><div class="k-s">${s}</div>${b === null || b === undefined ? "" : `<div class="bar"><i style="width:${b}%;background:${c}"></i></div>`}</div>`).join("");
  }
  _pct(a, b) { return Math.max(0, Math.min(100, (pN(a) ?? 0) / Math.max(0.01, pN(b) ?? 1) * 100)); }

  /* devices in stacking order: not controlled (forecast) first, then plan priority 1, 2, … */
  _stackOrder(d) {
    return d.devices.map((x, i) => ({ ...x, i }))
      .sort((a, b) => (a.forecast_only === b.forecast_only ? 0 : a.forecast_only ? -1 : 1)
        || (pN(a.plan_priority) ?? 99) - (pN(b.plan_priority) ?? 99) || a.i - b.i);
  }

  /* plan bars for n hours from now, stacked bottom-up: baseload, devices that are not controlled
     (forecast, lighter), then committed devices by plan priority – priority 1 at the bottom */
  _planSeries(d, n) {
    const idx = d.plan.index_now, at = (arr, t) => pN((arr || [])[idx + t]) ?? 0;
    const bars = [{ id: "base", name: "Baseload", color: "#94a3b8", values: [...Array(n).keys()].map((t) => at(d.plan.baseload_72h, t)) }];
    this._stackOrder(d).forEach((x) => {
      if (this._hidden[x.entry_id]) return;
      bars.push({ id: x.entry_id, name: x.forecast_only ? `${x.name} (forecast)` : `#${x.plan_priority} ${x.name}`,
        color: this._color(x.i), opacity: x.forecast_only ? 0.4 : 0.92,
        values: [...Array(n).keys()].map((t) => at(x.plan_72h, t)) });
    });
    const times = [...Array(n).keys()].map((t) => new Date(new Date(d.plan.start).getTime() + (idx + t) * 3600e3));
    return { bars, times, idx, at };
  }

  _planTip(d, s) {
    return (t) => {
      const tot = s.bars.reduce((a, b) => a + (pN(b.values[t]) ?? 0), 0);
      const rows = s.bars.filter((b) => (pN(b.values[t]) ?? 0) > 0.02).reverse().map((b) => `<div class="r"><span><i class="sw" style="background:${b.color}"></i> ${pE(b.name)}</span><b class="num">${pF(b.values[t])}</b></div>`).join("");
      return `<b>${pDay(s.times[t])} ${pHH(s.times[t])}</b><div class="r"><span>Total</span><b class="num">${tot.toFixed(2)} kWh</b></div>
        <div class="r"><span>All-in price</span><b class="num">${pF(s.at(d.plan.price_72h, t))} NOK</b></div>
        <div class="r"><span>Hour allowance</span><span class="num">${pF(s.at(d.plan.hour_cap_72h, t))} kWh</span></div>
        <div class="r"><span>Occupancy</span><span>${pE((PCP_OCC[(d.plan.occupancy_72h || [])[s.idx + t]] || ["–"])[0])}</span></div><div class="sep"></div>${rows}`;
    };
  }

  _tipAt(k, ev) {
    const g = this._geo[k], r = this.shadowRoot, svg = r.getElementById(`svg-${k}`), tip = r.getElementById(`tip-${k}`);
    if (!g || !tip) return;
    const b = svg.getBoundingClientRect(), mx = (ev.clientX - b.left) * (g.W / b.width), t = Math.floor((mx - g.L) / g.bw);
    const hv = svg.querySelector(".hv");
    if (t < 0 || t >= g.n) { tip.style.display = "none"; if (hv) hv.style.opacity = 0; return; }
    tip.innerHTML = g.html(t + (g.off || 0));
    tip.style.display = "block";
    const px = (g.L + t * g.bw) * (b.width / g.W); let left = px + 16; if (left + 220 > b.width) left = px - 230;
    tip.style.left = `${Math.max(0, left)}px`; tip.style.top = "8px";
    if (hv) { const cx = g.L + (t + 0.5) * g.bw; hv.setAttribute("x1", cx); hv.setAttribute("x2", cx); hv.style.opacity = 0.35; }
  }

  // ------------------------------------------------------------------ OVERVIEW
  _renderOverview(d) {
    const r = this.shadowRoot, k = d.kpi, np = d.norgespris || {};
    const steps = d.tariff.capacity_steps || [];
    const si = steps.findIndex((s) => s.upper_kw > 0 && Math.abs(s.upper_kw - k.active_step_kw) < 1e-6);
    const lower = si > 0 ? steps[si - 1].upper_kw : 0;
    const ssi = steps.findIndex((s) => s.upper_kw > 0 && Math.abs(s.upper_kw - k.selected_step_kw) < 1e-6);
    const selLower = ssi > 0 ? steps[ssi - 1].upper_kw : 0;
    const pn = k.price_now || {};
    r.getElementById("safe").innerHTML = k.safe_mode
      ? `<div class="safe"><b>SAFE MODE</b> · budget limited to the safe value until ${pHH(new Date(new Date(d.now).setMinutes(60, 0, 0)))}.
         ${k.safe_mode_sensor_ok ? "The grid sensor is back – safe mode is held to the end of the hour." : "The grid power sensor is not updating – check the meter / HAN reader."}
         ${k.safe_mode_since ? `Since ${pHH(new Date(k.safe_mode_since))}.` : ""}</div>` : "";
    r.getElementById("kpis").innerHTML = this._kpiHtml([
      ["Grid power", pF(k.grid_power_kw), "kW", `whole house · limit now ${pF(k.limit_now_kw)} kW`, this._pct(k.grid_power_kw, k.limit_now_kw), k.grid_power_kw > k.limit_now_kw ? "var(--error-color)" : "var(--primary-color)"],
      ["This hour", pF(k.energy_this_hour_kwh), "kWh", `projected ${pF(k.energy_projected_kwh, 1)} of ${pF(k.hour_allowance_kwh)} kWh allowed · avg 60 min ${pF(k.p_avg_60m_kw)} kW · ${k.hour_energy_source === "hour_sensor" ? "hour sensor" : "integrated grid power"}`, this._pct(k.energy_projected_kwh, k.hour_allowance_kwh), k.energy_projected_kwh > k.hour_allowance_kwh ? "var(--warning-color)" : "var(--success-color)"],
      ["Capacity step", `${lower}–${pF(k.active_step_kw, 0)}`, "kW",
        (k.step_locked ? `<span class="warnt">⚠ selected ${selLower}–${pF(k.selected_step_kw, 0)} kW · in use ${lower}–${pF(k.active_step_kw, 0)} kW (locked by this month's top-3)</span> · ` : `selected step · `)
        + `top-3 average ${pF(k.top3_mean_kw)} kWh/h · ${pNok(k.capacity_charge_nok)} NOK`, this._pct(k.top3_mean_kw, k.active_step_kw), k.step_locked ? "var(--error-color)" : "var(--warning-color)", k.step_locked ? "warnv" : ""],
      ["Price now", pF(pn.total), "NOK/kWh", `energy ${pF(pn.energy)} + grid ${pF(pn.grid)}${pn.period ? ` (${pE(pn.period)})` : ""}`],
      ["Month to date", pNok(k.month_cost_nok), "NOK", `${pNok(k.month_energy_kwh)} kWh · ${k.month_source?.source === "statistics" ? `from meter statistics${k.month_source.missing_hours ? ` (${k.month_source.missing_hours} h missing)` : ""}${k.month_source.corrected_hours ? ` · ${k.month_source.corrected_hours} implausible h corrected` : ""}` : "live (no meter statistics)"}`],
      np.active ? ["Norgespris left", pNok(np.remaining_kwh), "kWh", np.projected_reach_date ? `runs out ≈ ${pDay(new Date(np.projected_reach_date))}` : `of ${pNok(np.volume_kwh)} kWh · lasts the month`, 100 - this._pct(np.used_kwh, np.volume_kwh), "var(--info-color, var(--primary-color))"]
        : ["Norgespris benefit", k.shadow_benefit_nok === null ? "Off" : `${k.shadow_benefit_nok >= 0 ? "+" : ""}${pNok(k.shadow_benefit_nok)}`, k.shadow_benefit_nok === null ? "" : "NOK", "vs. optimised spot this month"],
    ]);
    this._renderLive(d);
    this._renderPhases(d);
    this._renderHistory(d);
    // plan (24 h) + device table
    const s = this._planSeries(d, 24);
    const g = pcBarChart(r.getElementById("svg-ov"), this._w("cw-ov"), { view: this._zoom["svg-ov"], n: 24, times: s.times, bars: s.bars, focusId: this._focus, height: this._fsH("svg-ov", 220), yLabel: "kWh/h", yrLabel: "NOK/kWh",
      cap: { values: s.times.map((_, t) => s.at(d.plan.hour_cap_72h, t)), label: "hour allowance" },
      lines: [{ name: "price", color: "var(--primary-text-color)", values: s.times.map((_, t) => s.at(d.plan.price_72h, t)), axis: "r", step: true }] });
    this._geo.ov = { ...g, html: this._planTip(d, s) };
    this._renderDevTable(d);
  }

  _renderLive(d) {
    const r = this.shadowRoot, he = d.history_entities || {}, h = this._hist.hour || {}, now = this._hist.t || Date.now();
    // fine resolution: one bin per ~5 s (or per pixel), value = the step value at that moment
    const t0 = now - 3600e3, bins = Math.max(120, Math.min(720, Math.round(this._w("svg-live") - 52)));
    const step = (pts) => { const out = []; let j = 0, cur = null;
      for (let i = 0; i < bins; i++) { const t = t0 + (i + 0.5) * 3600e3 / bins; while (j < pts.length && pts[j][0] <= t) { cur = pts[j][1]; j++; } out.push(cur); }
      return out; };
    const stacked = [];
    let sumBins = new Array(bins).fill(0);
    // stack bottom-up in plan-priority order (priority 1 at the bottom), "Other" on top
    const order = d.devices.map((x, i) => ({ ...x, i })).sort((a, b) => (pN(a.plan_priority) ?? 99) - (pN(b.plan_priority) ?? 99));
    order.forEach((x) => {
      const id = he.devices?.[x.entry_id]; const pts = (h[id] || []).map(([t, v]) => [t, v * this._scale(id)]).sort((a, b) => a[0] - b[0]);
      if (!pts.length) return;
      const vals = step(pts).map((v) => Math.max(0, v ?? 0));
      sumBins = sumBins.map((a, j) => a + vals[j]);
      stacked.push({ name: x.name, color: this._color(x.i), values: vals, live: pN(x.live_kw) });
    });
    const gid = he.grid_power, gp = (h[gid] || []).map(([t, v]) => [t, v * this._scale(gid)]).sort((a, b) => a[0] - b[0]);
    let other = null;
    if (gp.length) {
      const g = step(gp).map((v) => v ?? 0);
      const vals = g.map((v, j) => Math.max(0, v - sumBins[j]));
      other = Math.max(0, (pN(d.kpi.grid_power_kw) ?? 0) - order.reduce((a, x) => a + (pN(x.live_kw) ?? 0), 0));
      stacked.push({ name: "Other", color: "#94a3b8", values: vals, live: other });
    }
    this._geo.live = pcTsChart(r.getElementById("svg-live"), this._w("svg-live"), { view: this._zoom["svg-live"], t0, t1: now, bins, stacked, yLabel: "kW", height: this._fsH("svg-live", 230) });
    r.getElementById("lg-live").innerHTML = stacked.length
      ? stacked.slice().reverse().map((st) => `<span class="lg static"><span class="sw" style="background:${st.color}"></span>${pE(st.name)} <b class="num">${pF(st.live, 2)} kW</b></span>`).join("")
        + `<span class="lg static tot">Grid <b class="num">${pF(d.kpi.grid_power_kw, 2)} kW</b></span>`
      : `<span class="hint">No recorder history yet for the power sensors.</span>`;
  }

  _renderPhases(d) {
    const r = this.shadowRoot, he = d.history_entities || {}, h = this._hist.day || {}, now = this._hist.t || Date.now();
    const cc = { L1: "#06b6d4", L2: "#8b5cf6", L3: "#94a3b8" }, vc = { "L1-L2": "#0e7490", "L2-L3": "#6d28d9", "L3-L1": "#64748b" };
    const series = [];
    for (const [ph, id] of Object.entries(he.currents || {})) if (id && h[id]) series.push({ name: `${ph} A`, color: cc[ph], points: h[id].map(([t, v]) => [t, v * this._scale(id)]), width: 1.2 });
    // voltages below 100 V are treated as missing (dead/faulty sensor) so they cannot squash the 215–230 V window
    const bad = [];
    for (const [lbl, id] of Object.entries(he.voltages || {})) {
      if (!id || !h[id]) continue;
      const pts = h[id].map(([t, v]) => [t, pN(v) !== null && v >= PCP_MIN_VALID_V ? v : null]);
      const valid = pts.some(([, v]) => v !== null);
      if (!valid) bad.push(lbl);
      series.push({ name: `${lbl} V${valid ? "" : " · no data"}`, color: vc[lbl], axis: "r", points: pts, width: 1, invalid: !valid });
    }
    this._geo.ph = pcTsChart(r.getElementById("svg-ph"), this._w("svg-ph"), { view: this._zoom["svg-ph"], t0: now - 24 * 3600e3, t1: now, series, yLabel: "A", yrLabel: "V", height: this._fsH("svg-ph", 210), rWindow: [215, 230] });
    r.getElementById("lg-ph").innerHTML = series.length ? series.map((x) => `<span class="lg static${x.invalid ? " bad" : ""}" ${x.invalid ? `title="All values are below ${PCP_MIN_VALID_V} V – check the sensor"` : ""}><span class="sw" style="background:${x.color}"></span>${x.name}</span>`).join("")
        + (bad.length ? `<span class="hint warnt">⚠ ${bad.join(", ")}: no valid voltage (all values below ${PCP_MIN_VALID_V} V) – check the sensor.</span>` : "")
      : `<span class="hint">No recorder history yet. Voltage sensors are optional (Configure → Sensors and general settings).</span>`;
    r.getElementById("phv").innerHTML = ["L1", "L2", "L3"].map((ph) => `<span>${ph} <b>${pF(d.phases[ph]?.amps, 1)} A</b></span>`).join("");
  }

  _renderHistory(d) {
    const r = this.shadowRoot, back = 72, ahead = 72 - d.plan.index_now, n = back + ahead;
    const start = new Date(d.plan.start).getTime() + d.plan.index_now * 3600e3;    // current hour
    const times = [...Array(n).keys()].map((i) => new Date(start + (i - back) * 3600e3));
    const meas = new Array(n).fill(null), price = new Array(n).fill(null), planned = new Array(n).fill(null);
    const stats = this._hist.stats || [], sc = this._scale(d.history_entities?.grid_power);
    for (const [t, v] of stats) { const i = Math.round((t - (start - back * 3600e3)) / 3600e3); if (i >= 0 && i < back) meas[i] = v * sc; }
    for (const h of d.plan_history || []) {
      const i = Math.round((Date.parse(h.ts) - (start - back * 3600e3)) / 3600e3);
      if (i >= 0 && i < back) { if (meas[i] === null) meas[i] = h.actual_kwh; price[i] = h.price; planned[i] = h.planned_kwh; }
    }
    const s = this._planSeries(d, ahead);
    const bars = [{ id: "meas", name: "Measured", color: "#64748b", values: meas }];
    for (const b of s.bars) bars.push({ ...b, values: [...new Array(back).fill(null), ...b.values] });
    for (let t = 0; t < ahead; t++) price[back + t] = s.at(d.plan.price_72h, t);
    const g = pcBarChart(r.getElementById("svg-hist"), this._w("cw-hist"), { view: this._zoom["svg-hist"], n, times, bars, nowIdx: back, height: this._fsH("svg-hist", 260), yLabel: "kWh/h", yrLabel: "NOK/kWh",
      lines: [{ name: "price", color: "var(--primary-text-color)", values: price, axis: "r", step: true, gap: true },
        { name: "plan (past)", color: "var(--warning-color)", values: planned, dash: "3 3", width: 1.3, gap: true }] });
    this._geo.hist = { ...g, html: (i) => {
      const isPast = i < back, tot = bars.reduce((a, b) => a + (pN(b.values[i]) ?? 0), 0);
      return `<b>${pDay(times[i])} ${pHH(times[i])} · ${isPast ? "measured" : "planned"}</b><div class="r"><span>${isPast ? "Consumption" : "Planned"}</span><b class="num">${pF(tot)} kWh</b></div>
        ${isPast && planned[i] !== null ? `<div class="r"><span>Plan expected</span><span class="num">${pF(planned[i])} kWh</span></div>` : ""}
        <div class="r"><span>Price</span><span class="num">${pF(price[i])} NOK</span></div>`;
    } };
    r.getElementById("lg-hist").innerHTML = `<span class="lg static"><span class="sw" style="background:#64748b"></span>Measured (recorder statistics)</span>
      <span class="lg static"><span class="sw" style="background:#94a3b8"></span>Planned: baseload + devices</span>
      <span class="lg static"><span class="sw ln" style="border-color:var(--warning-color)"></span>What the plan expected</span>
      <span class="lg static"><span class="sw ln" style="border-color:var(--primary-text-color);border-top-style:solid"></span>Price</span>`;
  }

  _renderDevTable(d) {
    const r = this.shadowRoot, idx = d.plan.index_now;
    const sorted = d.devices.map((x, i) => ({ ...x, _i: i }))
      .sort((a, b) => (pN(a.plan_priority) ?? 99) - (pN(b.plan_priority) ?? 99) || a._i - b._i);
    r.getElementById("devs").innerHTML = sorted.map((x) => { const i = x._i;
      const st = PCP_STATUS[x.committed ? x.ui_status : "manual"] || PCP_STATUS.waiting;
      const plan = (x.plan_72h || []).slice(idx, idx + 24).map((v) => pN(v) ?? 0);
      const mx = Math.max(...plan, 0.5);
      const sp = plan.map((v) => `<i style="height:${Math.max(4, v / mx * 100)}%;background:${this._color(i)};opacity:${v > 0.05 ? (x.forecast_only ? 0.45 : 0.9) : 0.2}"></i>`).join("");
      const n = x.next_action;
      const nx = x.forecast_only ? `≈ ${plan.reduce((a, b) => a + b, 0).toFixed(1)} kWh/24 h` : n ? (n.running_now ? `now → ${pHH(new Date(n.end))}` : `${pHH(new Date(n.start))}–${pHH(new Date(n.end))}`) : "none";
      return `<div class="dev${this._focus === x.entry_id ? " sel" : ""}" role="button" tabindex="0" data-id="${x.entry_id}" aria-pressed="${this._focus === x.entry_id}">
        <span class="dn"><i class="sw" style="background:${this._color(i)}"></i> ${pE(x.name)}<small>${PCP_TYPE[x.device_type] || ""} · ${pE(x.phase_pair || "")}</small></span>
        <span><span class="chip c-${st[1]}">${st[0]}</span></span><span class="x num">${x.committed ? `${pF(x.live_kw)} kW` : (x.has_power_sensor === false ? "–" : `${pF(x.live_kw)} kW`)}</span>
        <span class="x num">${nx}</span><span class="x spark" title="max ${mx.toFixed(1)} kWh/h">${sp}<em>${mx.toFixed(1)}</em></span><span class="x"><span class="prio">${x.plan_priority}</span></span></div>`;
    }).join("") || `<div class="hint" style="padding:10px">No devices yet. Add them under Settings → Devices &amp; services → Power Controller.</div>`;
    r.querySelectorAll("#devs .dev").forEach((el) => {
      const go = () => { const id = el.dataset.id; this._focus = this._focus === id ? null : id;
        r.getElementById("sel").textContent = this._focus ? `Highlighting ${d.devices.find((x) => x.entry_id === id)?.name}` : "All devices";
        this._renderOverview(this._data); };
      el.addEventListener("click", go);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    });
  }

  // ------------------------------------------------------------------ PRICE
  _renderPrice(d) {
    const r = this.shadowRoot, k = d.kpi, pn = k.price_now || {}, np = d.norgespris || {};
    r.getElementById("kpis-pr").innerHTML = this._kpiHtml([
      ["Price now", pF(pn.total), "NOK/kWh", `incl. VAT · ${pE(d.price_model)}`],
      ["Energy part", pF(pn.energy), "NOK/kWh", "after price model + mark-up"],
      ["Grid part", pF(pn.grid), "NOK/kWh", `energy charge + fees${pn.period ? ` · ${pE(pn.period)}` : ""}`],
      ["Capacity charge", pNok(k.capacity_charge_nok), "NOK", `this month · top-3 ${pF(k.top3_mean_kw)} kWh/h`],
      ["Month to date", pNok(k.month_cost_nok), "NOK", `${pNok(k.month_energy_kwh)} kWh`],
    ]);
    // 15-minute prices (Nord Pool quarters) when available, otherwise hourly
    const q = (d.plan.price_15m || []).length >= 4, spp = q ? 4 : 1;
    const idx = d.plan.index_now * spp, n = 72 * spp - idx;
    const hr = (t) => Math.floor((idx + t) / spp);
    const tot = (t) => pN(q ? d.plan.price_15m[idx + t] : d.plan.price_72h[idx + t]);
    const spot = (t) => pN(q ? d.plan.spot_15m[idx + t] : d.plan.spot_72h[idx + t]);
    const gridH = (h) => { const a = pN(d.plan.price_72h[h]), e = pN(d.plan.energy_72h[h]); return a === null || e === null ? null : Math.max(0, a - e); };
    const times = [...Array(n).keys()].map((t) => new Date(new Date(d.plan.start).getTime() + (idx + t) * 3600e3 / spp));
    const grid = times.map((_, t) => gridH(hr(t)));
    const energy = times.map((_, t) => { const a = tot(t), g = grid[t]; return a === null || g === null ? null : Math.max(0, a - g); });
    const g = pcBarChart(r.getElementById("svg-pr"), this._w("cw-pr"), { view: this._zoom["svg-pr"], n, times, height: this._fsH("svg-pr", 250), yLabel: "NOK/kWh",
      bars: [{ id: "e", name: "Energy", color: "#64748b", values: energy }, { id: "g", name: "Grid", color: "#a8a29e", values: grid }],
      lines: [{ name: "spot", color: "var(--primary-text-color)", values: times.map((_, t) => spot(t)), dash: "4 3", width: 1.3 }] });
    this._geo.pr = { ...g, html: (t) => `<b>${pDay(times[t])} ${pHH(times[t])}${q ? "–" + pHH(new Date(times[t].getTime() + 900e3)) : ""}</b><div class="r"><span>All-in</span><b class="num">${pF(tot(t))}</b></div>
      <div class="r"><span>Energy</span><span class="num">${pF(energy[t])}</span></div><div class="r"><span>Grid (${pE((d.plan.grid_period_72h || [])[hr(t)] || "")})</span><span class="num">${pF(grid[t])}</span></div>
      <div class="r"><span>Spot incl. VAT</span><span class="num">${pF(spot(t))}</span></div>` };
    r.getElementById("prres").textContent = q ? "15-minute prices (Nord Pool quarters)" : "hourly prices";
    r.getElementById("lg-pr").innerHTML = `<span class="lg static"><span class="sw" style="background:#64748b"></span>Energy</span><span class="lg static"><span class="sw" style="background:#a8a29e"></span>Grid + fees</span><span class="lg static"><span class="sw ln" style="border-color:var(--primary-text-color)"></span>Spot (shadow)</span>`;
    // Norgespris volume
    r.getElementById("npm").textContent = np.active ? "price model: Norgespris" : "not active (price model is not Norgespris)";
    const used = this._pct(np.used_kwh, np.volume_kwh), proj = this._pct(np.projected_month_kwh, np.volume_kwh);
    r.getElementById("np").innerHTML = `<div class="big">${pNok(np.remaining_kwh)} <small>kWh left</small></div>
      <div class="gauge"><i style="width:${used}%"></i><em style="left:${Math.min(100, proj)}%" title="projected end of month"></em></div>
      <div class="ticks"><span>0</span><span>${pNok((np.volume_kwh || 0) / 2)}</span><span>${pNok(np.volume_kwh)} kWh</span></div>
      <div class="kv"><span>Used this month</span><b>${pNok(np.used_kwh)} kWh (${pF(np.used_pct, 0)} %)</b>
        <span>Average per day</span><b>${pF(np.avg_per_day_kwh, 1)} kWh</b>
        <span>Projected this month</span><b>${pNok(np.projected_month_kwh)} kWh</b>
        <span>Volume runs out</span><b>${np.projected_reach_date ? pDay(new Date(np.projected_reach_date)) : "not this month"}</b></div>
      <div class="hint">Above the volume the energy is billed at spot price. The marker shows the projected month total.</div>`;
    this._renderTariff(d);
    this._renderShadow(d);
  }

  _renderTariff(d) {
    const r = this.shadowRoot, t = d.tariff, k = d.kpi, nowD = new Date(d.now), nowMin = nowD.getHours() * 60 + nowD.getMinutes();
    r.getElementById("tfn").textContent = `${t.name} · valid from ${t.valid_from}`;
    const names = [...new Set(t.today.map((x) => x.period))];
    const pc = ["#94a3b8", "#64748b", "#a8a29e", "#78716c", "#9ca3af"];
    const colOf = (n) => pc[names.indexOf(n) % pc.length];
    const legend = names.map((n) => { const p = t.today.find((x) => x.period === n).price; return `<span><i class="sw" style="background:${colOf(n)}"></i> ${pE(n)} ${pF(p)}</span>`; }).join("");
    const stp = t.capacity_steps;
    const ai = stp.findIndex((s) => s.upper_kw > 0 && Math.abs(s.upper_kw - k.active_step_kw) < 1e-6);
    const shown = stp.slice(0, Math.max(6, ai + 2)).slice(0, 8);
    const maxP = Math.max(...shown.map((s) => s.monthly_nok), 1);
    r.getElementById("tf").innerHTML = `<div class="hint" style="margin-bottom:6px">Grid energy charge today incl. fees and VAT (NOK/kWh)</div>
      <div class="band">${t.today.map((x) => `<i title="${String(x.hour).padStart(2, "0")}:00 ${pE(x.period)} ${pF(x.price)}" style="background:${colOf(x.period)}"></i>`).join("")}<span class="now" style="left:${nowMin / 1440 * 100}%"></span></div>
      <div class="ticks"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div><div class="keys">${legend}</div>
      <div class="hint" style="margin-top:14px">Capacity steps (NOK/month incl. VAT) · active step highlighted</div>
      <div class="ladder">${shown.map((s, i) => `<div class="${i === ai ? "on" : ""}" style="height:${25 + (s.monthly_nok / maxP) * 75}%">${pNok(s.monthly_nok)}</div>`).join("")}</div>
      <div class="ll">${shown.map((s, i) => `<span>${s.upper_kw > 0 ? `${i ? shown[i - 1].upper_kw : 0}–${s.upper_kw}` : `>${i ? shown[i - 1].upper_kw : 0}`}</span>`).join("")}</div>`;
  }

  _renderShadow(d) {
    const r = this.shadowRoot, sh = d.shadow;
    if (!sh) {
      r.getElementById("sh").innerHTML = `<div class="hint">Shadow evaluation is off. Select <b>Shadow Comparison Model</b> on the Power Controller device to enable it.</div>`;
      return;
    }
    const rows = [["Actual · " + pE(d.price_model), sh.actual.total_nok, "var(--primary-color)"], ["Optimised for spot", sh.optimised_spot.total_nok, "#8b5cf6"], ["Same usage on spot", sh.passive_spot.total_nok, "var(--secondary-text-color)"]];
    const mx = Math.max(...rows.map((x) => x[1]), 1), b = sh.benefit_nok;
    r.getElementById("shm").textContent = `Shadow evaluation · ${sh.month}`;
    r.getElementById("sh").innerHTML = `<div class="big ${b >= 0 ? "pos" : "neg"}">${b >= 0 ? "+" : ""}${pNok(b)} NOK</div>
      <div class="hint" style="margin-bottom:12px">${b >= 0 ? "Your current price model is cheaper this month" : "Spot with optimisation would have been cheaper this month"} · 12 months: ${sh.rolling_12_months.benefit_nok >= 0 ? "+" : ""}${pNok(sh.rolling_12_months.benefit_nok)} NOK</div>
      ${rows.map(([n, v, c]) => `<div class="sc"><div class="t"><span>${n}</span><b class="num">${pNok(v)} NOK</b></div><div class="bar" style="height:9px"><i style="width:${v / mx * 100}%;background:${c}"></i></div></div>`).join("")}
      <div class="hint">Optimiser gain on spot: ${pNok(sh.optimiser_gain_nok)} NOK · EV shortfall ${pF(sh.ev_shortfall_kwh, 1)} kWh · ${sh.hours} h counted (${sh.hours_estimated_spot} with estimated spot)</div>`;
  }

  // ------------------------------------------------------------------ SETTINGS
  _renderSettings(d) {
    const box = this.shadowRoot.getElementById("setg");
    if (!box) return;
    const active = this.shadowRoot.activeElement;
    const typing = active && ["INPUT", "SELECT"].includes(active.tagName);
    if (!(typing && box.contains(active))) this._renderSettingFields(d, box);
    const nl = this.shadowRoot.getElementById("nlog"), nt = d.notifications || {};
    if (nl) {
      nl.innerHTML = `<div class="kv"><span>Service</span><b>${pE(nt.service || "not configured")}</b><span>Active conditions</span><b>${pE((nt.active || []).join(", ") || "none")}</b></div>`
        + ((nt.log || []).slice().reverse().map((m) => `<div class="nrow"><span class="chip c-${m.level === "error" ? "bad" : "warn"}">${pE(m.level)}</span><b>${pE(m.title.replace("Power Controller: ", ""))}</b><span class="hint">${pE(m.message)}</span><small class="hint">${pDay(new Date(m.ts))} ${pHH(new Date(m.ts))}</small></div>`).join("")
          || `<div class="hint">No messages sent yet.</div>`);
      const nb = this.shadowRoot.getElementById("ntest");
      if (nb && !nb._b) { nb._b = true; nb.addEventListener("click", () => { this._call("power_controller", "test_notification", {}); this._say("Test notification sent"); }); }
    }
    const hw = this.shadowRoot.getElementById("hwm");
    if (hw && !(typing && hw.contains(active))) this._renderHotwater(d, hw);
    const tz = this.shadowRoot.getElementById("thz");
    if (tz && !(typing && tz.contains(active))) this._renderThermal(d, tz);
  }

  _help(text, extra = "") {
    if (!text) return "";
    return `<span class="hq" tabindex="0" role="button" aria-label="Help"><span aria-hidden="true">?</span><span class="tipbox" role="tooltip">${text}${extra}</span></span>`;
  }

  _renderSettingFields(d, box) {
    const st = (id) => this._hass?.states?.[id];
    const name = (e) => e.label || (st(e.id)?.attributes?.friendly_name || e.id).replace(/^Power Controller /, "");
    const field = (e) => {
      const id = e.id, s = st(id); if (!s) return "";
      const dom = id.split(".")[0], a = s.attributes || {};
      let ctl = "";
      if (dom === "number") {
        ctl = `<input type="number" data-eid="${pE(id)}" value="${pE(s.state)}" min="${pE(a.min)}" max="${pE(a.max)}" step="${pE(a.step)}" inputmode="decimal"><span class="u">${pE(a.unit_of_measurement || "")}</span>`;
      } else if (dom === "select") {
        ctl = `<select data-eid="${pE(id)}">${(a.options || []).map((o) => `<option ${o === s.state ? "selected" : ""}>${pE(o)}</option>`).join("")}</select>`;
      } else if (dom === "time") {
        ctl = `<input type="time" data-eid="${pE(id)}" value="${pE(String(s.state).slice(0, 5))}">`;
      } else if (dom === "datetime") {
        const v = s.state && s.state !== "unknown" ? new Date(s.state) : null;
        const loc = v && !isNaN(v) ? new Date(v.getTime() - v.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "";
        ctl = `<input type="datetime-local" data-eid="${pE(id)}" value="${loc}">`;
      }
      const opts = PCP_OPT_HELP[e.key];
      const optHtml = opts ? `<dl class="opth">${Object.entries(opts).map(([o, t]) => `<dt${o === s.state ? ' class="cur"' : ""}>${pE(o)}</dt><dd>${t}</dd>`).join("")}</dl>` : "";
      const cur = opts && opts[s.state] ? `<em class="optcur">${pE(s.state)}: ${opts[s.state]}</em>` : "";
      return `<label class="sf"><span>${pE(name(e))}${this._help(PCP_HELP[e.key], optHtml)}<small>${pE(id)}</small>${cur}</span><span class="sc2">${ctl}</span></label>`;
    };
    box.innerHTML = (d.settings || []).map((g) => `<div class="card"><h2>${pE(g.title)}</h2><div class="sfl">${g.entities.map((e) => field(typeof e === "string" ? { id: e } : e)).join("")}</div></div>`).join("");
    box.querySelectorAll("[data-eid]").forEach((el) => el.addEventListener("change", () => {
      const id = el.dataset.eid, dom = id.split(".")[0];
      if (dom === "number") {
        const v = Number(el.value); if (el.value === "" || isNaN(v)) return;
        this._call("number", "set_value", { entity_id: id, value: v });
      } else if (dom === "select") this._call("select", "select_option", { entity_id: id, option: el.value });
      else if (dom === "time" && el.value) this._call("time", "set_value", { entity_id: id, time: `${el.value}:00` });
      else if (dom === "datetime" && el.value) this._call("datetime", "set_value", { entity_id: id, datetime: `${el.value.replace("T", " ")}:00` });
      this._say(`${id} saved`);
    }));
    this._bindHelp(box);
  }

  _bindHelp(root) {
    root.querySelectorAll(".hq").forEach((q) => {
      const toggle = (ev) => { ev.preventDefault(); ev.stopPropagation(); const o = q.classList.contains("open");
        root.querySelectorAll(".hq.open").forEach((x) => x.classList.remove("open")); if (!o) q.classList.add("open"); };
      q.addEventListener("click", toggle);
      q.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") toggle(ev); if (ev.key === "Escape") q.classList.remove("open"); });
    });
  }

  // ------------------------------------------------------------------ HOT-WATER MODELS
  _renderHotwater(d, box) {
    const tanks = d.hotwater_models || [];
    if (!tanks.length) { box.innerHTML = `<div class="card"><div class="hint">No water heaters configured.</div></div>`; return; }
    const ST = { learning: ["Learning", "info"], settling: ["Settling", "warn"], stable: ["Stable", "ok"], manual: ["Manual values", "mut"], no_model: ["Not learning", "mut"] };
    const H = PCP_HW_HELP;
    const fact = (lbl, help, val, sub = "") => `<div class="tf"><span>${lbl}${this._help(help)}</span><b>${val}</b>${sub ? `<small>${sub}</small>` : ""}</div>`;
    const prof = (a, b) => {
      const mx = Math.max(0.05, ...a, ...b), W = 300, Ht = 70, bw = W / 24;
      let o = "";
      for (let h = 0; h < 24; h++) {
        const ha = (a[h] / mx) * (Ht - 12), hb = (b[h] / mx) * (Ht - 12);
        o += `<rect x="${h * bw + 1}" y="${Ht - 10 - ha}" width="${bw / 2 - 1}" height="${Math.max(0.5, ha)}" fill="#0e7490"/>`;
        o += `<rect x="${h * bw + bw / 2}" y="${Ht - 10 - hb}" width="${bw / 2 - 1}" height="${Math.max(0.5, hb)}" fill="#a8a29e"/>`;
        if (h % 6 === 0) o += `<text x="${h * bw}" y="${Ht}" class="tx">${String(h).padStart(2, "0")}</text>`;
      }
      return `<svg viewBox="0 0 ${W} ${Ht + 2}" class="hwp" role="img" aria-label="Hot water use per hour">${o}</svg>`;
    };
    box.innerHTML = tanks.map((t) => {
      if (t.status === "no_model") {
        return `<div class="card"><div class="hd"><h2>${pE(t.name)}</h2><span class="chip c-mut">Not learning</span></div>
          <div class="hint">Learning needs both a water temperature sensor ${t.has_temp_sensor ? "✓" : "✗"} and a power sensor ${t.has_power_sensor ? "✓" : "✗"} on this device (Configure). Until then the planner uses the fixed defaults.</div></div>`;
      }
      const st = ST[t.status] || ST.learning;
      const loss = t.loss_w_per_c === null || t.loss_w_per_c === undefined ? `${pF(t.loss_w_per_c_default, 1)} W/°C` : `${pF(t.loss_w_per_c, 1)} W/°C`;
      return `<div class="card hw" data-dev="${pE(t.device_id)}">
        <div class="hd"><div><h2>${pE(t.name)}</h2><div class="hint">${pF(t.volume_l, 0)} l · ${pF(t.capacity_kwh_per_c, 2)} kWh/°C${t.water_temp !== null ? ` · now ${pF(t.water_temp, 1)} °C` : ""}</div></div><span class="chip c-${st[1]}">${st[0]}</span></div>
        <div class="tfs">
          ${fact("Standby loss", H.loss, loss, t.loss_w_per_c === null ? "default (not learned yet)" : `default ${pF(t.loss_w_per_c_default, 1)} W/°C`)}
          ${fact("Loss per day", H.lossday, `${pF(t.loss_kwh_per_day_at_65, 2)} kWh`, "at 65 °C")}
          ${fact("Cooling", H.cool, `${pF(t.cooling_c_per_h_at_65, 2)} °C/h`, "at 65 °C, no tapping")}
          ${fact("Heat-up", H.heat, t.heatup_c_per_h === null ? "–" : `${pF(t.heatup_c_per_h, 1)} °C/h`, "with the element on")}
          ${fact("Use, workday", H.use, `${pF(t.daily_use_workday_kwh, 1)} kWh`, `${t.days_workday} days learned`)}
          ${fact("Use, day off", H.use, `${pF(t.daily_use_dayoff_kwh, 1)} kWh`, `${t.days_dayoff} days learned`)}
          ${fact("Learned from", H.samples, `${t.samples} steps`, `${t.quiet_samples} quiet · ${t.skipped} skipped${t.last_update ? ` · last ${pHH(new Date(t.last_update))}` : ""}`)}
        </div>
        ${prof(t.profile_workday || [], t.profile_dayoff || [])}
        <div class="legend static"><span class="lg static"><span class="sw" style="background:#0e7490"></span>Workday kWh/h</span><span class="lg static"><span class="sw" style="background:#a8a29e"></span>Day off kWh/h</span><span class="hint" style="align-self:center">learned hot-water use per hour</span></div>
        <details class="tzm"><summary>Adjust manually / reset</summary>
          <div class="hint" style="margin:6px 0 10px">Manual values replace the learned ones and pause learning. The daily use scales the learned hourly profile. Reset starts learning again from the defaults.</div>
          <div class="tzr">
            <label class="sf"><span>Standby loss${this._help(H.loss)}</span><span class="sc2"><input type="number" step="0.1" min="0.1" max="50" data-f="loss" value="${pF(t.loss_w_per_c ?? t.loss_w_per_c_default, 1)}"><span class="u">W/°C</span></span></label>
            <label class="sf"><span>Daily use${this._help(H.use)}</span><span class="sc2"><input type="number" step="0.5" min="0" max="100" data-f="use" value="${pF(t.daily_use_workday_kwh, 1)}"><span class="u">kWh</span></span></label>
          </div>
          <div class="tzb"><button type="button" class="btn pri" data-a="set">Apply manual values</button>
            <button type="button" class="btn" data-a="${t.frozen ? "unfreeze" : "freeze"}">${t.frozen ? "Resume learning" : "Pause learning"}</button>
            <button type="button" class="btn" data-a="reset">Reset</button></div>
        </details></div>`;
    }).join("");
    box.querySelectorAll(".hw").forEach((card) => {
      const dev = card.dataset.dev;
      card.querySelectorAll("[data-a]").forEach((b) => b.addEventListener("click", () => {
        const a = b.dataset.a, data = { device_id: dev, action: a };
        if (a === "reset" && !confirm("Reset the learned hot-water model?")) return;
        if (a === "set") {
          const loss = Number(card.querySelector("[data-f=loss]").value), use = Number(card.querySelector("[data-f=use]").value);
          if (!(loss > 0) || !(use >= 0)) { this._say("Enter valid values"); return; }
          Object.assign(data, { loss_w_per_c: loss, daily_kwh: use, freeze: true });
        }
        this._call("power_controller", "hotwater_model", data);
        this._say(`Hot-water model: ${a}`);
      }));
    });
    this._bindHelp(box);
  }

  // ------------------------------------------------------------------ THERMAL MODELS
  _renderThermal(d, box) {
    const zones = d.thermal_zones || [];
    if (!zones.length) { box.innerHTML = `<div class="card"><div class="hint">No thermal zones yet.</div></div>`; return; }
    const ST = { learning: ["Learning", "info"], settling: ["Settling", "warn"], stable: ["Stable", "ok"], manual: ["Manual values", "mut"] };
    const H = PCP_THERMAL_HELP;
    const fact = (lbl, help, val, sub = "") => `<div class="tf"><span>${lbl}${this._help(help)}</span><b>${val}</b>${sub ? `<small>${sub}</small>` : ""}</div>`;
    box.innerHTML = zones.map((z, zi) => {
      const st = ST[z.status] || ST.learning;
      const devs = z.devices.length ? z.devices.map((x) => `${pE(x.name)} <small>(${x.heater_kind === "stepless" ? "stepless" : "on/off"}, ×${pF(x.heating_coefficient, 1)})</small>`).join(" · ") : `<span class="hint">no heating devices in this zone</span>`;
      const seed = z.seed || {};
      const chg = (cur, base) => { const c = pN(cur), b = pN(base); if (c === null || !b) return ""; const p = (c / b - 1) * 100; return Math.abs(p) < 1 ? "= start value" : `${p > 0 ? "+" : ""}${p.toFixed(0)} % vs. start`; };
      return `<div class="card tz" data-zone="${pE(z.zone_id)}">
        <div class="hd"><div><h2>${pE(z.name)}</h2><div class="hint">${pE(z.zone_id)} · ${pF(z.area_m2, 0)} m² · ${pE(z.insulation)}${z.window_sensor ? ` · window ${z.window_open ? "<b>open – learning paused</b>" : "closed"}` : " · no window sensor"}</div></div>
          <span class="chip c-${st[1]}">${st[0]}</span></div>
        <div class="hint" style="margin-bottom:10px">Heating: ${devs}</div>
        <div class="tfs">
          ${fact("Room now", H.room, z.room_temp === null ? "–" : `${pF(z.room_temp, 1)} °C`)}
          ${fact("Heat loss", H.loss, `${pF(z.heat_loss_w_per_c, 0)} W/°C`, chg(z.heat_loss_w_per_c, seed.heat_loss_w_per_c))}
          ${fact("Heat capacity", H.cap, `${pF(z.heat_capacity_kwh_per_c, 2)} kWh/°C`, chg(z.heat_capacity_kwh_per_c, seed.heat_capacity_kwh_per_c))}
          ${fact("Time constant", H.tau, `${pF(z.time_constant_h, 0)} h`, `start ${pF(seed.time_constant_h, 0)} h`)}
          ${fact("To hold 21 °C", H.hold, `${pF(z.power_21_at_0_kw, 2)} kW`, `at 0 °C · ${pF(z.power_21_at_minus10_kw, 2)} kW at −10 °C`)}
          ${fact("Without heat, 2 h", H.cool, `−${pF(z.cooling_2h_c, 1)} °C`, "from 21 °C at 0 °C outside")}
          ${fact("Prediction error", H.err, z.err_ema_c === null ? "–" : `${pF(z.err_ema_c, 2)} °C`, "typical per 15-min step")}
          ${fact("Learned from", H.samples, `${z.samples} steps`, `${z.skipped} skipped${z.last_update ? ` · last ${pDay(new Date(z.last_update))} ${pHH(new Date(z.last_update))}` : ""}`)}
        </div>
        <div class="chartw"><svg id="svg-tz-${zi}" role="img" aria-label="Learning history"></svg></div>
        <div class="legend static"><span class="lg static"><span class="sw" style="background:#0e7490"></span>Heat loss W/°C</span><span class="lg static"><span class="sw" style="background:#a8a29e"></span>Time constant h</span><span class="hint" style="align-self:center">last 7 days, one point per hour</span></div>
        <details class="tzm"><summary>Adjust manually / reset</summary>
          <div class="hint" style="margin:6px 0 10px">Manual values replace the learned ones and pause learning (you can resume learning later). Reset goes back to the start values from area and insulation and starts learning again.</div>
          <div class="tzr">
            <label class="sf"><span>Heat capacity${this._help(H.cap)}</span><span class="sc2"><input type="number" step="0.05" min="0.05" max="100" data-f="cap" value="${pF(z.heat_capacity_kwh_per_c, 2)}"><span class="u">kWh/°C</span></span></label>
            <label class="sf"><span>Heat loss${this._help(H.loss)}</span><span class="sc2"><input type="number" step="1" min="1" max="2000" data-f="loss" value="${pF(z.heat_loss_w_per_c, 0)}"><span class="u">W/°C</span></span></label>
          </div>
          <div class="tzb"><button type="button" class="btn pri" data-a="set">Apply manual values</button>
            <button type="button" class="btn" data-a="${z.frozen ? "unfreeze" : "freeze"}">${z.frozen ? "Resume learning" : "Pause learning"}</button>
            <button type="button" class="btn" data-a="reset">Reset to start values</button></div>
        </details></div>`;
    }).join("");
    zones.forEach((z, zi) => {
      const hist = z.history || [], svg = this.shadowRoot.getElementById(`svg-tz-${zi}`);
      if (!svg) return;
      const pts = (k) => hist.map((h) => [new Date(h[0]).getTime(), pN(h[k])]);
      const t1 = Date.now(), t0 = t1 - 7 * 86400e3;
      if (hist.length < 2) {
        svg.style.display = "none";
        if (!svg.nextElementSibling?.classList?.contains("nohist")) svg.insertAdjacentHTML("afterend", `<div class="hint nohist">Not enough history yet – the model learns every 15 minutes while the window is closed and there is something to learn from (heating, or a clear indoor–outdoor difference).</div>`);
        return;
      }
      pcTsChart(svg, this._w(`svg-tz-${zi}`), { t0, t1, height: 150, yLabel: "W/°C", yrLabel: "h", tickEveryMs: 86400e3,
        series: [{ name: "loss", color: "#0e7490", points: pts(1), width: 1.6 }, { name: "tau", color: "#a8a29e", axis: "r", points: pts(2), width: 1.2 }] });
    });
    box.querySelectorAll(".tz").forEach((card) => {
      const zid = card.dataset.zone;
      card.querySelectorAll("[data-a]").forEach((b) => b.addEventListener("click", () => {
        const a = b.dataset.a, data = { zone_id: zid, action: a };
        if (a === "reset" && !confirm("Reset the learned model for this zone to the start values?")) return;
        if (a === "set") {
          const cap = Number(card.querySelector("[data-f=cap]").value), loss = Number(card.querySelector("[data-f=loss]").value);
          if (!(cap > 0) || !(loss > 0)) { this._say("Enter positive values"); return; }
          Object.assign(data, { heat_capacity_kwh_per_c: cap, heat_loss_w_per_c: loss, freeze: true });
        }
        this._call("power_controller", "thermal_model", data);
        this._say(`Thermal model ${zid}: ${a}`);
      }));
    });
    this._bindHelp(box);
  }

  // ------------------------------------------------------------------ PLAN
  _renderPlan(d) {
    if (!d) return;
    const r = this.shadowRoot, n = Math.min(this._horizon, 72 - d.plan.index_now);
    const lg = r.getElementById("lg-pl");
    lg.innerHTML = `<span class="lg static"><span class="sw" style="background:#94a3b8"></span>Baseload</span>`
      + this._stackOrder(d).map((x) => `<button type="button" class="lg" data-id="${x.entry_id}" aria-pressed="${!this._hidden[x.entry_id]}"><span class="sw" style="background:${this._color(x.i)};opacity:${x.forecast_only ? 0.45 : 1}"></span>${x.forecast_only ? `${pE(x.name)} · forecast` : `#${x.plan_priority} ${pE(x.name)}`}</button>`).join("")
      + `<span class="lg static"><span class="sw ln" style="border-color:var(--primary-text-color)"></span>Price</span><span class="hint" style="align-self:center">Stacked bottom-up: baseload, not controlled, then priority 1, 2, …</span>`;
    lg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { this._hidden[b.dataset.id] = !this._hidden[b.dataset.id]; this._renderPlan(this._data); }));
    const s = this._planSeries(d, n);
    const g = pcBarChart(r.getElementById("svg-pl"), this._w("cw-pl"), { view: this._zoom["svg-pl"], n, times: s.times, bars: s.bars, height: this._fsH("svg-pl", 290), yLabel: "kWh/h", yrLabel: "NOK/kWh",
      cap: { values: s.times.map((_, t) => s.at(d.plan.hour_cap_72h, t)), label: "hour allowance" },
      lines: [{ name: "price", color: "var(--primary-text-color)", values: s.times.map((_, t) => s.at(d.plan.price_72h, t)), axis: "r", step: true }] });
    this._geo.pl = { ...g, html: this._planTip(d, s) };
    this._renderOccupancy(d);
    this._renderPva(d);
  }

  _renderOccupancy(d) {
    const r = this.shadowRoot, labels = d.occupancy_labels || {}, sel = d.occupancy_selected, ent = d.entities?.occupancy_select;
    const away = d.mode === "Away";
    r.getElementById("occsel").innerHTML = `<div class="occsel"><div class="hint">Selector (now)${away ? " · <b>ignored while Mode is Away</b>" : ""}</div>
      <div class="seg wrapseg" role="group" aria-label="Occupancy now">${Object.entries(labels).map(([key, lab]) =>
        `<button type="button" data-occ="${pE(lab)}" aria-pressed="${sel === key}" ${ent ? "" : "disabled"}><i class="sw" style="background:${PCP_OCC[key][1]}"></i> ${pE(lab)}</button>`).join("")}</div>
      <div class="hint">Schedule says <b>${pE((PCP_OCC[d.occupancy_scheduled] || ["–"])[0])}</b> · effective now <b>${pE((PCP_OCC[d.occupancy] || ["–"])[0])}</b></div></div>`;
    r.querySelectorAll("#occsel [data-occ]").forEach((b) => b.addEventListener("click", () => {
      this._call("select", "select_option", { entity_id: ent, option: b.dataset.occ }); this._say(`Occupancy set to ${b.dataset.occ}. Replanning.`);
    }));
    const nowD = new Date(d.now), nowMin = nowD.getHours() * 60 + nowD.getMinutes();
    r.getElementById("occ").innerHTML = d.occupancy_days.map((day, i) => {
      const dt = new Date(`${day.date}T12:00:00`);
      const lbl = `${i === 0 ? "Today" : i === 1 ? "Tomorrow" : dt.toLocaleDateString(undefined, { weekday: "long" })} · ${dt.toLocaleDateString(undefined, { day: "numeric", month: "short" })}`;
      const seg = day.quarters.map((q) => `<i style="background:${(PCP_OCC[q] || ["", "#888"])[1]}"></i>`).join("");
      const tog = !day.workday ? `<span class="hint">Day off</span>` : day.switch
        ? `<button type="button" class="tg" role="switch" data-sw="${pE(day.switch)}" aria-checked="${day.home_office}">Home office<span class="k"></span></button>`
        : `<span class="hint">${day.home_office ? "Home office" : "Workday"}</span>`;
      return `<div class="occ"><div class="t"><b>${lbl}</b>${tog}</div><div class="band">${seg}${i === 0 ? `<span class="now" style="left:${nowMin / 1440 * 100}%"></span>` : ""}</div>${i === 6 || i === 0 ? `<div class="ticks"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>` : ""}</div>`;
    }).join("");
    r.querySelectorAll("#occ [data-sw]").forEach((b) => b.addEventListener("click", () => {
      b.setAttribute("aria-checked", b.getAttribute("aria-checked") !== "true");
      this._call("switch", "toggle", { entity_id: b.dataset.sw }); this._say("Home office changed. Replanning.");
    }));
  }

  _renderPva(d) {
    const r = this.shadowRoot, h = (d.plan_history || []).slice(-168);
    const times = h.map((x) => new Date(x.ts));
    if (!h.length) {
      r.getElementById("svg-pva").innerHTML = ""; r.getElementById("pva").textContent = "no finished hours recorded yet";
      return;
    }
    const act = h.map((x) => x.actual_kwh), pl = h.map((x) => x.planned_kwh);
    const g = pcBarChart(r.getElementById("svg-pva"), this._w("cw-pva"), { view: this._zoom["svg-pva"], n: h.length, times, height: this._fsH("svg-pva", 230), yLabel: "kWh/h",
      bars: [{ id: "a", name: "Measured", color: "#64748b", values: act }],
      lines: [{ name: "Planned", color: "var(--warning-color)", values: pl, step: true, width: 1.4 }] });
    this._geo.pva = { ...g, html: (t) => `<b>${pDay(times[t])} ${pHH(times[t])}</b><div class="r"><span>Measured</span><b class="num">${pF(act[t])} kWh</b></div><div class="r"><span>Planned</span><span class="num">${pF(pl[t])} kWh</span></div>` };
    const sa = act.reduce((a, b) => a + b, 0), sp = pl.reduce((a, b) => a + b, 0);
    const mae = act.reduce((a, v, i) => a + Math.abs(v - pl[i]), 0) / act.length;
    r.getElementById("pva").textContent = `${h.length} h · measured ${sa.toFixed(0)} kWh vs planned ${sp.toFixed(0)} kWh · mean error ${mae.toFixed(2)} kWh/h`;
  }

  // ------------------------------------------------------------------ DISTRIBUTION (live flow)
  _renderDistribution(d) {
    const r = this.shadowRoot, f = d.flow || {};
    this._renderFlow(d, f);
    // phases
    let worst = "ok";
    r.getElementById("ph").innerHTML = ["L1", "L2", "L3"].map((ph) => {
      const p = d.phases[ph] || {}; const rated = pN(p.rated_a) || 1; const pc = (pN(p.amps) ?? 0) / rated * 100;
      if (p.state === "limiting") worst = "limiting"; else if (p.state === "accepting" && worst === "ok") worst = "accepting";
      const col = pc > 100 ? "var(--error-color)" : pc > 85 ? "var(--warning-color)" : "var(--primary-color)";
      return `<div class="ph"><div class="t"><span><b>${ph}</b> <span class="hint">· ${pE(p.state || "ok")} · budget ${pF(p.budget_pct, 0)} %</span></span><span class="num hint">${pF(p.amps, 1)} / ${pF(rated, 0)} A</span></div><div class="bar" style="height:10px"><i style="width:${Math.min(100, pc)}%;background:${col}"></i></div></div>`;
    }).join("");
    r.getElementById("phchip").innerHTML = worst === "ok" ? `<span class="chip c-ok">All phases OK</span>` : worst === "accepting" ? `<span class="chip c-warn">Accepting overload</span>` : `<span class="chip c-bad">Limiting</span>`;
    // decisions
    r.getElementById("dtab").innerHTML = `<div class="dt h"><span>Device · reason</span><span>Status</span><span class="num">Grant kW</span><span class="num">Live kW</span><span class="num" title="Part of the grant this device does not use and lends to others">Lent out kW</span></div>` +
      d.devices.map((x, i) => {
        const st = PCP_STATUS[x.committed ? x.ui_status : "manual"] || PCP_STATUS.waiting;
        const lent = pN(x.lent_kw) ?? 0;
        const why = x.committed ? ({ blocked: "planned, no capacity left", "not planned": "not planned this hour" }[x.dist_reason] || (x.ui_status === "lent" ? "reserved but idle → lent" : lent > 0 ? "unused part lent to others" : x.ui_status === "priority_now" ? "priority now" : "has planned power")) : (x.has_power_sensor === false ? "not controlled – no power sensor, forecast used" : "not controlled – measured load");
        return `<div class="dt"><span><i class="sw" style="background:${this._color(i)}"></i> ${pE(x.name)}<small>${why} · #${x.dist_priority}</small></span><span><span class="chip c-${st[1]}">${st[0]}</span></span>
          <span class="num">${x.committed ? pF(x.allocated_kw) : "–"}</span><span class="num">${x.committed || x.has_power_sensor !== false ? pF(x.live_kw) : "n/a"}</span><span class="num${lent > 0 ? " lentv" : ""}">${lent > 0 ? pF(lent) : "–"}</span></div>`;
      }).join("");
  }

  _renderFlow(d, f) {
    const r = this.shadowRoot, svg = r.getElementById("svg-flow"), W = Math.max(300, this._w("cw-flow")), wide = W >= 760;
    const tl = f.tariff_limiter || {}, gl = f.grid_limiter || {}, dist = f.distribution || {}, oa = f.overload || {};
    const rated = gl.rated_a || {};
    const oaWorst = ["L1", "L2", "L3"].some((ph) => oa[ph]?.state === "limiting") ? "var(--error-color)"
      : ["L1", "L2", "L3"].some((ph) => oa[ph]?.state === "accepting") ? "var(--warning-color)" : "#7c6f9b";
    const chain = [
      { key: "grid", title: "Grid", lines: [`${pF(f.grid_kw)} kW`, `L1 ${pF(f.phase_a?.L1, 1)} · L2 ${pF(f.phase_a?.L2, 1)} · L3 ${pF(f.phase_a?.L3, 1)} A`], c: "#64748b" },
      { key: "gl", title: "Grid limiter", lines: [`${pF(rated.L1, 0)} A per phase`, `internal ${pF(gl.internal_a, 0)} · external ${gl.external_a ? pF(gl.external_a, 0) : "–"} A`, `binding: ${gl.binding || "internal"}`], c: "#7c6f9b" },
      { key: "tl", title: "Tariff limiter", lines: [`${pF(tl.energy_this_hour_kwh)} / ${pF(tl.hour_allowance_kwh)} kWh this hour`, `limit now ${pF(tl.limit_now_kw)} kW`, tl.safe_mode ? (d.kpi.safe_mode_sensor_ok ? "SAFE MODE until the end of the hour" : "SAFE MODE (grid sensor stale)") : ""], c: tl.safe_mode ? "var(--error-color)" : "#7c6f9b" },
      { key: "oa", title: "Overload acceptance", lines: ["L1", "L2", "L3"].map((ph) => `${ph} ${oa[ph]?.state || "ok"} · ${pF(oa[ph]?.budget_pct, 0)} %`), c: oaWorst },
      { key: "dist", title: "Distribution", lines: [`budget ${pF(dist.budget_kw)} kW`, `reserved ${pF(dist.reserved_kw)} · lent ${pF(dist.lent_kw)} kW`, `unused ${pF(dist.unused_kw)} kW`], c: "#5f7f6a" },
    ];
    const side = [
      { key: "base", title: "Baseload", lines: [`${pF(f.baseload_kw)} kW`, "not controllable"], c: "#94a3b8" },
      { key: "unc", title: "Not controlled", lines: [`${pF(f.uncommitted_kw)} kW`, "uncommitted devices"], c: "#94a3b8" },
    ];
    const devs = d.devices.map((x, i) => ({ ...x, i }));
    const boxes = {}; let s = "", H;
    if (wide) {
      // LEFT HALF: the limiter chain top-down, consumers that are subtracted first beside the grid.
      // RIGHT HALF: one row per device. Lines only cross the gap between the halves.
      // LEFT HALF: limiter chain. RIGHT HALF: baseload + not controlled at the top (fed directly
      // from the grid), the controlled devices below them – no box covers an arrow.
      const half = W / 2, pad = 10, gapX = 70, colW = Math.min(300, half - gapX / 2 - pad - 20);
      const cx = pad, bh = 70, vgap = 22;
      chain.forEach((b, i) => { boxes[b.key] = { x: cx, y: 8 + i * (bh + vgap), w: colW, h: bh, ...b }; });
      const dx = half + gapX / 2, dw = W - dx - 120, dh = 58, dgap = 14;
      side.forEach((b, i) => { boxes[b.key] = { x: dx, y: 8 + i * (54 + 10), w: dw, h: 54, ...b }; });
      const sideH = 8 + side.length * 64 + 18;
      const chainH = 8 + chain.length * (bh + vgap);
      const devs1 = devs.filter((x) => x.committed), devs0 = devs.filter((x) => !x.committed);
      const y0 = Math.max(sideH, boxes.dist.y + boxes.dist.h / 2 - (devs1.length * (dh + dgap)) / 2);
      devs1.forEach((x, i) => { boxes[x.entry_id] = { x: dx, y: y0 + i * (dh + dgap), w: dw, h: dh }; });
      let yu = y0 + devs1.length * (dh + dgap) + (devs0.length ? 18 : 0);
      if (devs0.length) s += `<text x="${dx}" y="${yu - 6}" class="tx">Not controlled (counted as load)</text>`;
      devs0.forEach((x, i) => { boxes[x.entry_id] = { x: dx, y: yu + i * (dh + dgap), w: dw, h: dh }; });
      H = Math.max(chainH, yu + devs0.length * (dh + dgap)) + 10;
      for (let i = 0; i < chain.length - 1; i++) {
        const a = boxes[chain[i].key], b = boxes[chain[i + 1].key];
        s += `<line x1="${a.x + a.w / 2}" y1="${a.y + a.h}" x2="${b.x + b.w / 2}" y2="${b.y - 3}" class="fl" marker-end="url(#ah)"/>`;
      }
      const g = boxes.grid;
      side.forEach((sb) => { const b = boxes[sb.key], y1 = g.y + g.h / 2, y2 = b.y + b.h / 2, m = (g.x + g.w + b.x) / 2;
        s += `<path d="M${g.x + g.w},${y1} C${m},${y1} ${m},${y2} ${b.x - 4},${y2}" fill="none" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="4 3" marker-end="url(#ah)"/>`; });
      // distribution -> devices: leave the right edge of the box, fan out in the gap
      const db = boxes.dist, sx = db.x + db.w, sy = db.y + db.h / 2, mid = sx + (dx - sx) * 0.55;
      devs.filter((x) => x.committed).forEach((x) => {
        const b = boxes[x.entry_id], ty = b.y + b.h / 2, kw = pN(x.allocated_kw) ?? 0;
        const st = PCP_STATUS[x.committed ? x.ui_status : "manual"] || PCP_STATUS.waiting;
        const col = x.committed ? { ok: "var(--success-color)", info: "var(--info-color, var(--primary-color))", warn: "var(--warning-color)", bad: "var(--error-color)", mut: "var(--divider-color)" }[st[1]] : "var(--divider-color)";
        s += `<path d="M${sx},${sy} C${mid},${sy} ${mid},${ty} ${b.x - 4},${ty}" fill="none" stroke="${col}" stroke-width="${1 + Math.min(8, kw * 1.4)}" ${kw > 0 && x.committed ? "" : 'stroke-dasharray="3 3"'} marker-end="url(#ah)" opacity=".9"/>`;
        if (kw > 0) s += `<text x="${b.x - 8}" y="${ty - 6}" text-anchor="end" class="tx b">${pF(kw, 1)} kW</text>`;
      });
      // lending device -> device: arcs on the free right side of the device column
      const ex = dx + dw;
      for (const e of f.edges || []) {
        if (!e.from) continue;
        const a = boxes[e.from], b = boxes[e.to]; if (!a || !b) continue;
        const y1 = a.y + a.h / 2, y2 = b.y + b.h / 2, bulge = ex + 30 + Math.min(40, Math.abs(y2 - y1) / 4);
        s += `<path d="M${ex},${y1} C${bulge},${y1} ${bulge},${y2} ${ex + 4},${y2}" fill="none" stroke="var(--info-color, var(--primary-color))" stroke-width="2" stroke-dasharray="5 4" marker-end="url(#ahl)"/>`;
        s += `<text x="${bulge + 4}" y="${(y1 + y2) / 2 + 4}" class="tx b">lends ${pF(e.kw, 1)} kW</text>`;
      }
    } else {
      const bw = W - 20; let y = 8;
      chain.forEach((b) => { boxes[b.key] = { x: 10, y, w: bw, h: 78, ...b }; y += 78 + 22; if (b.key === "grid") { side.forEach((sb, j) => { boxes[sb.key] = { x: 10 + j * (bw / 2 + 4), y, w: bw / 2 - 4, h: 62, ...sb }; }); y += 62 + 22; } });
      devs.forEach((x) => { boxes[x.entry_id] = { x: 40, y, w: bw - 64, h: 70 }; y += 70 + 14; });
      H = y + 6;
      for (let i = 0; i < chain.length - 1; i++) {
        const a = boxes[chain[i].key], b = boxes[chain[i + 1].key];
        s += `<line x1="${a.x + a.w / 2}" y1="${a.y + a.h + (chain[i].key === "grid" ? 62 + 44 : 0)}" x2="${b.x + b.w / 2}" y2="${b.y - 2}" class="fl" marker-end="url(#ah)"/>`;
      }
      const db = boxes.dist;
      if (devs.length) { const last = boxes[devs[devs.length - 1].entry_id]; s += `<line x1="24" y1="${db.y + db.h}" x2="24" y2="${last.y + last.h / 2}" class="fl"/>`; }
      devs.forEach((x) => {
        const b = boxes[x.entry_id], kw = pN(x.allocated_kw) ?? 0;
        s += `<line x1="24" y1="${b.y + b.h / 2}" x2="${b.x - 2}" y2="${b.y + b.h / 2}" stroke="var(--secondary-text-color)" stroke-width="${1 + Math.min(6, kw)}" marker-end="url(#ah)"/>`;
      });
      for (const e of f.edges || []) {
        if (!e.from) continue;
        const a = boxes[e.from], b = boxes[e.to]; if (!a || !b) continue;
        const x1 = a.x + a.w, y1 = a.y + a.h / 2, y2 = b.y + b.h / 2, cx2 = x1 + 18;
        s += `<path d="M${x1},${y1} C${cx2},${y1} ${cx2},${y2} ${x1 + 2},${y2}" fill="none" stroke="var(--info-color, var(--primary-color))" stroke-width="2" stroke-dasharray="5 4" marker-end="url(#ahl)"/>`;
      }
    }
    // boxes last, so no line is drawn on top of a box
    const fit = (t, w, px) => { const n = Math.max(4, Math.floor((w - 20) / px)); t = String(t || ""); return t.length > n ? `${t.slice(0, n - 1)}…` : t; };
    const boxSvg = (b, title, lines, color) => `<g><rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="10" class="bx"/><rect x="${b.x}" y="${b.y}" width="4" height="${b.h}" rx="2" fill="${color}"/>
      <text x="${b.x + 12}" y="${b.y + 18}" class="bt"><title>${pE(title)}</title>${pE(fit(title, b.w, 7.6))}</text>${lines.filter(Boolean).map((l, i) => `<text x="${b.x + 12}" y="${b.y + 35 + i * 14}" class="bl">${pE(fit(l, b.w, 6.4))}</text>`).join("")}</g>`;
    let bx = "";
    for (const b of [...chain, ...side]) bx += boxSvg(boxes[b.key], b.title, b.lines, b.c);
    devs.forEach((x) => {
      const b = boxes[x.entry_id], st = PCP_STATUS[x.committed ? x.ui_status : "manual"] || PCP_STATUS.waiting;
      bx += boxSvg(b, x.name, [`${st[0]} · ${pLive(x)}`, x.committed ? `#${x.dist_priority}${pN(x.lent_kw) > 0 ? ` · lends ${pF(x.lent_kw, 1)} kW` : ""}` : "counted as load"], this._color(x.i));
    });
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("height", H);
    svg.innerHTML = `<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" style="fill:var(--secondary-text-color)"/></marker>
      <marker id="ahl" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" style="fill:var(--info-color, var(--primary-color))"/></marker></defs>${s}${bx}`;
    r.getElementById("flowt").textContent = `updated ${pHH(new Date(d.now))}${d.urgent ? " · urgent: reductions sent at once" : ""}`;
  }

  _renderCards(d) {
    const box = this.shadowRoot.getElementById("cards");
    if (!customElements.get("power-controller-object-card") && !this._cardImport) {
      // same URL as the globally injected card -> module cache, executed once
      this._cardImport = import(`./power-controller-object-card.js?v=${PCP_VERSION}`).then(() => this._renderCards(this._data)).catch(() => {});
    }
    const ids = d.devices.map((x) => x.entity_id || x.entities?.plan).filter(Boolean);
    if (!customElements.get("power-controller-object-card")) {
      box.innerHTML = `<div class="hint">The object card script is not loaded (reload the browser).</div>`; return;
    }
    const key = ids.join("|");
    if (this._cardKey !== key) {
      box.innerHTML = ""; this._cards = {};
      d.devices.forEach((x) => {
        const ent = x.entities?.plan; if (!ent) return;
        const c = document.createElement("power-controller-object-card");
        c.setConfig({ entity: ent }); c.hass = this._hass; this._cards[x.entry_id] = c; box.appendChild(c);
      });
      this._cardKey = key;
    }
    if (!ids.length) box.innerHTML = `<div class="hint">No devices yet.</div>`;
  }

  // ------------------------------------------------------------------ priorities
  _prioKey() { return this._ptype === "plan" ? "plan_priority" : "dist_priority"; }
  _loadOrder() {
    const k = this._prioKey();
    this._order = (this._data?.devices || []).slice().sort((a, b) => a[k] - b[k]).map((x) => x.entry_id);
  }
  _dirty() {
    const k = this._prioKey(), by = Object.fromEntries((this._data?.devices || []).map((x) => [x.entry_id, x]));
    return (this._order || []).some((id, i) => by[id] && by[id][k] !== i + 1);
  }
  _renderPrio() {
    const r = this.shadowRoot, d = this._data;
    if (!d) return;
    if (!this._order || this._order.length !== d.devices.length) this._loadOrder();
    const k = this._prioKey(), by = Object.fromEntries(d.devices.map((x, i) => [x.entry_id, { ...x, i }]));
    r.getElementById("prh").textContent = this._ptype === "plan" ? "Planning: whose comfort/energy need weighs most in the 72-hour plan" : "Distribution: who gets power first in real time when capacity is short";
    r.getElementById("pol").innerHTML = this._order.map((id, i) => {
      const x = by[id]; const st = PCP_STATUS[x.committed ? x.ui_status : "manual"] || PCP_STATUS.waiting;
      return `<li class="pr${x[k] !== i + 1 ? " moved" : ""}" draggable="true" data-id="${id}"><span class="hdl" aria-hidden="true">⠿</span><span class="prio">${i + 1}</span>
        <i class="sw" style="background:${this._color(x.i)}"></i><span class="pn2">${pE(x.name)}<small>${PCP_TYPE[x.device_type] || ""}</small></span><span class="chip c-${st[1]} x">${st[0]}</span>
        <button type="button" class="ab" data-mv="-1" aria-label="Move ${pE(x.name)} up" ${i === 0 ? "disabled" : ""}>↑</button><button type="button" class="ab" data-mv="1" aria-label="Move ${pE(x.name)} down" ${i === this._order.length - 1 ? "disabled" : ""}>↓</button></li>`;
    }).join("");
    const dirty = this._dirty();
    r.getElementById("apply").disabled = !dirty; r.getElementById("reset").disabled = !dirty;
    const ch = r.getElementById("dirty"); ch.textContent = dirty ? "Unsaved changes" : "No changes"; ch.className = `chip ${dirty ? "c-warn" : "c-mut"}`;
    r.getElementById("yaml").textContent = `action: power_controller.set_priorities\ndata:\n  target: ${this._target()}\n  ordered_entry_ids:\n${this._order.map((id) => `    - ${id}  # ${by[id].name}`).join("\n")}`;
  }
  _target() { return this._both ? "both" : this._ptype === "plan" ? "plan" : "distribution"; }
  _bindPrioList() {
    const r = this.shadowRoot, pl = r.getElementById("pol");
    pl.addEventListener("click", (e) => {
      const b = e.target.closest("[data-mv]"); if (!b) return;
      const i = this._order.indexOf(b.closest("li").dataset.id), j = i + Number(b.dataset.mv);
      if (j < 0 || j >= this._order.length) return;
      [this._order[i], this._order[j]] = [this._order[j], this._order[i]]; this._renderPrio();
      (r.querySelector(`#pol li:nth-child(${j + 1}) [data-mv="${b.dataset.mv}"]`) || r.querySelector(`#pol li:nth-child(${j + 1}) [data-mv]`))?.focus();
    });
    let dragId = null;
    pl.addEventListener("dragstart", (e) => { const li = e.target.closest("li"); if (!li) return; dragId = li.dataset.id; li.classList.add("drag"); e.dataTransfer.effectAllowed = "move"; try { e.dataTransfer.setData("text/plain", dragId); } catch (_) { /* */ } });
    pl.addEventListener("dragover", (e) => {
      if (!dragId) return; e.preventDefault();
      const li = e.target.closest("li"); if (!li || li.dataset.id === dragId) return;
      const from = this._order.indexOf(dragId), to = this._order.indexOf(li.dataset.id);
      this._order.splice(from, 1); this._order.splice(to, 0, dragId); this._renderPrio();
      r.querySelector(`#pol li[data-id="${dragId}"]`)?.classList.add("drag");
    });
    pl.addEventListener("dragend", () => { dragId = null; this._renderPrio(); });
  }
  async _applyPrio() {
    const r = this.shadowRoot;
    await this._call("power_controller", "set_priorities", { ordered_entry_ids: this._order, target: this._target() });
    r.getElementById("toast").textContent = `✓ Priorities applied (${this._target()}) – the plan is being recalculated.`;
    (this._data?.devices || []).forEach((x) => {
      const i = this._order.indexOf(x.entry_id) + 1;
      if (this._both || this._ptype === "plan") x.plan_priority = i;
      if (this._both || this._ptype === "dist") x.dist_priority = i;
    });
    this._renderPrio();
  }


  _css() {
    return `<style>
    :host{display:block;min-height:100%;background:var(--primary-background-color);color:var(--primary-text-color);font-family:var(--ha-font-family-body, Roboto, system-ui, sans-serif);
      --pc-soft:color-mix(in srgb, var(--primary-text-color) 6%, transparent);--pc-r:var(--ha-card-border-radius, 12px)}
    *{box-sizing:border-box}
    .tbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;justify-content:space-between;padding:10px 18px;background:var(--app-header-background-color, var(--primary-background-color));color:var(--app-header-text-color, var(--primary-text-color));border-bottom:var(--app-header-border-bottom, 1px solid var(--divider-color))}
    .brand{display:flex;gap:12px;align-items:center;min-width:0;flex:1}
    .logo{width:38px;height:38px;border-radius:calc(var(--pc-r) * .8);background:var(--primary-color);color:var(--text-primary-color, #fff);display:grid;place-items:center;flex:none}
    h1{font-size:18px;margin:0;font-weight:700}.sub{font-size:12.5px;opacity:.75;margin-top:2px}
    .wrap{padding:16px 18px 24px;max-width:1500px;margin:0 auto}
    .bar2{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:14px}
    .seg{display:inline-flex;background:var(--pc-soft);border-radius:10px;padding:3px;border:1px solid var(--divider-color);flex-wrap:wrap}
    .seg button{border:0;background:transparent;color:var(--secondary-text-color);font:inherit;font-size:12.5px;padding:6px 12px;border-radius:8px;cursor:pointer}
    .seg button[aria-pressed=true],.seg button[aria-selected=true]{background:var(--card-background-color);color:var(--primary-text-color);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.15)}
    button:focus-visible,[tabindex]:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}
    .pills{display:flex;flex-wrap:wrap;gap:8px}
    .pill{font-size:12px;padding:6px 10px;border-radius:999px;background:var(--card-background-color);border:1px solid var(--divider-color);color:var(--secondary-text-color);white-space:nowrap}
    .pill b{color:var(--primary-text-color);font-weight:600}
    .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:middle}
    .card{background:var(--ha-card-background, var(--card-background-color));border:var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color));border-radius:var(--pc-r);box-shadow:var(--ha-card-box-shadow, none);padding:16px;min-width:0}
    .mb{margin-bottom:14px}
    .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px;margin-bottom:14px}
    .k-l{font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--secondary-text-color);font-weight:600}
    .k-v{font-size:26px;font-weight:700;margin-top:6px;letter-spacing:-.02em}
    .k-v small{font-size:13px;color:var(--secondary-text-color);font-weight:500;margin-left:3px}
    .k-s{font-size:12px;color:var(--secondary-text-color);margin-top:4px}
    .bar{height:6px;border-radius:6px;background:var(--pc-soft);margin-top:10px;overflow:hidden}.bar i{display:block;height:100%;border-radius:6px}
    .pos{color:var(--success-color)}.neg{color:var(--error-color)}
    .row{display:grid;grid-template-columns:minmax(0,2.2fr) minmax(0,1fr);gap:14px;margin-bottom:14px}
    .row3{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:14px;margin-bottom:14px}
    .row.half{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
    @media (max-width:900px){.row,.row.half{grid-template-columns:minmax(0,1fr)}}
    .lg.tot{font-weight:600}
    .safe{border:1px solid var(--error-color);background:color-mix(in srgb, var(--error-color) 12%, transparent);color:var(--primary-text-color);border-radius:var(--pc-r);padding:10px 14px;margin-bottom:12px;font-size:13px}
    .safe b{color:var(--error-color)}
    .pill.bad{border-color:var(--error-color);color:var(--error-color)}.pill.bad b{color:var(--error-color)}
    .pill.warn{border-color:var(--warning-color)}
    .k-v.warnv{color:var(--warning-color)}
    .hd{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:12px}
    h2{font-size:14.5px;margin:0;font-weight:650}.hint{font-size:12px;color:var(--secondary-text-color)}
    .legend{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
    .lg{display:inline-flex;align-items:center;gap:6px;font-size:12px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);border-radius:999px;padding:4px 10px;cursor:pointer;font-family:inherit}
    .lg[aria-pressed=false]{opacity:.4}.lg.static{cursor:default}
    .sw{width:9px;height:9px;border-radius:3px;display:inline-block}.sw.dash{background:none;border-top:2px dashed var(--secondary-text-color);height:0;width:14px;border-radius:0}
    .chartw{position:relative;width:100%}svg{display:block;width:100%}
    .tip{position:absolute;pointer-events:none;background:var(--card-background-color);border:1px solid var(--divider-color);box-shadow:0 6px 20px rgba(0,0,0,.18);border-radius:10px;padding:10px 12px;font-size:12px;min-width:180px;display:none;z-index:2}
    .tip .r{display:flex;justify-content:space-between;gap:12px;margin-top:3px}.tip .sep{border-top:1px solid var(--divider-color);margin:6px 0}
    .ph{margin-bottom:14px}.ph .t{display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px;gap:8px}
    .chip{font-size:11px;font-weight:600;padding:3px 8px;border-radius:999px;display:inline-block;white-space:nowrap}
    .c-ok{background:color-mix(in srgb, var(--success-color) 15%, transparent);color:var(--success-color)}
    .c-warn{background:color-mix(in srgb, var(--warning-color) 16%, transparent);color:var(--warning-color)}
    .c-info{background:color-mix(in srgb, var(--info-color, var(--primary-color)) 15%, transparent);color:var(--info-color, var(--primary-color))}
    .c-bad{background:color-mix(in srgb, var(--error-color) 15%, transparent);color:var(--error-color)}
    .c-mut{background:var(--pc-soft);color:var(--secondary-text-color)}
    .dev{display:grid;grid-template-columns:minmax(150px,1.4fr) minmax(100px,.8fr) minmax(70px,.6fr) minmax(100px,.8fr) minmax(140px,1.4fr) 40px;gap:10px;align-items:center;padding:10px 8px;border-top:1px solid var(--divider-color);font-size:13px;cursor:pointer;border-radius:8px}
    .dev:hover{background:var(--pc-soft)}.dev.sel{background:var(--pc-soft);box-shadow:inset 3px 0 0 var(--primary-color)}
    .dev.h{cursor:default;background:transparent;box-shadow:none;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--secondary-text-color);font-weight:600;border-top:0}
    .dn{font-weight:600}.dn small,.pn2 small{display:block;font-weight:400;color:var(--secondary-text-color);font-size:11.5px}
    .num{font-variant-numeric:tabular-nums}
    .spark{display:flex;align-items:flex-end;gap:1px;height:26px}.spark i{flex:1;border-radius:1px;min-height:1px}
    .prio{width:26px;height:26px;border-radius:8px;background:var(--pc-soft);display:grid;place-items:center;font-weight:700;font-size:12px;flex:none}
    @media (max-width:760px){.dev{grid-template-columns:minmax(0,1fr) auto}.dev .x{display:none}.dev.h{display:none}}
    .occ{margin-bottom:14px}.occ .t{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;font-size:13px;gap:8px}
    .band{display:flex;height:22px;border-radius:7px;overflow:hidden;position:relative}.band i{flex:1}
    .now{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--primary-text-color);border-radius:2px}
    .ticks{display:flex;justify-content:space-between;font-size:10.5px;color:var(--secondary-text-color);margin-top:4px}
    .tg{display:inline-flex;align-items:center;gap:8px;font-size:12px;color:var(--secondary-text-color);background:none;border:0;cursor:pointer;font-family:inherit;padding:2px}
    .tg .k{width:34px;height:20px;border-radius:999px;background:var(--divider-color);position:relative;transition:background .2s;flex:none}
    .tg .k::after{content:'';position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;transition:left .2s;box-shadow:0 1px 2px rgba(0,0,0,.3)}
    .tg[aria-checked=true] .k{background:var(--primary-color)}.tg[aria-checked=true] .k::after{left:16px}
    .keys{display:flex;flex-wrap:wrap;gap:12px;font-size:11.5px;color:var(--secondary-text-color);margin-top:6px}
    .sc{margin-bottom:12px}.sc .t{display:flex;justify-content:space-between;font-size:13px;margin-bottom:5px}
    .big{font-size:30px;font-weight:750;letter-spacing:-.02em}
    .ladder{display:flex;gap:4px;align-items:flex-end;height:90px;margin-top:8px}
    .ladder div{flex:1;border-radius:6px 6px 0 0;background:var(--divider-color);display:flex;align-items:flex-start;justify-content:center;font-size:10px;color:var(--secondary-text-color);padding-top:4px}
    .ladder div.on{background:var(--primary-color);color:var(--text-primary-color, #fff);font-weight:700}
    .ll{display:flex;gap:4px;font-size:10.5px;color:var(--secondary-text-color);margin-top:4px}.ll span{flex:1;text-align:center}
    .dcs{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(340px,100%),1fr));gap:14px;align-items:start}
    .pl{list-style:none;margin:0;padding:0}
    .pr{display:flex;align-items:center;gap:10px;padding:9px 8px;border-top:1px solid var(--divider-color);border-radius:8px}
    .pr.drag{opacity:.4}.pr.moved .prio{background:var(--primary-color);color:var(--text-primary-color, #fff)}
    .pn2{flex:1;min-width:0;font-weight:600;font-size:13.5px}
    .hdl{cursor:grab;color:var(--secondary-text-color);font-size:16px;user-select:none}
    @media (max-width:560px){.pr .x{display:none}}
    .ab{width:30px;height:30px;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);cursor:pointer;font:inherit}
    .ab:disabled{opacity:.35;cursor:default}
    .btn{font:inherit;font-size:13px;font-weight:600;border-radius:9px;padding:8px 14px;cursor:pointer;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color)}
    .btn.pri{background:var(--primary-color);border-color:var(--primary-color);color:var(--text-primary-color, #fff)}.btn:disabled{opacity:.45;cursor:default}
    pre{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;background:var(--pc-soft);border-radius:8px;padding:10px;margin:0;white-space:pre-wrap;word-break:break-word}
    .toast{font-size:12.5px;color:var(--success-color);min-height:18px;margin-top:8px}
    .foot{font-size:11.5px;color:var(--secondary-text-color);text-align:center;margin-top:8px}
    .sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
    .sec{display:flex;align-items:baseline;gap:10px;margin:18px 2px 10px;font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--secondary-text-color);border-bottom:1px solid var(--divider-color);padding-bottom:6px}
    .sec span{font-weight:700;color:var(--primary-text-color)}.sec small{text-transform:none;letter-spacing:0;font-size:12px}
    .vals{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:var(--secondary-text-color);font-variant-numeric:tabular-nums}
    .vals span{display:inline-flex;align-items:center;gap:4px}.vals b{color:var(--primary-text-color)}
    svg .gl{stroke:color-mix(in srgb, var(--primary-text-color) 18%, transparent)}svg .dl{stroke:var(--secondary-text-color);stroke-dasharray:2 3;opacity:.6}
    svg .tx{fill:var(--secondary-text-color);font-size:10.5px}svg .tx.b{font-weight:600}
    svg .past{fill:var(--primary-text-color);opacity:.04}svg .nowl{stroke:var(--primary-color);stroke-width:1.5}svg .nowt{fill:var(--primary-color);font-weight:600}
    svg .cap{fill:none;stroke:var(--error-color);stroke-width:1.4;stroke-dasharray:6 4}svg .capt{fill:var(--error-color)}
    svg .hv{stroke:var(--primary-text-color)}
    svg .bx{fill:var(--card-background-color);stroke:var(--divider-color)}svg .bt{fill:var(--primary-text-color);font-size:13px;font-weight:600}
    svg .bl{fill:var(--secondary-text-color);font-size:11.5px;font-variant-numeric:tabular-nums}svg .fl{stroke:var(--secondary-text-color);stroke-width:1.6}
    .sw.ln{background:none;height:0;width:14px;border-top:2px dashed;border-radius:0}
    .legend.static .lg{cursor:default}
    .spark{position:relative}.spark em{position:absolute;right:0;top:-12px;font-style:normal;font-size:10px;color:var(--secondary-text-color)}
    details.dia{margin-bottom:14px;background:var(--ha-card-background, var(--card-background-color));border:1px solid var(--divider-color);border-radius:var(--pc-r);padding:10px 14px}
    details.dia summary{cursor:pointer;font-weight:600;font-size:13px}details.dia img{display:block;width:100%;max-width:1100px;margin:10px auto 2px;border-radius:10px}
    .big small{font-size:14px;color:var(--secondary-text-color);font-weight:500}
    .gauge{height:14px;border-radius:8px;background:var(--pc-soft);position:relative;overflow:visible;margin-top:10px}
    .gauge i{position:absolute;left:0;top:0;bottom:0;border-radius:8px;background:var(--info-color, var(--primary-color))}
    .gauge em{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--warning-color)}
    .kv{display:grid;grid-template-columns:1fr auto;gap:6px 12px;font-size:13px;margin:12px 0;font-variant-numeric:tabular-nums}.kv span{color:var(--secondary-text-color)}
    .occsel{margin-bottom:12px;display:flex;flex-direction:column;gap:6px}.wrapseg{display:flex;flex-wrap:wrap}
    .wrapseg button{display:inline-flex;align-items:center;gap:6px}.wrapseg button:disabled{opacity:.5;cursor:default}
    .occ .band{height:16px}.occ{margin-bottom:8px}
    .hq{position:relative;display:inline-grid;place-items:center;width:16px;height:16px;margin-left:6px;border-radius:50%;border:1px solid var(--secondary-text-color);color:var(--secondary-text-color);font-size:10.5px;font-weight:700;cursor:help;vertical-align:1px;font-style:normal}
    .hq:focus-visible{outline:2px solid var(--primary-color)}
    .tipbox{display:none;position:absolute;z-index:20;left:-8px;top:22px;width:min(340px,78vw);padding:10px 12px;border-radius:10px;background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);box-shadow:0 8px 24px rgba(0,0,0,.3);font-size:12.5px;font-weight:400;line-height:1.45;text-transform:none;letter-spacing:0;white-space:normal;cursor:auto}
    .hq:hover .tipbox,.hq:focus .tipbox,.hq.open .tipbox{display:block}
    .opth{margin:8px 0 0}.opth dt{font-weight:600;margin-top:6px}.opth dt.cur{color:var(--primary-color)}.opth dt.cur::after{content:" (selected)";font-weight:400}.opth dd{margin:1px 0 0;color:var(--secondary-text-color)}
    .optcur{display:block;font-style:normal;font-size:11.5px;color:var(--secondary-text-color);margin-top:3px;max-width:420px}
    .tzg{grid-template-columns:repeat(auto-fit,minmax(min(460px,100%),1fr))}
    .tfs{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-bottom:10px}
    .tf{background:color-mix(in srgb, var(--primary-text-color) 5%, transparent);border-radius:10px;padding:8px 10px;font-size:12px;color:var(--secondary-text-color)}
    .tf b{display:block;font-size:16px;color:var(--primary-text-color);margin-top:2px;font-variant-numeric:tabular-nums}.tf small{display:block;font-size:11px}
    .tzm{margin-top:8px}.tzm summary{cursor:pointer;font-size:13px;font-weight:600}.tzb{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
    .nrow{display:grid;grid-template-columns:auto auto 1fr auto;gap:8px;align-items:center;padding:7px 0;border-top:1px solid var(--divider-color);font-size:12.5px}
    @media (max-width:640px){.nrow{grid-template-columns:auto 1fr}.nrow .hint{grid-column:1/-1}}
    .lg.bad{border-color:var(--warning-color);color:var(--warning-color)}.warnt{color:var(--warning-color);align-self:center}
    .dt .lentv{color:var(--info-color, var(--primary-color));font-weight:600}
    .dt{display:grid;grid-template-columns:minmax(0,1fr) auto 52px 52px 52px;gap:8px;align-items:center;padding:8px 4px;border-top:1px solid var(--divider-color);font-size:13px}
    .dt small{display:block;color:var(--secondary-text-color);font-size:11.5px}.dt.h{border-top:0;font-size:11px;text-transform:uppercase;color:var(--secondary-text-color);font-weight:600}
    .dt .num{text-align:right;font-variant-numeric:tabular-nums}
    .tabs button{font-size:13px;padding:7px 14px}
    .fsable{position:relative}.fsb{position:absolute;top:10px;right:10px;z-index:3;width:30px;height:30px;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--secondary-text-color);cursor:pointer;display:grid;place-items:center;opacity:.7}
    .fsb:hover{opacity:1}.fsable>.hd{padding-right:38px}
    .card:fullscreen{background:var(--primary-background-color);padding:24px;overflow:auto;border-radius:0}
    .setg{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(360px,100%),1fr));gap:14px}
    .setg h2{margin-bottom:10px}.sfl{display:flex;flex-direction:column}
    .sf{display:flex;justify-content:space-between;align-items:center;gap:6px 12px;padding:8px 0;border-top:1px solid var(--divider-color);font-size:13px;flex-wrap:wrap}
    .sf>span:first-child{min-width:0;flex:1 1 150px}
    .sf small{display:block;font-size:11px;color:var(--secondary-text-color);font-family:ui-monospace,Consolas,monospace;overflow-wrap:anywhere}
    .sc2{display:flex;align-items:center;gap:6px;flex:0 1 auto;max-width:100%;margin-left:auto}.sc2 .u{font-size:12px;color:var(--secondary-text-color);min-width:30px}
    .sf input,.sf select{font:inherit;font-size:13px;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:8px;padding:5px 8px;max-width:min(190px,100%);min-width:0}
    .sf input[type=number]{width:96px}
    a.btn{text-decoration:none;display:inline-block}
    svg.hwp{display:block;width:100%;height:auto;margin-top:4px}
    svg.zoomable{touch-action:pan-y;cursor:grab}svg.zoomable:active{cursor:grabbing}
    .zrs{position:absolute;bottom:10px;right:12px;z-index:3;font:inherit;font-size:11.5px;border-radius:8px;padding:4px 10px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);cursor:pointer}
    .card{position:relative}
    .zhint{font-size:11px;color:var(--secondary-text-color);margin-top:4px}
    [hidden]{display:none!important}
    </style>`;
  }
}

if (!customElements.get("power-controller-panel")) customElements.define("power-controller-panel", PowerControllerPanel);
