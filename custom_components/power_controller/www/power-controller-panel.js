/*
 * Power Controller – overview panel (sidebar, admin only).
 *
 * Tabs: Overview (KPIs, 72 h plan, phases, devices, occupancy, Norgespris vs spot, grid tariff),
 *       Devices (one power-controller-object-card per device), Priorities (drag to reorder).
 * Data: websocket "power_controller/overview" every 10 s. Actions use normal services.
 * Styling: Home Assistant theme variables only. No external dependencies.
 */
const PCP_VERSION = "2.6.0";
const PCP_COLORS = ["#f97316", "#8b5cf6", "#06b6d4", "#22c55e", "#ec4899", "#eab308", "#3b82f6", "#14b8a6", "#ef4444", "#a855f7"];
const PCP_STATUS = {
  running: ["Running", "ok"], lent: ["Idle · lent", "info"], waiting: ["Waiting", "mut"], blocked: ["Blocked", "warn"],
  shedding: ["Shedding", "bad"], priority_now: ["Priority now", "warn"], manual: ["Manual", "mut"],
};
const PCP_OCC = {
  home_awake: ["Home & awake", "var(--success-color, #43a047)"], home_office: ["Home office", "#8b5cf6"],
  away_work: ["Away at work", "var(--divider-color, #ccc)"], sleep: ["Sleep", "#3b4a8a"], out_of_town: ["Out of town", "#888"],
};
const PCP_TYPE = { climate_heating: "Heating", climate_hotwater: "Hot water", ev_charger: "EV charger", switch: "Switch" };
const pE = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pN = (v) => (v === null || v === undefined || isNaN(Number(v)) ? null : Number(v));
const pF = (v, d = 2, fb = "–") => (pN(v) === null ? fb : Number(v).toFixed(d));
const pHH = (d) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
const pNok = (v) => (pN(v) === null ? "–" : Math.round(v).toLocaleString("nb-NO"));

class PowerControllerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "ov"; this._horizon = 72; this._focus = null; this._hidden = {};
    this._ptype = "plan"; this._both = false; this._order = null; this._cards = {};
    this._data = null; this._error = null;
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
    this._ro = new ResizeObserver(() => this._drawChart());
    const cw = this.shadowRoot.getElementById("cw"); if (cw) this._ro.observe(cw);
  }
  disconnectedCallback() { clearInterval(this._timer); this._ro?.disconnect(); }

  async _fetch() {
    if (!this._hass) return;
    try {
      this._data = await this._hass.callWS({ type: "power_controller/overview" });
      this._error = null;
    } catch (err) { this._error = err?.message || String(err); }
    this._update();
  }

  _call(domain, service, data) {
    return this._hass.callService(domain, service, data).then(() => setTimeout(() => this._fetch(), 1200))
      .catch((err) => this._say(`${domain}.${service} failed: ${err?.message || err}`));
  }
  _say(m) { const l = this.shadowRoot.getElementById("live"); if (l) l.textContent = m; }
  _color(i) { return PCP_COLORS[i % PCP_COLORS.length]; }
  _devColor(eid) { const i = (this._data?.devices || []).findIndex((d) => d.entry_id === eid); return this._color(Math.max(0, i)); }

  // ------------------------------------------------------------------ skeleton
  _skeleton() {
    const r = this.shadowRoot;
    r.innerHTML = `${this._css()}
      <div class="tbar"><span id="menu"></span><div class="brand"><div class="logo"><ha-icon icon="mdi:transmission-tower"></ha-icon></div>
        <div><h1>Power Controller</h1><div class="sub" id="sub">Loading…</div></div></div><div class="pills" id="pills"></div></div>
      <div class="wrap">
        <div class="bar2"><div class="seg" role="tablist" aria-label="Views">
          <button type="button" role="tab" data-tab="ov" aria-selected="true">Overview</button>
          <button type="button" role="tab" data-tab="dv" aria-selected="false">Devices</button>
          <button type="button" role="tab" data-tab="pr" aria-selected="false">Priorities</button></div>
          <span class="hint" id="err" role="alert"></span></div>
        <section id="v-ov">
          <div class="kpis" id="kpis"></div>
          <div class="row">
            <div class="card"><div class="hd"><div><h2>Energy plan</h2><div class="hint">Planned load per device (kWh/h), all-in price and spot (NOK/kWh)</div></div>
              <div class="seg" id="hz"><button type="button" data-h="24" aria-pressed="false">24 h</button><button type="button" data-h="72" aria-pressed="true">72 h</button></div></div>
              <div class="legend" id="lg"></div><div class="chartw" id="cw"><svg id="svg" role="img" aria-label="Energy plan"></svg><div class="tip" id="tip"></div></div></div>
            <div class="card"><div class="hd"><h2>Phases · 230 V IT</h2><span id="phchip"></span></div><div id="ph"></div>
              <div class="hint">Overload acceptance per phase: state and remaining budget.</div></div>
          </div>
          <div class="card mb"><div class="hd"><div><h2>Devices</h2><div class="hint">Select a device to highlight it in the plan</div></div><span class="hint" id="sel">All devices</span></div>
            <div class="dev h"><span>Device</span><span>Status</span><span class="x">Now</span><span class="x">Next</span><span class="x">Next 24 h</span><span class="x">Prio</span></div><div id="devs"></div></div>
          <div class="row3">
            <div class="card"><div class="hd"><h2>Occupancy</h2><span class="hint">Comfort follows expected presence</span></div><div id="occ"></div>
              <div class="keys">${["home_awake", "home_office", "away_work", "sleep"].map((k) => `<span><i class="sw" style="background:${PCP_OCC[k][1]}"></i> ${PCP_OCC[k][0]}</span>`).join("")}</div></div>
            <div class="card"><div class="hd"><h2>Norgespris vs. spot</h2><span class="hint" id="shm">Shadow evaluation</span></div><div id="sh"></div></div>
            <div class="card"><div class="hd"><h2>Grid tariff</h2><span class="hint" id="tfn"></span></div><div id="tf"></div></div>
          </div>
        </section>
        <section id="v-dv" hidden><div class="hd"><div><h2>Devices</h2><div class="hint">The same object cards you can place on any dashboard (custom:power-controller-object-card)</div></div></div><div class="dcs" id="cards"></div></section>
        <section id="v-pr" hidden><div class="row">
          <div class="card"><div class="hd"><div><h2>Priority order</h2><div class="hint" id="prh"></div></div>
            <div class="seg" id="pt"><button type="button" data-t="plan" aria-pressed="true">Planning</button><button type="button" data-t="dist" aria-pressed="false">Distribution</button></div></div>
            <ol class="pl" id="pl"></ol>
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
      ["ov", "dv", "pr"].forEach((k) => { r.getElementById(`v-${k}`).hidden = k !== this._tab; });
      this._update();
    }));
    r.querySelectorAll("#hz button").forEach((b) => b.addEventListener("click", () => {
      this._horizon = Number(b.dataset.h);
      r.querySelectorAll("#hz button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
      this._drawChart();
    }));
    const svg = r.getElementById("svg");
    svg.addEventListener("pointermove", (e) => this._tipAt(e));
    svg.addEventListener("pointerdown", (e) => this._tipAt(e));
    svg.addEventListener("pointerleave", () => { r.getElementById("tip").style.display = "none"; });
    r.querySelectorAll("#pt button").forEach((b) => b.addEventListener("click", () => {
      this._ptype = b.dataset.t; this._order = null;
      r.querySelectorAll("#pt button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
      this._renderPrio();
    }));
    r.getElementById("both").addEventListener("click", (e) => { this._both = !this._both; e.currentTarget.setAttribute("aria-checked", this._both); this._renderPrio(); });
    r.getElementById("reset").addEventListener("click", () => { this._order = null; this._renderPrio(); r.getElementById("toast").textContent = ""; });
    r.getElementById("apply").addEventListener("click", () => this._applyPrio());
    this._bindPrioList();
    if (this.isConnected && this._ro) this._ro.observe(r.getElementById("cw"));
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
    if (this._tab === "ov") this._renderOverview(d);
    if (this._tab === "dv") this._renderCards(d);
    if (this._tab === "pr") this._renderPrio();
  }

  _renderOverview(d) {
    const r = this.shadowRoot, k = d.kpi;
    const steps = d.tariff.capacity_steps || [];
    const si = steps.findIndex((s) => s.upper_kw > 0 && Math.abs(s.upper_kw - k.active_step_kw) < 1e-6);
    const lower = si > 0 ? steps[si - 1].upper_kw : 0;
    const stepCost = si >= 0 ? steps[si].monthly_nok : null;
    const pct = (a, b) => Math.max(0, Math.min(100, (pN(a) ?? 0) / Math.max(0.01, pN(b) ?? 1) * 100));
    const pn = k.price_now || {};
    const kp = [
      ["Grid power", pF(k.grid_power_kw), "kW", `Limit now ${pF(k.limit_now_kw)} kW`, pct(k.grid_power_kw, k.limit_now_kw), k.grid_power_kw > k.limit_now_kw ? "var(--error-color)" : "var(--primary-color)"],
      ["This hour", pF(k.energy_this_hour_kwh), "kWh", `Projected ${pF(k.energy_projected_kwh, 1)} of ${pF(k.hour_allowance_kwh)} kWh allowed`, pct(k.energy_projected_kwh, k.hour_allowance_kwh), k.energy_projected_kwh > k.hour_allowance_kwh ? "var(--warning-color)" : "var(--success-color)"],
      ["Capacity step", `${lower}–${pF(k.active_step_kw, 0)}`, "kW", `Top-3 average ${pF(k.top3_mean_kw)} kWh/h${stepCost !== null ? ` · ${pNok(stepCost)} NOK` : ""}`, pct(k.top3_mean_kw, k.active_step_kw), "var(--warning-color)"],
      ["Price now", pF(pn.total), "NOK/kWh", `Energy ${pF(pn.energy)} + grid ${pF(pn.grid)}${pn.period ? ` (${pE(pn.period)})` : ""}`, null],
      ["Month to date", pNok(k.month_cost_nok), "NOK", `${pNok(k.month_energy_kwh)} kWh · capacity ${pNok(k.capacity_charge_nok)} NOK`, null],
      ["Norgespris benefit", k.shadow_benefit_nok === null ? "Off" : `${k.shadow_benefit_nok >= 0 ? "+" : ""}${pNok(k.shadow_benefit_nok)}`, k.shadow_benefit_nok === null ? "" : "NOK", "vs. optimised spot this month", null, null, k.shadow_benefit_nok === null ? "" : k.shadow_benefit_nok >= 0 ? "pos" : "neg"],
    ];
    r.getElementById("kpis").innerHTML = kp.map(([l, v, u, s, b, c, cls]) => `<div class="card"><div class="k-l">${l}</div><div class="k-v num ${cls || ""}">${v}<small>${u}</small></div><div class="k-s">${s}</div>${b === null ? "" : `<div class="bar"><i style="width:${b}%;background:${c}"></i></div>`}</div>`).join("");

    // legend
    const lg = r.getElementById("lg");
    const series = [{ id: "base", name: "Baseload", c: "#94a3b8" }].concat(d.devices.map((x, i) => ({ id: x.entry_id, name: x.name, c: this._color(i) })));
    lg.innerHTML = series.map((s) => `<button type="button" class="lg" data-id="${s.id}" aria-pressed="${!this._hidden[s.id]}"><span class="sw" style="background:${s.c}"></span>${pE(s.name)}</button>`).join("")
      + `<span class="lg static"><span class="sw" style="background:var(--primary-text-color)"></span>Price</span><span class="lg static"><span class="sw dash"></span>Spot</span>`;
    lg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { this._hidden[b.dataset.id] = !this._hidden[b.dataset.id]; b.setAttribute("aria-pressed", !this._hidden[b.dataset.id]); this._drawChart(); }));
    this._series = series;
    this._drawChart();

    // phases
    let worst = "ok";
    r.getElementById("ph").innerHTML = ["L1", "L2", "L3"].map((ph) => {
      const p = d.phases[ph] || {}; const rated = pN(p.rated_a) || 1; const pc = (pN(p.amps) ?? 0) / rated * 100;
      if (p.state === "limiting") worst = "limiting"; else if (p.state === "accepting" && worst === "ok") worst = "accepting";
      const col = pc > 100 ? "var(--error-color)" : pc > 85 ? "var(--warning-color)" : "var(--primary-color)";
      return `<div class="ph"><div class="t"><span><b>${ph}</b> <span class="hint">· ${pE(p.state || "ok")} · budget ${pF(p.budget_pct, 0)} %</span></span><span class="num hint">${pF(p.amps, 1)} / ${pF(rated, 0)} A</span></div><div class="bar" style="height:10px"><i style="width:${Math.min(100, pc)}%;background:${col}"></i></div></div>`;
    }).join("");
    r.getElementById("phchip").innerHTML = worst === "ok" ? `<span class="chip c-ok">All phases OK</span>` : worst === "accepting" ? `<span class="chip c-warn">Accepting overload</span>` : `<span class="chip c-bad">Limiting</span>`;

    // devices table
    const idx = d.plan.index_now;
    r.getElementById("devs").innerHTML = d.devices.map((x, i) => {
      const st = PCP_STATUS[x.committed ? x.ui_status : "manual"] || PCP_STATUS.waiting;
      const plan = (x.plan_72h || []).slice(idx, idx + 24).map((v) => pN(v) ?? 0);
      const mx = Math.max(...plan, 0.5);
      const sp = plan.map((v) => `<i style="height:${Math.max(4, v / mx * 100)}%;background:${this._color(i)};opacity:${v > 0.05 ? 0.9 : 0.25}"></i>`).join("");
      const n = x.next_action;
      const nx = !x.committed ? "–" : n ? (n.running_now ? `now → ${pHH(new Date(n.end))}` : `${pHH(new Date(n.start))}–${pHH(new Date(n.end))}`) : "none";
      return `<div class="dev${this._focus === x.entry_id ? " sel" : ""}" role="button" tabindex="0" data-id="${x.entry_id}" aria-pressed="${this._focus === x.entry_id}">
        <span class="dn"><i class="sw" style="background:${this._color(i)}"></i> ${pE(x.name)}<small>${PCP_TYPE[x.device_type] || ""} · ${pE(x.phase_pair || "")}</small></span>
        <span><span class="chip c-${st[1]}">${st[0]}</span></span><span class="x num">${x.committed ? `${pF(x.live_kw)} kW` : "–"}</span>
        <span class="x num">${nx}</span><span class="x spark">${sp}</span><span class="x"><span class="prio">${x.plan_priority}</span></span></div>`;
    }).join("") || `<div class="hint" style="padding:10px">No devices yet. Add them under Settings → Devices & services → Power Controller.</div>`;
    r.querySelectorAll("#devs .dev").forEach((el) => {
      const go = () => { const id = el.dataset.id; this._focus = this._focus === id ? null : id;
        r.getElementById("sel").textContent = this._focus ? `Highlighting ${d.devices.find((x) => x.entry_id === id)?.name}` : "All devices";
        this._renderOverview(this._data); };
      el.addEventListener("click", go);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    });

    // occupancy
    const nowMin = new Date(d.now).getHours() * 60 + new Date(d.now).getMinutes();
    r.getElementById("occ").innerHTML = d.occupancy_days.map((day, i) => {
      const dt = new Date(`${day.date}T12:00:00`);
      const lbl = `${i === 0 ? "Today" : "Tomorrow"} · ${dt.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}`;
      const seg = day.quarters.map((q) => `<i style="background:${(PCP_OCC[q] || ["", "#888"])[1]}"></i>`).join("");
      const tog = day.workday ? `<button type="button" class="tg" role="switch" data-sw="${pE(day.switch || "")}" aria-checked="${day.home_office}" ${day.switch ? "" : "disabled"}>Home office<span class="k"></span></button>` : `<span class="hint">Day off</span>`;
      return `<div class="occ"><div class="t"><b>${lbl}</b>${tog}</div><div class="band">${seg}${i === 0 ? `<span class="now" style="left:${nowMin / 1440 * 100}%"></span>` : ""}</div>
        <div class="ticks"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div></div>`;
    }).join("");
    r.querySelectorAll("#occ [data-sw]").forEach((b) => b.addEventListener("click", () => {
      if (!b.dataset.sw) return;
      b.setAttribute("aria-checked", b.getAttribute("aria-checked") !== "true");
      this._call("switch", "toggle", { entity_id: b.dataset.sw }); this._say("Home office changed. Replanning.");
    }));

    // shadow
    const sh = d.shadow;
    if (!sh) {
      r.getElementById("sh").innerHTML = `<div class="hint">Shadow evaluation is off (select <b>Shadow Comparison Model</b> on the Power Controller device to enable it).</div>`;
    } else {
      const rows = [["Actual · " + pE(d.price_model), sh.actual.total_nok, "var(--primary-color)"], ["Optimised for spot", sh.optimised_spot.total_nok, "#8b5cf6"], ["Same usage on spot", sh.passive_spot.total_nok, "var(--secondary-text-color)"]];
      const mx = Math.max(...rows.map((x) => x[1]), 1);
      const b = sh.benefit_nok;
      r.getElementById("shm").textContent = `Shadow evaluation · ${sh.month}`;
      r.getElementById("sh").innerHTML = `<div class="big ${b >= 0 ? "pos" : "neg"}">${b >= 0 ? "+" : ""}${pNok(b)} NOK</div>
        <div class="hint" style="margin-bottom:12px">${b >= 0 ? "Your current price model is cheaper this month" : "Spot with optimisation would have been cheaper this month"} · 12 months: ${sh.rolling_12_months.benefit_nok >= 0 ? "+" : ""}${pNok(sh.rolling_12_months.benefit_nok)} NOK</div>
        ${rows.map(([n, v, c]) => `<div class="sc"><div class="t"><span>${n}</span><b class="num">${pNok(v)} NOK</b></div><div class="bar" style="height:9px"><i style="width:${v / mx * 100}%;background:${c}"></i></div></div>`).join("")}
        <div class="hint">Optimiser gain on spot: ${pNok(sh.optimiser_gain_nok)} NOK · EV shortfall ${pF(sh.ev_shortfall_kwh, 1)} kWh · ${sh.hours} h counted (${sh.hours_estimated_spot} with estimated spot)</div>`;
    }

    // tariff
    const t = d.tariff;
    r.getElementById("tfn").textContent = `${t.name} · valid from ${t.valid_from}`;
    const names = [...new Set(t.today.map((x) => x.period))];
    const pc = ["var(--info-color, #039be5)", "var(--warning-color, #ffa600)", "#8b5cf6", "#22c55e", "#ec4899"];
    const colOf = (n) => pc[names.indexOf(n) % pc.length];
    const legend = names.map((n) => { const p = t.today.find((x) => x.period === n).price; return `<span><i class="sw" style="background:${colOf(n)}"></i> ${pE(n)} ${pF(p)}</span>`; }).join("");
    const stp = t.capacity_steps;
    const ai = stp.findIndex((s) => s.upper_kw > 0 && Math.abs(s.upper_kw - k.active_step_kw) < 1e-6);
    const shown = stp.slice(0, Math.max(6, ai + 2)).slice(0, 8);
    const maxP = Math.max(...shown.map((s) => s.monthly_nok), 1);
    r.getElementById("tf").innerHTML = `<div class="hint" style="margin-bottom:6px">Energy charge today incl. fees and VAT</div>
      <div class="band">${t.today.map((x) => `<i title="${String(x.hour).padStart(2, "0")}:00 ${pE(x.period)} ${pF(x.price)}" style="background:${colOf(x.period)}"></i>`).join("")}<span class="now" style="left:${nowMin / 1440 * 100}%"></span></div>
      <div class="ticks"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div><div class="keys">${legend}</div>
      <div class="hint" style="margin-top:14px">Capacity steps (NOK/month incl. VAT)</div>
      <div class="ladder">${shown.map((s, i) => `<div class="${i === ai ? "on" : ""}" style="height:${25 + (s.monthly_nok / maxP) * 75}%">${pNok(s.monthly_nok)}</div>`).join("")}</div>
      <div class="ll">${shown.map((s, i) => `<span>${s.upper_kw > 0 ? `${i ? shown[i - 1].upper_kw : 0}–${s.upper_kw}` : `>${i ? shown[i - 1].upper_kw : 0}`}</span>`).join("")}</div>`;
  }

  // ------------------------------------------------------------------ chart
  _drawChart() {
    const r = this.shadowRoot, d = this._data;
    const svg = r.getElementById("svg"), cw = r.getElementById("cw");
    if (!d || !svg || this._tab !== "ov" || !cw.clientWidth) return;
    const idx = d.plan.index_now, n = Math.min(this._horizon, 72 - idx);
    const start = new Date(d.plan.start);
    const at = (arr, t) => pN((arr || [])[idx + t]) ?? 0;
    const stacks = [];
    let kmax = 1;
    for (let t = 0; t < n; t++) {
      let acc = 0; const parts = [];
      for (const s of this._series) {
        if (this._hidden[s.id]) continue;
        const v = s.id === "base" ? at(d.plan.baseload_72h, t) : at(d.devices.find((x) => x.entry_id === s.id)?.plan_72h, t);
        if (v > 0) { parts.push([s, acc, v]); acc += v; }
      }
      stacks.push(parts); kmax = Math.max(kmax, acc, at(d.plan.hour_cap_72h, t));
    }
    kmax = Math.ceil(kmax * 1.1);
    const prices = [...Array(n).keys()].map((t) => pN(d.plan.price_72h[idx + t]));
    const spots = [...Array(n).keys()].map((t) => pN(d.plan.spot_72h[idx + t]));
    const pmax = Math.max(0.5, ...prices.filter((x) => x !== null), ...spots.filter((x) => x !== null)) * 1.15;
    const Wd = Math.max(280, cw.clientWidth), Ht = Wd < 520 ? 240 : 300, L = 38, R = 46, T = 12, B = 30, iw = Wd - L - R, ih = Ht - T - B, bw = iw / n, step = Wd < 560 ? 12 : 6;
    this._geo = { L, bw, n, Wd, idx, start, stacks, prices, spots };
    const yk = (v) => T + ih - ih * v / kmax, yp = (v) => T + ih - ih * v / pmax;
    let o = "";
    for (let g = 0; g <= 4; g++) {
      const y = T + ih - ih * g / 4;
      o += `<line x1="${L}" x2="${L + iw}" y1="${y}" y2="${y}" style="stroke:var(--divider-color)"/>`;
      o += `<text x="${L - 8}" y="${y + 4}" text-anchor="end" font-size="10.5" style="fill:var(--secondary-text-color)">${(kmax * g / 4).toFixed(kmax < 8 ? 1 : 0)}</text>`;
      o += `<text x="${L + iw + 8}" y="${y + 4}" font-size="10.5" style="fill:var(--secondary-text-color)">${(pmax * g / 4).toFixed(2)}</text>`;
    }
    for (let t = 0; t < n; t++) {
      const ts = new Date(start.getTime() + (idx + t) * 3600e3), h = ts.getHours(), x = L + t * bw;
      if (h === 0) o += `<line x1="${x}" x2="${x}" y1="${T}" y2="${T + ih}" style="stroke:var(--secondary-text-color)" stroke-dasharray="2,3" opacity=".5"/><text x="${x + 4}" y="${T + 11}" font-size="10.5" font-weight="600" style="fill:var(--secondary-text-color)">${ts.toLocaleDateString(undefined, { weekday: "short" })}</text>`;
      if (h % step === 0) o += `<text x="${x + bw / 2}" y="${Ht - 10}" text-anchor="middle" font-size="10.5" style="fill:var(--secondary-text-color)">${String(h).padStart(2, "0")}:00</text>`;
      for (const [s, acc, v] of stacks[t]) {
        const op = this._focus && this._focus !== s.id ? 0.18 : 0.92;
        o += `<rect x="${x + bw * 0.12}" y="${yk(acc + v)}" width="${Math.max(1, bw * 0.76)}" height="${Math.max(0, ih * v / kmax - 0.5)}" rx="1.5" fill="${s.c}" opacity="${op}"/>`;
      }
    }
    o += `<rect x="${L}" y="${T}" width="${bw}" height="${ih}" style="fill:var(--primary-color)" opacity=".1"/>`;
    let cap = "", p1 = "", p2 = "";
    for (let t = 0; t < n; t++) {
      const x = L + t * bw, c = yk(at(d.plan.hour_cap_72h, t));
      cap += `${t ? "L" : "M"}${x},${c}L${x + bw},${c}`;
      if (prices[t] !== null) p1 += `${p1 ? "L" : "M"}${x},${yp(prices[t])}L${x + bw},${yp(prices[t])}`;
      if (spots[t] !== null) p2 += `${p2 ? "L" : "M"}${x + bw / 2},${yp(spots[t])}`;
    }
    o += `<path d="${cap}" fill="none" style="stroke:var(--error-color)" stroke-dasharray="6,4" stroke-width="1.4"/>`;
    o += `<text x="${L + 4}" y="${yk(at(d.plan.hour_cap_72h, 0)) - 5}" font-size="10.5" style="fill:var(--error-color)">Hour allowance ${pF(at(d.plan.hour_cap_72h, 0))} kWh</text>`;
    if (p2) o += `<path d="${p2}" fill="none" style="stroke:var(--secondary-text-color)" stroke-width="1.2" stroke-dasharray="3,3" opacity=".8"/>`;
    if (p1) o += `<path d="${p1}" fill="none" style="stroke:var(--primary-text-color)" stroke-width="1.8"/>`;
    o += `<line id="hv" x1="0" x2="0" y1="${T}" y2="${T + ih}" style="stroke:var(--primary-text-color)" opacity="0"/>`;
    svg.setAttribute("viewBox", `0 0 ${Wd} ${Ht}`); svg.setAttribute("height", Ht); svg.innerHTML = o;
  }

  _tipAt(ev) {
    const g = this._geo, r = this.shadowRoot, svg = r.getElementById("svg"), tip = r.getElementById("tip");
    if (!g) return;
    const b = svg.getBoundingClientRect(), mx = (ev.clientX - b.left) * (g.Wd / b.width), t = Math.floor((mx - g.L) / g.bw);
    if (t < 0 || t >= g.n) { tip.style.display = "none"; return; }
    const ts = new Date(g.start.getTime() + (g.idx + t) * 3600e3);
    const tot = g.stacks[t].reduce((s, p) => s + p[2], 0);
    tip.innerHTML = `<b>${ts.toLocaleDateString(undefined, { weekday: "short" })} ${pHH(ts)}${t === 0 ? " · now" : ""}</b>
      <div class="r"><span>Total</span><b class="num">${tot.toFixed(2)} kWh</b></div><div class="r"><span>Price</span><b class="num">${pF(g.prices[t])} NOK</b></div>
      <div class="r"><span>Spot</span><span class="num">${pF(g.spots[t])}</span></div><div class="sep"></div>
      ${g.stacks[t].filter((p) => p[2] > 0.05).map(([s, , v]) => `<div class="r"><span><i class="sw" style="background:${s.c}"></i> ${pE(s.name)}</span><b class="num">${v.toFixed(2)}</b></div>`).join("")}`;
    tip.style.display = "block";
    const px = (g.L + t * g.bw) * (b.width / g.Wd); let left = px + 16; if (left + 200 > b.width) left = px - 210;
    tip.style.left = `${Math.max(0, left)}px`; tip.style.top = "10px";
    const hv = svg.querySelector("#hv"); if (hv) { const cx = g.L + t * g.bw + g.bw / 2; hv.setAttribute("x1", cx); hv.setAttribute("x2", cx); hv.setAttribute("opacity", ".35"); }
  }

  // ------------------------------------------------------------------ devices tab
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
    r.getElementById("pl").innerHTML = this._order.map((id, i) => {
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
    const r = this.shadowRoot, pl = r.getElementById("pl");
    pl.addEventListener("click", (e) => {
      const b = e.target.closest("[data-mv]"); if (!b) return;
      const i = this._order.indexOf(b.closest("li").dataset.id), j = i + Number(b.dataset.mv);
      if (j < 0 || j >= this._order.length) return;
      [this._order[i], this._order[j]] = [this._order[j], this._order[i]]; this._renderPrio();
      (r.querySelector(`#pl li:nth-child(${j + 1}) [data-mv="${b.dataset.mv}"]`) || r.querySelector(`#pl li:nth-child(${j + 1}) [data-mv]`))?.focus();
    });
    let dragId = null;
    pl.addEventListener("dragstart", (e) => { const li = e.target.closest("li"); if (!li) return; dragId = li.dataset.id; li.classList.add("drag"); e.dataTransfer.effectAllowed = "move"; try { e.dataTransfer.setData("text/plain", dragId); } catch (_) { /* */ } });
    pl.addEventListener("dragover", (e) => {
      if (!dragId) return; e.preventDefault();
      const li = e.target.closest("li"); if (!li || li.dataset.id === dragId) return;
      const from = this._order.indexOf(dragId), to = this._order.indexOf(li.dataset.id);
      this._order.splice(from, 1); this._order.splice(to, 0, dragId); this._renderPrio();
      r.querySelector(`#pl li[data-id="${dragId}"]`)?.classList.add("drag");
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
    [hidden]{display:none!important}
    </style>`;
  }
}

if (!customElements.get("power-controller-panel")) customElements.define("power-controller-panel", PowerControllerPanel);
