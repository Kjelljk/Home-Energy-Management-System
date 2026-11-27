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
const PCP_VERSION = "2.9.0";
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
const PCP_FS_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M5 5h5v2H7v3H5V5m9 0h5v5h-2V7h-3V5m3 9h2v5h-5v-2h3v-3m-7 3v2H5v-5h2v3h3z"/></svg>';
const pE = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pN = (v) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v));
const pF = (v, d = 2, fb = "–") => (pN(v) === null ? fb : Number(v).toFixed(d));
const pHH = (d) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
const pNok = (v) => (pN(v) === null ? "–" : Math.round(v).toLocaleString("nb-NO"));
const pDay = (d) => d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
const pMs = (v) => (typeof v === "number" ? (v > 1e12 ? v : v * 1000) : Date.parse(v));
const niceMax = (m) => { const s = m <= 1 ? 0.25 : m <= 3 ? 0.5 : m <= 10 ? 1 : m <= 30 ? 5 : 10; return Math.max(s, Math.ceil(m / s) * s); };

/* ---------------------------------------------------------------- chart helpers (SVG) */
function pcBarChart(svg, width, o) {
  // o: {n, times[], bars:[{id,name,color,values,opacity}], lines:[{name,color,values,axis,dash,step,width}],
  //     cap:{values,label}, nowIdx, focusId, height, yLabel, yrLabel, dimBefore}
  const W = Math.max(280, width), Ht = o.height || (W < 520 ? 230 : 280), L = 40, R = o.lines?.some((l) => l.axis === "r") ? 46 : 12, T = 14, B = 30;
  const iw = W - L - R, ih = Ht - T - B, n = Math.max(1, o.n), bw = iw / n, step = W < 560 ? 12 : 6;
  let kmax = 0.5;
  for (let t = 0; t < n; t++) {
    let acc = 0; for (const b of o.bars) acc += Math.max(0, pN(b.values[t]) ?? 0);
    kmax = Math.max(kmax, acc, o.cap ? pN(o.cap.values[t]) ?? 0 : 0);
    for (const l of o.lines || []) if (l.axis !== "r") kmax = Math.max(kmax, pN(l.values[t]) ?? 0);
  }
  kmax = niceMax(kmax * 1.05);
  const rv = (o.lines || []).filter((l) => l.axis === "r").flatMap((l) => l.values.map(pN).filter((x) => x !== null));
  const pmax = rv.length ? niceMax(Math.max(...rv) * 1.1) : 1;
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
    s += `<rect x="${L}" y="${T}" width="${bw * o.nowIdx}" height="${ih}" class="past"/>`;
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
      s += `<rect x="${x + bw * 0.1}" y="${yk(acc + v)}" width="${Math.max(1, bw * 0.8)}" height="${Math.max(0.5, ih * v / kmax - 0.4)}" rx="1.2" fill="${b.color}" opacity="${op}"${b.hatch ? ' class="hatch"' : ""}/>`;
      acc += v;
    }
  }
  if (o.cap) {
    let d = ""; for (let t = 0; t < n; t++) { const v = pN(o.cap.values[t]); if (v === null) continue; const y = yk(v); d += `${d ? "L" : "M"}${L + t * bw},${y}L${L + (t + 1) * bw},${y}`; }
    if (d) s += `<path d="${d}" class="cap"/><text x="${L + iw - 4}" y="${yk(pN(o.cap.values[n - 1]) ?? 0) - 5}" text-anchor="end" class="tx capt">${o.cap.label}</text>`;
  }
  for (const l of o.lines || []) {
    let d = ""; const yf = l.axis === "r" ? yp : yk;
    for (let t = 0; t < n; t++) {
      const v = pN(l.values[t]); if (v === null) { if (l.gap) d += " "; continue; }
      const x = L + t * bw, y = yf(v);
      d += l.step ? `${d && !d.endsWith(" ") ? "L" : "M"}${x},${y}L${x + bw},${y}` : `${d && !d.endsWith(" ") ? "L" : "M"}${x + bw / 2},${y}`;
    }
    if (d) s += `<path d="${d.replace(/ M/g, "M")}" fill="none" stroke="${l.color}" stroke-width="${l.width || 1.7}"${l.dash ? ` stroke-dasharray="${l.dash}"` : ""}/>`;
  }
  if (o.nowIdx !== undefined && o.nowIdx !== null && o.nowIdx >= 0 && o.nowIdx <= n) {
    const x = L + bw * o.nowIdx;
    s += `<line x1="${x}" x2="${x}" y1="${T}" y2="${T + ih}" class="nowl"/><text x="${x + 3}" y="${T + ih - 4}" class="tx nowt">now</text>`;
  }
  s += `<line class="hv" x1="0" x2="0" y1="${T}" y2="${T + ih}" style="opacity:0"/>`;
  svg.setAttribute("viewBox", `0 0 ${W} ${Ht}`); svg.setAttribute("height", Ht); svg.innerHTML = s;
  return { L, bw, n, W, T, ih };
}

function pcTsChart(svg, width, o) {
  // o: {t0, t1, series:[{name,color,points:[[ms,v]],axis:'l'|'r',width}], stacked:[{name,color,values[]}] (binned), bins, yLabel, yrLabel, height}
  const W = Math.max(280, width), Ht = o.height || 220, L = 40, R = o.series?.some((x) => x.axis === "r") ? 46 : 12, T = 14, B = 26;
  const iw = W - L - R, ih = Ht - T - B, span = Math.max(1, o.t1 - o.t0);
  const xs = (ms) => L + iw * (ms - o.t0) / span;
  let lmax = 0.1, rmin = Infinity, rmax = -Infinity;
  if (o.stacked) for (let i = 0; i < o.bins; i++) { let a = 0; for (const st of o.stacked) a += Math.max(0, st.values[i] ?? 0); lmax = Math.max(lmax, a); }
  for (const se of o.series || []) for (const [, v] of se.points) { if (se.axis === "r") { rmin = Math.min(rmin, v); rmax = Math.max(rmax, v); } else lmax = Math.max(lmax, v); }
  lmax = niceMax(lmax * 1.05);
  if (rmin === Infinity) { rmin = 0; rmax = 1; }
  else if (o.rWindow) { rmin = Math.min(o.rWindow[0], Math.floor(rmin - 1)); rmax = Math.max(o.rWindow[1], Math.ceil(rmax + 1)); }
  else { rmin = Math.floor(rmin - 1); rmax = Math.ceil(rmax + 1); }
  const yl = (v) => T + ih - ih * v / lmax, yr = (v) => T + ih - ih * (v - rmin) / (rmax - rmin);
  let s = "";
  for (let g = 0; g <= 4; g++) {
    const y = T + ih - ih * g / 4;
    s += `<line x1="${L}" x2="${L + iw}" y1="${y}" y2="${y}" class="gl"/><text x="${L - 6}" y="${y + 4}" text-anchor="end" class="tx">${(lmax * g / 4).toFixed(lmax < 4 ? 1 : 0)}</text>`;
    if (R > 20) s += `<text x="${L + iw + 6}" y="${y + 4}" class="tx">${(rmin + (rmax - rmin) * g / 4).toFixed(0)}</text>`;
  }
  if (o.yLabel) s += `<text x="${L}" y="${T - 4}" class="tx">${o.yLabel}</text>`;
  if (o.yrLabel && R > 20) s += `<text x="${L + iw}" y="${T - 4}" text-anchor="end" class="tx">${o.yrLabel}</text>`;
  const ticks = o.tickEveryMs || (span <= 2 * 3600e3 ? (W < 500 ? 30 : 15) * 60e3 : (W < 500 ? 6 : 3) * 3600e3);
  for (let t = Math.ceil(o.t0 / ticks) * ticks; t <= o.t1; t += ticks) s += `<text x="${xs(t)}" y="${Ht - 8}" text-anchor="middle" class="tx">${pHH(new Date(t))}</text>`;
  if (o.stacked && o.bins) {
    const bw = iw / o.bins, base = new Array(o.bins).fill(0);
    for (const st of o.stacked) {
      let top = "", bot = "";
      for (let i = 0; i < o.bins; i++) {
        const v = Math.max(0, st.values[i] ?? 0), x = L + i * bw;
        top += `${i ? "L" : "M"}${x},${yl(base[i] + v)}L${x + bw},${yl(base[i] + v)}`;
        bot = `L${x + bw},${yl(base[i])}L${x},${yl(base[i])}` + bot;
        base[i] += v;
      }
      s += `<path d="${top}${bot}Z" fill="${st.color}" opacity=".85"/>`;
    }
  }
  for (const se of o.series || []) {
    if (!se.points.length) continue;
    const yf = se.axis === "r" ? yr : yl;
    let d = ""; se.points.forEach(([t, v], i) => { d += `${i ? "L" : "M"}${xs(t).toFixed(1)},${yf(v).toFixed(1)}`; });
    s += `<path d="${d}" fill="none" stroke="${se.color}" stroke-width="${se.width || 1.3}" opacity=".9"/>`;
  }
  svg.setAttribute("viewBox", `0 0 ${W} ${Ht}`); svg.setAttribute("height", Ht); svg.innerHTML = s;
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
    this._data = null; this._error = null; this._hist = {}; this._histTs = 0; this._geo = {};
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
    const day = await hist([...Object.values(he.currents || {}), ...Object.values(he.voltages || {})], 24 * 3600e3);
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
          <h3 class="sec"><span>Now &amp; history</span><small>measured · updates every 10 s</small></h3>
          <div class="kpis" id="kpis"></div>
          <div class="row">
            <div class="card"><div class="hd"><div><h2>Power per device · last hour</h2><div class="hint">kW, measured. "Other" = grid − controllable devices</div></div><div class="vals" id="livev"></div></div>
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
    r.getElementById("pills").innerHTML = `<span class="pill"><span class="dot" style="background:${d.kpi.warning ? "var(--warning-color)" : "var(--success-color)"}"></span>Mode <b>${pE(d.mode)}</b></span>
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

  /* plan bars for n hours from now: baseload + every device (forecast-only devices lighter) */
  _planSeries(d, n) {
    const idx = d.plan.index_now, at = (arr, t) => pN((arr || [])[idx + t]) ?? 0;
    const bars = [{ id: "base", name: "Baseload", color: "#94a3b8", values: [...Array(n).keys()].map((t) => at(d.plan.baseload_72h, t)) }];
    d.devices.forEach((x, i) => {
      if (this._hidden[x.entry_id]) return;
      bars.push({ id: x.entry_id, name: x.name + (x.forecast_only ? " (forecast)" : ""), color: this._color(i), opacity: x.forecast_only ? 0.4 : 0.92,
        values: [...Array(n).keys()].map((t) => at(x.plan_72h, t)) });
    });
    const times = [...Array(n).keys()].map((t) => new Date(new Date(d.plan.start).getTime() + (idx + t) * 3600e3));
    return { bars, times, idx, at };
  }

  _planTip(d, s) {
    return (t) => {
      const tot = s.bars.reduce((a, b) => a + (pN(b.values[t]) ?? 0), 0);
      const rows = s.bars.filter((b) => (pN(b.values[t]) ?? 0) > 0.02).map((b) => `<div class="r"><span><i class="sw" style="background:${b.color}"></i> ${pE(b.name)}</span><b class="num">${pF(b.values[t])}</b></div>`).join("");
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
    tip.innerHTML = g.html(t);
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
    const pn = k.price_now || {};
    r.getElementById("kpis").innerHTML = this._kpiHtml([
      ["Grid power", pF(k.grid_power_kw), "kW", `whole house · limit now ${pF(k.limit_now_kw)} kW`, this._pct(k.grid_power_kw, k.limit_now_kw), k.grid_power_kw > k.limit_now_kw ? "var(--error-color)" : "var(--primary-color)"],
      ["This hour", pF(k.energy_this_hour_kwh), "kWh", `projected ${pF(k.energy_projected_kwh, 1)} of ${pF(k.hour_allowance_kwh)} kWh allowed`, this._pct(k.energy_projected_kwh, k.hour_allowance_kwh), k.energy_projected_kwh > k.hour_allowance_kwh ? "var(--warning-color)" : "var(--success-color)"],
      ["Capacity step", `${lower}–${pF(k.active_step_kw, 0)}`, "kW", `top-3 average ${pF(k.top3_mean_kw)} kWh/h · ${pNok(k.capacity_charge_nok)} NOK`, this._pct(k.top3_mean_kw, k.active_step_kw), "var(--warning-color)"],
      ["Price now", pF(pn.total), "NOK/kWh", `energy ${pF(pn.energy)} + grid ${pF(pn.grid)}${pn.period ? ` (${pE(pn.period)})` : ""}`],
      ["Month to date", pNok(k.month_cost_nok), "NOK", `${pNok(k.month_energy_kwh)} kWh (finished hours)`],
      np.active ? ["Norgespris left", pNok(np.remaining_kwh), "kWh", np.projected_reach_date ? `runs out ≈ ${pDay(new Date(np.projected_reach_date))}` : `of ${pNok(np.volume_kwh)} kWh · lasts the month`, 100 - this._pct(np.used_kwh, np.volume_kwh), "var(--info-color, var(--primary-color))"]
        : ["Norgespris benefit", k.shadow_benefit_nok === null ? "Off" : `${k.shadow_benefit_nok >= 0 ? "+" : ""}${pNok(k.shadow_benefit_nok)}`, k.shadow_benefit_nok === null ? "" : "NOK", "vs. optimised spot this month"],
    ]);
    this._renderLive(d);
    this._renderPhases(d);
    this._renderHistory(d);
    // plan (24 h) + device table
    const s = this._planSeries(d, 24);
    const g = pcBarChart(r.getElementById("svg-ov"), this._w("cw-ov"), { n: 24, times: s.times, bars: s.bars, focusId: this._focus, height: this._fsH("svg-ov", 220), yLabel: "kWh/h", yrLabel: "NOK/kWh",
      cap: { values: s.times.map((_, t) => s.at(d.plan.hour_cap_72h, t)), label: "hour allowance" },
      lines: [{ name: "price", color: "var(--primary-text-color)", values: s.times.map((_, t) => s.at(d.plan.price_72h, t)), axis: "r", step: true }] });
    this._geo.ov = { ...g, html: this._planTip(d, s) };
    this._renderDevTable(d);
  }

  _renderLive(d) {
    const r = this.shadowRoot, he = d.history_entities || {}, h = this._hist.hour || {}, now = this._hist.t || Date.now();
    const t0 = now - 3600e3, bins = 60;
    const stacked = [];
    let sumBins = new Array(bins).fill(0);
    d.devices.forEach((x, i) => {
      const id = he.devices?.[x.entry_id]; const pts = (h[id] || []).map(([t, v]) => [t, v * this._scale(id)]);
      if (!pts.length) return;
      const vals = pcBin(pts, t0, now, bins).map((v) => Math.max(0, v ?? 0));
      sumBins = sumBins.map((a, j) => a + vals[j]);
      stacked.push({ name: x.name, color: this._color(i), values: vals });
    });
    const gid = he.grid_power, gp = (h[gid] || []).map(([t, v]) => [t, v * this._scale(gid)]);
    if (gp.length) {
      const g = pcBin(gp, t0, now, bins).map((v) => v ?? 0);
      stacked.push({ name: "Other", color: "#94a3b8", values: g.map((v, j) => Math.max(0, v - sumBins[j])) });
    }
    pcTsChart(r.getElementById("svg-live"), this._w("svg-live"), { t0, t1: now, bins, stacked, yLabel: "kW", height: this._fsH("svg-live", 210) });
    r.getElementById("lg-live").innerHTML = stacked.length ? stacked.map((st) => `<span class="lg static"><span class="sw" style="background:${st.color}"></span>${pE(st.name)}</span>`).join("")
      : `<span class="hint">No recorder history yet for the power sensors.</span>`;
    r.getElementById("livev").innerHTML = d.devices.map((x, i) => `<span><i class="sw" style="background:${this._color(i)}"></i>${pF(x.live_kw, 1)}</span>`).join("") + `<span><b>${pF(d.kpi.grid_power_kw, 1)} kW</b></span>`;
  }

  _renderPhases(d) {
    const r = this.shadowRoot, he = d.history_entities || {}, h = this._hist.day || {}, now = this._hist.t || Date.now();
    const cc = { L1: "#06b6d4", L2: "#8b5cf6", L3: "#94a3b8" }, vc = { "L1-L2": "#0e7490", "L2-L3": "#6d28d9", "L3-L1": "#64748b" };
    const series = [];
    for (const [ph, id] of Object.entries(he.currents || {})) if (id && h[id]) series.push({ name: `${ph} A`, color: cc[ph], points: h[id].map(([t, v]) => [t, v * this._scale(id)]), width: 1.2 });
    for (const [lbl, id] of Object.entries(he.voltages || {})) if (id && h[id]) series.push({ name: `${lbl} V`, color: vc[lbl], axis: "r", points: h[id], width: 1 });
    pcTsChart(r.getElementById("svg-ph"), this._w("svg-ph"), { t0: now - 24 * 3600e3, t1: now, series, yLabel: "A", yrLabel: "V", height: this._fsH("svg-ph", 210), rWindow: [215, 230] });
    r.getElementById("lg-ph").innerHTML = series.length ? series.map((x) => `<span class="lg static"><span class="sw" style="background:${x.color}"></span>${x.name}</span>`).join("")
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
    const g = pcBarChart(r.getElementById("svg-hist"), this._w("cw-hist"), { n, times, bars, nowIdx: back, height: this._fsH("svg-hist", 260), yLabel: "kWh/h", yrLabel: "NOK/kWh",
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
    r.getElementById("devs").innerHTML = d.devices.map((x, i) => {
      const st = PCP_STATUS[x.committed ? x.ui_status : "manual"] || PCP_STATUS.waiting;
      const plan = (x.plan_72h || []).slice(idx, idx + 24).map((v) => pN(v) ?? 0);
      const mx = Math.max(...plan, 0.5);
      const sp = plan.map((v) => `<i style="height:${Math.max(4, v / mx * 100)}%;background:${this._color(i)};opacity:${v > 0.05 ? (x.forecast_only ? 0.45 : 0.9) : 0.2}"></i>`).join("");
      const n = x.next_action;
      const nx = x.forecast_only ? `≈ ${plan.reduce((a, b) => a + b, 0).toFixed(1)} kWh/24 h` : n ? (n.running_now ? `now → ${pHH(new Date(n.end))}` : `${pHH(new Date(n.start))}–${pHH(new Date(n.end))}`) : "none";
      return `<div class="dev${this._focus === x.entry_id ? " sel" : ""}" role="button" tabindex="0" data-id="${x.entry_id}" aria-pressed="${this._focus === x.entry_id}">
        <span class="dn"><i class="sw" style="background:${this._color(i)}"></i> ${pE(x.name)}<small>${PCP_TYPE[x.device_type] || ""} · ${pE(x.phase_pair || "")}</small></span>
        <span><span class="chip c-${st[1]}">${st[0]}</span></span><span class="x num">${pF(x.live_kw)} kW</span>
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
    const g = pcBarChart(r.getElementById("svg-pr"), this._w("cw-pr"), { n, times, height: this._fsH("svg-pr", 250), yLabel: "NOK/kWh",
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
    if (active && box.contains(active) && ["INPUT", "SELECT"].includes(active.tagName)) return;   // don't disturb typing
    const st = (id) => this._hass?.states?.[id];
    const name = (id) => {
      const n = st(id)?.attributes?.friendly_name || id;
      return n.replace(/^Power Controller /, "");
    };
    const field = (id) => {
      const s = st(id); if (!s) return "";
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
      return `<label class="sf"><span>${pE(name(id))}<small>${pE(id)}</small></span><span class="sc2">${ctl}</span></label>`;
    };
    box.innerHTML = (d.settings || []).map((g) => `<div class="card"><h2>${pE(g.title)}</h2><div class="sfl">${g.entities.map(field).join("")}</div></div>`).join("");
    box.querySelectorAll("[data-eid]").forEach((el) => el.addEventListener("change", () => {
      const id = el.dataset.eid, dom = id.split(".")[0];
      if (dom === "number") {
        const v = Number(el.value); if (el.value === "" || isNaN(v)) return;
        this._call("number", "set_value", { entity_id: id, value: v });
      } else if (dom === "select") this._call("select", "select_option", { entity_id: id, option: el.value });
      else if (dom === "time" && el.value) this._call("time", "set_value", { entity_id: id, time: `${el.value}:00` });
      else if (dom === "datetime" && el.value) this._call("datetime", "set_value", { entity_id: id, datetime: `${el.value.replace("T", " ")}:00` });
      this._say(`${name(id)} saved`);
    }));
  }

  // ------------------------------------------------------------------ PLAN
  _renderPlan(d) {
    if (!d) return;
    const r = this.shadowRoot, n = Math.min(this._horizon, 72 - d.plan.index_now);
    const lg = r.getElementById("lg-pl");
    lg.innerHTML = d.devices.map((x, i) => `<button type="button" class="lg" data-id="${x.entry_id}" aria-pressed="${!this._hidden[x.entry_id]}"><span class="sw" style="background:${this._color(i)};opacity:${x.forecast_only ? 0.45 : 1}"></span>${pE(x.name)}${x.forecast_only ? " · forecast" : ""}</button>`).join("")
      + `<span class="lg static"><span class="sw" style="background:#94a3b8"></span>Baseload</span><span class="lg static"><span class="sw ln" style="border-color:var(--primary-text-color)"></span>Price</span>`;
    lg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { this._hidden[b.dataset.id] = !this._hidden[b.dataset.id]; this._renderPlan(this._data); }));
    const s = this._planSeries(d, n);
    const g = pcBarChart(r.getElementById("svg-pl"), this._w("cw-pl"), { n, times: s.times, bars: s.bars, height: this._fsH("svg-pl", 290), yLabel: "kWh/h", yrLabel: "NOK/kWh",
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
    const g = pcBarChart(r.getElementById("svg-pva"), this._w("cw-pva"), { n: h.length, times, height: this._fsH("svg-pva", 230), yLabel: "kWh/h",
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
    r.getElementById("dtab").innerHTML = `<div class="dt h"><span>Device · reason</span><span>Status</span><span class="num">Live / granted</span></div>` +
      d.devices.map((x, i) => {
        const st = PCP_STATUS[x.committed ? x.ui_status : "manual"] || PCP_STATUS.waiting;
        const why = x.committed ? ({ blocked: "planned, no capacity left", "not planned": "not planned this hour" }[x.dist_reason] || (x.ui_status === "lent" ? "reserved but idle → lent" : x.ui_status === "priority_now" ? "priority now" : "has planned power")) : "not controlled – counted as load";
        return `<div class="dt"><span><i class="sw" style="background:${this._color(i)}"></i> ${pE(x.name)}<small>${why}</small></span><span><span class="chip c-${st[1]}">${st[0]}</span></span>
          <span class="num">${pF(x.live_kw)} / ${pF(x.allocated_kw)} kW<small>#${x.dist_priority}</small></span></div>`;
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
      { key: "tl", title: "Tariff limiter", lines: [`${pF(tl.energy_this_hour_kwh)} / ${pF(tl.hour_allowance_kwh)} kWh this hour`, `limit now ${pF(tl.limit_now_kw)} kW`, tl.safe_mode ? "SAFE MODE (grid sensor stale)" : ""], c: tl.safe_mode ? "var(--error-color)" : "#7c6f9b" },
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
      const half = W / 2, pad = 10, gapX = 56, colW = Math.min(250, half - gapX - 120);
      const cx = pad, sideX = cx + colW + 18, sideW = Math.max(100, half - gapX - sideX);
      const bh = 70, vgap = 22;
      chain.forEach((b, i) => { boxes[b.key] = { x: cx, y: 8 + i * (bh + vgap), w: colW, h: bh, ...b }; });
      side.forEach((b, i) => { boxes[b.key] = { x: sideX, y: 8 + i * (58 + 12), w: sideW, h: 58, ...b }; });
      const dx = half + gapX / 2, dw = W - dx - 120, dh = 58, dgap = 14;
      const chainH = 8 + chain.length * (bh + vgap);
      const devH = devs.length * (dh + dgap);
      const y0 = Math.max(8, (chainH - devH) / 2);
      devs.forEach((x, i) => { boxes[x.entry_id] = { x: dx, y: y0 + i * (dh + dgap), w: dw, h: dh }; });
      H = Math.max(chainH, y0 + devH) + 10;
      for (let i = 0; i < chain.length - 1; i++) {
        const a = boxes[chain[i].key], b = boxes[chain[i + 1].key];
        s += `<line x1="${a.x + a.w / 2}" y1="${a.y + a.h}" x2="${b.x + b.w / 2}" y2="${b.y - 3}" class="fl" marker-end="url(#ah)"/>`;
      }
      const g = boxes.grid;
      side.forEach((sb) => { const b = boxes[sb.key]; s += `<path d="M${g.x + g.w},${g.y + g.h / 2} L${b.x - 4},${b.y + b.h / 2}" fill="none" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="4 3" marker-end="url(#ah)"/>`; });
      // distribution -> devices: leave the right edge of the box, fan out in the gap
      const db = boxes.dist, sx = db.x + db.w, sy = db.y + db.h / 2, mid = sx + (dx - sx) * 0.55;
      devs.forEach((x) => {
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
      bx += boxSvg(b, x.name, [`${st[0]} · ${pF(x.live_kw)} / ${pF(x.allocated_kw)} kW`, x.committed ? `#${x.dist_priority}` : "counted as load"], this._color(x.i));
    });
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("height", H);
    svg.innerHTML = `<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" style="fill:var(--secondary-text-color)"/></marker>
      <marker id="ahl" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" style="fill:var(--info-color, var(--primary-color))"/></marker></defs>${s}${bx}`;
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
    @media (max-width:900px){.row{grid-template-columns:minmax(0,1fr)}}
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
    svg .gl{stroke:var(--divider-color)}svg .dl{stroke:var(--secondary-text-color);stroke-dasharray:2 3;opacity:.6}
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
    .dt{display:grid;grid-template-columns:minmax(0,1fr) auto 104px;gap:8px;align-items:center;padding:8px 4px;border-top:1px solid var(--divider-color);font-size:13px}
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
    [hidden]{display:none!important}
    </style>`;
  }
}

if (!customElements.get("power-controller-panel")) customElements.define("power-controller-panel", PowerControllerPanel);
