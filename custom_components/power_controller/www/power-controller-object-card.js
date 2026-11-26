/*
 * Power Controller – object card (custom:power-controller-object-card)
 *
 * One card per controlled object for normal user dashboards.
 *  - Committed: status from the real-time distribution, 24 h plan strip, next action,
 *    target controls (comfort/eco, nominal temperature, runtime, EV target/ready-by),
 *    Priority now. Mode buttons are hidden so users don't fight the controller.
 *  - Uncommitted: a normal control card for the underlying entity (modes, setpoint,
 *    presets, fan/swing, on/off).
 *
 * Data contract: attributes of sensor.pc_<device>_plan (coordinator.device_attributes).
 * Styling: only Home Assistant theme variables – follows the active (custom) theme.
 * No external dependencies.
 */
const PC_CARD_VERSION = "2.6.1";

const PC_STATUS = {
  running: { label: "Running", color: "var(--success-color, #43a047)", pulse: true },
  lent: { label: "Idle · capacity lent", color: "var(--info-color, #039be5)", pulse: false },
  waiting: { label: "Waiting", color: "var(--secondary-text-color, #9b9b9b)", pulse: false },
  blocked: { label: "Blocked", color: "var(--warning-color, #ffa600)", pulse: true },
  shedding: { label: "Shedding", color: "var(--error-color, #db4437)", pulse: true },
  priority_now: { label: "Priority now", color: "var(--warning-color, #ffa600)", pulse: true },
  manual: { label: "Manual", color: "var(--secondary-text-color, #9b9b9b)", pulse: false },
};

const PC_TYPE_ICON = {
  climate_heating: "mdi:home-thermometer-outline",
  climate_hotwater: "mdi:water-boiler",
  ev_charger: "mdi:ev-station",
  switch: "mdi:power-socket-eu",
};

const PC_MODE_ICON = {
  off: "mdi:power", heat: "mdi:fire", cool: "mdi:snowflake", heat_cool: "mdi:sun-snowflake-variant",
  auto: "mdi:thermostat-auto", dry: "mdi:water-percent", fan_only: "mdi:fan",
  electric: "mdi:lightning-bolt", eco: "mdi:leaf", performance: "mdi:rocket-launch",
  high_demand: "mdi:speedometer", heat_pump: "mdi:heat-pump-outline", gas: "mdi:fire-circle",
  on: "mdi:fire",
};

const pcEsc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pcNum = (v) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v));
const pcHHMM = (d) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
const pcTitle = (s) => String(s || "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

class PowerControllerObjectCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._pending = {};      // optimistic values while a service call is debounced
    this._timers = {};
    this._sig = "";
  }

  static getConfigElement() { return document.createElement("power-controller-object-card-editor"); }

  static getStubConfig(hass) {
    const ent = Object.keys(hass.states).find((e) => e.startsWith("sensor.") && hass.states[e].attributes?.entry_id
      && hass.states[e].attributes?.entities);
    return { entity: ent || "", show_plan: true, show_distribution: true, show_targets: true };
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("Choose the device's plan sensor (sensor.pc_<device>_plan)");
    this._config = { show_plan: true, show_distribution: true, show_targets: true, ...config };
    this._sig = "";
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const sig = this._signature();
    if (sig !== this._sig) { this._sig = sig; this._render(); }
  }

  getCardSize() { return this._attrs()?.committed ? 5 : 4; }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: "auto" }; }

  // ------------------------------------------------------------------ data
  _attrs() { return this._hass?.states[this._config?.entity]?.attributes; }
  _ents() { return this._attrs()?.entities || {}; }
  _controlId() { return this._config.control_entity || this._ents().control; }
  _st(id) { return id ? this._hass.states[id] : undefined; }

  _signature() {
    if (!this._hass || !this._config) return "";
    const ids = new Set([this._config.entity, this._controlId(), ...Object.values(this._ents())]);
    let s = JSON.stringify(this._pending);
    ids.forEach((id) => { const st = id && this._hass.states[id]; if (st) s += `|${id}:${st.last_updated}:${st.state}`; });
    return s + (this._hass.themes?.darkMode ? "d" : "l");
  }

  _val(key, fallback) { return key in this._pending ? this._pending[key] : fallback; }

  // ------------------------------------------------------------------ actions
  _call(domain, service, data) {
    return this._hass.callService(domain, service, data).catch((err) => {
      this._toast(`${domain}.${service} failed: ${err?.message || err}`);
    });
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  _toggle(entityId) { if (entityId) this._call("homeassistant", "toggle", { entity_id: entityId }); }

  _setNumber(entityId, value) {
    const domain = entityId.split(".")[0];
    if (domain === "input_number" || domain === "number") return this._call(domain, "set_value", { entity_id: entityId, value });
    this._toast(`${entityId} cannot be set from the card`);
  }

  // debounced stepper: update optimistic value now, call service after 700 ms of no clicks
  _step(key, current, delta, min, max, step, apply) {
    const base = pcNum(this._val(key, current)) ?? 0;
    const v = Math.round(Math.min(max, Math.max(min, base + delta * step)) / step) * step;
    this._pending[key] = Number(v.toFixed(2));
    clearTimeout(this._timers[key]);
    this._timers[key] = setTimeout(async () => {
      await apply(this._pending[key]);
      setTimeout(() => { delete this._pending[key]; this._sig = ""; if (this._hass) this.hass = this._hass; }, 1500);
    }, 700);
    this._sig = ""; this.hass = this._hass;
  }

  _numLimits(entityId, dMin, dMax, dStep) {
    const a = this._st(entityId)?.attributes || {};
    return { min: pcNum(a.min) ?? dMin, max: pcNum(a.max) ?? dMax, step: pcNum(a.step) ?? dStep };
  }

  // ------------------------------------------------------------------ render helpers
  _stepper(key, value, unit, label, decimals) {
    const v = pcNum(value);
    return `<div class="stp"><button type="button" data-act="step" data-key="${key}" data-d="-1" aria-label="Lower ${pcEsc(label || key)}">−</button>
      <output>${v === null ? "–" : v.toFixed(decimals)} ${unit}${label ? `<small>${pcEsc(label)}</small>` : ""}</output>
      <button type="button" data-act="step" data-key="${key}" data-d="1" aria-label="Raise ${pcEsc(label || key)}">+</button></div>`;
  }

  _select(key, icon, value, options, label) {
    return `<label class="sel"><ha-icon icon="${icon}"></ha-icon><span class="sr">${pcEsc(label)}</span>
      <select data-act="select" data-key="${key}" aria-label="${pcEsc(label)}">${options.map((o) =>
        `<option value="${pcEsc(o)}" ${o === value ? "selected" : ""}>${pcEsc(pcTitle(o))}</option>`).join("")}</select></label>`;
  }

  _segment(key, value, options) {
    return `<div class="seg" role="group" aria-label="${pcEsc(key)}">${options.map((o) =>
      `<button type="button" data-act="seg" data-key="${key}" data-v="${pcEsc(o)}" aria-pressed="${o === value}" title="${pcEsc(pcTitle(o))}" aria-label="${pcEsc(pcTitle(o))}" class="${o === "off" ? "off" : ""}">
        <ha-icon icon="${PC_MODE_ICON[o] || "mdi:circle-medium"}"></ha-icon></button>`).join("")}</div>`;
  }

  _stateLine(a, ctrl) {
    const t = a.device_type;
    const fmt = (st) => (st ? (this._hass.formatEntityState ? this._hass.formatEntityState(st) : st.state) : "");
    if (t === "ev_charger") {
      const soc = pcNum(a.soc);
      const fin = a.finish_by ? pcHHMM(new Date(a.finish_by)) : null;
      return `${soc === null ? "–" : soc.toFixed(0)} % · target ${pcNum(this._val("target_soc", a.target_soc)) ?? "–"} %${fin ? ` by ${fin}` : ""}${a.connected === false ? " · unplugged" : ""}`;
    }
    if (t === "switch") return `${ctrl ? fmt(ctrl) : "–"} · ${(pcNum(a.live_kw) ?? 0).toFixed(1)} kW`;
    const cur = pcNum(ctrl?.attributes?.current_temperature) ?? pcNum(a.current_temp);
    const tgt = pcNum(ctrl?.attributes?.temperature);
    const mode = ctrl ? fmt(ctrl) : "–";
    return `${mode}${cur !== null ? ` · ${cur.toFixed(1)} °C` : ""}${tgt !== null && ctrl?.state !== "off" ? ` → ${tgt.toFixed(1)} °C` : ""}`;
  }

  _distDetail(a) {
    const s = a.ui_status;
    const nxt = a.next_action;
    if (s === "running") return "planned now";
    if (s === "lent") return `reserved ${(pcNum(a.allocated_kw) ?? 0).toFixed(1)} kW`;
    if (s === "waiting") return nxt ? `next ${pcHHMM(new Date(nxt.start))}` : "nothing planned";
    if (s === "blocked") return "planned · waiting for capacity";
    if (s === "shedding") return "cut back to stay within limits";
    if (s === "priority_now") return "overrides plan";
    return "not controlled";
  }

  _distHtml(a) {
    if (!this._config.show_distribution) return "";
    const key = a.committed ? (a.ui_status || "waiting") : "manual";
    const st = PC_STATUS[key] || PC_STATUS.waiting;
    const live = pcNum(a.live_kw) ?? 0, alloc = pcNum(a.allocated_kw) ?? 0, nom = Math.max(0.1, pcNum(a.nominal_kw) ?? 1);
    const kw = key === "manual" || (alloc <= 0 && live <= 0.01) ? "" : `<span class="kw">${live.toFixed(2)} / ${alloc.toFixed(1)} kW</span>`;
    const meter = key === "manual" || alloc <= 0 ? "" :
      `<div class="meter"><i style="width:${Math.min(100, (alloc / nom) * 100)}%;background:color-mix(in srgb, ${st.color} 30%, transparent)"></i><i style="width:${Math.min(100, (live / nom) * 100)}%;background:${st.color}"></i></div>`;
    return `<div style="--s:${st.color}"><div class="dist${st.pulse ? " pulse" : ""}" role="status"><span class="d"></span><b>${st.label}</b><span class="dt">${this._distDetail({ ...a, ui_status: key })}</span>${kw}</div>${meter}</div>`;
  }

  _nextText(a) {
    const n = a.next_action;
    const t = a.device_type;
    const span = n ? `<b>${pcHHMM(new Date(n.start))}–${pcHHMM(new Date(n.end))}</b>` : "";
    const sp = n && pcNum(n.setpoint) !== null ? ` to <b>${Number(n.setpoint).toFixed(1)} °C</b>` : "";
    if (t === "switch") {
      const done = pcNum(a.runtime_today_h) ?? 0, req = pcNum(this._val("runtime", a.runtime_required_h)) ?? 0;
      return `Runtime today <b>${done.toFixed(1)} of ${req.toFixed(0)} h</b>${n ? ` · ${n.running_now ? "running until" : "next"} ${n.running_now ? `<b>${pcHHMM(new Date(n.end))}</b>` : span}` : ""}`;
    }
    if (t === "ev_charger") {
      const fin = a.finish_by ? ` · ready <b>${pcHHMM(new Date(a.finish_by))}</b>` : "";
      if (!n) return `No charging planned${fin}`;
      return `${n.running_now ? "Charging now until" : "Charging"} ${n.running_now ? `<b>${pcHHMM(new Date(n.end))}</b>` : span}${fin}`;
    }
    let leg = "";
    if (t === "climate_hotwater" && Array.isArray(a.legionella_hours) && a.legionella_hours.length && a.plan_start) {
      const d = new Date(new Date(a.plan_start).getTime() + a.legionella_hours[0] * 3600e3);
      if (d > new Date()) leg = ` · legionella <b>${d.toLocaleDateString(undefined, { weekday: "short" })} ${pcHHMM(d)}</b>`;
    }
    if (!n) return `Nothing planned in the next 72 h${leg}`;
    if (n.running_now) return `Heating now until <b>${pcHHMM(new Date(n.end))}</b>${sp}${leg}`;
    return `Next heat ${span}${sp}${leg}`;
  }

  _stripHtml(a) {
    if (!this._config.show_plan || !a.committed) return "";
    const plan = (a.plan_24h || []).map((v) => pcNum(v) ?? 0);
    if (!plan.length) return "";
    const mx = Math.max(...plan, 0.5);
    const start = a.window_start ? new Date(a.window_start) : new Date();
    const labels = [0, 6, 12, 18, 24].map((h) => (h === 0 ? "Now" : pcHHMM(new Date(start.getTime() + h * 3600e3))));
    const bars = plan.map((v, i) => `<i class="${i === 0 ? "now" : ""}" style="height:${Math.max(6, (v / mx) * 100)}%;opacity:${v > 0.05 ? 0.9 : 0.22}"></i>`).join("");
    return `<div class="strip"><div class="bars" aria-hidden="true">${bars}</div><div class="sl">${labels.map((l) => `<span>${l}</span>`).join("")}</div><div class="nxt">${this._nextText(a)}</div></div>`;
  }

  _committedBody(a) {
    const e = this._ents();
    const t = a.device_type;
    let f = "";
    if (this._config.show_targets) {
      if (t === "climate_heating") {
        const parts = [];
        if (e.comfort_temp) parts.push(this._stepper("comfort", this._val("comfort", a.comfort_temp), "°C", "Comfort", 1));
        if (e.eco_temp) parts.push(this._stepper("eco", this._val("eco", a.eco_temp), "°C", "Eco", 1));
        if (parts.length) f = `<div class="${parts.length > 1 ? "row2" : ""}">${parts.join("")}</div>`;
      } else if (t === "climate_hotwater" && e.nominal_temp) {
        f = this._stepper("nominal", this._val("nominal", a.nominal_temp), "°C", "Nominal temperature", 0);
      } else if (t === "switch" && e.runtime_hours) {
        f = this._stepper("runtime", this._val("runtime", a.runtime_required_h), "h", "Required runtime per day", 0);
      } else if (t === "ev_charger") {
        const soc = pcNum(a.soc), tgt = pcNum(this._val("target_soc", a.target_soc)) ?? 80;
        const fin = a.finish_by ? pcHHMM(new Date(a.finish_by)) : "07:00";
        f = `<div><div class="socb"><i style="width:${soc ?? 0}%"></i><em style="left:${tgt}%"></em></div>
             <div class="sl"><span>${soc === null ? "–" : soc.toFixed(0)} % now</span><span>target ${tgt} %</span></div></div>
             <div class="row2">${e.target_soc ? this._stepper("target_soc", tgt, "%", "Target", 0) : ""}
             ${e.finish_by ? `<label class="sel"><ha-icon icon="mdi:clock-check-outline"></ha-icon><span class="sr">Ready by</span><input type="time" data-act="finish" value="${fin}" aria-label="Ready by"></label>` : ""}</div>`;
      }
    }
    const hold = pcNum(a.hold_remaining_s) ?? 0;
    const holdTxt = hold > 0 ? `Anti-cycling ${Math.floor(hold / 60)}:${String(hold % 60).padStart(2, "0")}` : `Plan #${a.plan_priority ?? "–"}`;
    return `${this._stripHtml(a)}${f ? `<div class="feat">${f}</div>` : ""}
      <div class="acts"><button type="button" class="pn" data-act="pnow" aria-pressed="${!!a.priority_now}" ${e.priority_now ? "" : "disabled"}>
      <ha-icon icon="mdi:lightning-bolt"></ha-icon>Priority now</button><span class="hint">${holdTxt}</span></div>`;
  }

  _manualBody(a, ctrl) {
    if (a.device_type === "ev_charger") return `<div class="feat"><div class="hint">The charger is not limited by Power Controller while uncommitted.</div></div>`;
    if (!ctrl) return `<div class="feat"><div class="hint">Control entity not found.</div></div>`;
    const dom = ctrl.entity_id.split(".")[0];
    const at = ctrl.attributes || {};
    const f = [];
    if (dom === "climate") {
      if (Array.isArray(at.hvac_modes) && at.hvac_modes.length) f.push(this._segment("hvac", ctrl.state, at.hvac_modes));
      if (pcNum(at.temperature) !== null) f.push(this._stepper("ctemp", this._val("ctemp", at.temperature), "°C", "", 1));
      if (Array.isArray(at.preset_modes) && at.preset_modes.length) f.push(this._select("preset", "mdi:sofa-outline", at.preset_mode, at.preset_modes, "Preset"));
      if (Array.isArray(at.fan_modes) && at.fan_modes.length) f.push(this._select("fan", "mdi:fan", at.fan_mode, at.fan_modes, "Fan mode"));
      if (Array.isArray(at.swing_modes) && at.swing_modes.length) f.push(this._select("swing", "mdi:arrow-oscillating", at.swing_mode, at.swing_modes, "Swing mode"));
    } else if (dom === "water_heater") {
      const ops = Array.isArray(at.operation_list) ? at.operation_list : [];
      if (ops.length && ops.length <= 4) f.push(this._segment("op", at.operation_mode || ctrl.state, ops));
      else if (ops.length) f.push(this._select("op", "mdi:water-boiler", at.operation_mode || ctrl.state, ops, "Operation"));
      if (pcNum(at.temperature) !== null) f.push(this._stepper("wtemp", this._val("wtemp", at.temperature), "°C", "", 0));
    } else {
      f.push(this._segment("onoff", ctrl.state === "on" ? "on" : "off", ["off", "on"]));
    }
    return `<div class="feat">${f.join("")}</div>`;
  }

  // ------------------------------------------------------------------ render
  _render() {
    if (!this._config || !this._hass) return;
    const root = this.shadowRoot;
    const focusKey = root.activeElement?.dataset ? `${root.activeElement.dataset.act}|${root.activeElement.dataset.key || ""}|${root.activeElement.dataset.d || root.activeElement.dataset.v || ""}` : null;
    const a = this._attrs();
    if (!a || !a.entry_id) {
      root.innerHTML = `${this._css()}<ha-card><div class="warn">${pcEsc(this._config.entity)} is not a Power Controller device plan sensor.<br>Choose <b>sensor.pc_&lt;device&gt;_plan</b>.</div></ha-card>`;
      return;
    }
    const ctrl = this._st(this._controlId());
    const name = this._config.name || a.name || ctrl?.attributes?.friendly_name || this._config.entity;
    const icon = this._config.icon || ctrl?.attributes?.icon || PC_TYPE_ICON[a.device_type] || "mdi:flash";
    const active = (pcNum(a.live_kw) ?? 0) > 0.05 || (ctrl && !["off", "unavailable", "unknown"].includes(ctrl.state));
    const color = a.device_type === "ev_charger" ? "var(--success-color, #43a047)" :
      a.device_type === "switch" ? "var(--warning-color, #ffa600)" : "var(--state-climate-heat-color, #ff8100)";
    const committed = !!a.committed;
    root.innerHTML = `${this._css()}
      <ha-card style="--c:${color}">
        <div class="hdr">
          <button type="button" class="ico${active ? " act" : ""}" data-act="more" aria-label="Details for ${pcEsc(name)}"><ha-icon icon="${pcEsc(icon)}"></ha-icon></button>
          <div class="nm" data-act="more"><b>${pcEsc(name)}</b><span>${pcEsc(this._stateLine(a, ctrl))}</span></div>
          <button type="button" class="badge" data-act="commit" aria-pressed="${committed}" aria-label="${committed ? "Committed" : "Uncommitted"}"
            title="${committed ? "Controlled by Power Controller – tap to release" : "Not controlled – tap to let Power Controller manage it"}" ${this._ents().committed ? "" : "disabled"}>
            <ha-icon icon="mdi:chart-timeline-variant-shimmer"></ha-icon><span class="bt">${committed ? "Committed" : "Uncommitted"}</span></button>
        </div>
        ${this._distHtml(a)}
        ${committed ? this._committedBody(a) : this._manualBody(a, ctrl)}
      </ha-card>`;
    if (focusKey) {
      const [act, key, dv] = focusKey.split("|");
      const sel = `[data-act="${act}"]${key ? `[data-key="${key}"]` : ""}${dv ? `[data-d="${dv}"],[data-act="${act}"]${key ? `[data-key="${key}"]` : ""}[data-v="${dv}"]` : ""}`;
      try { root.querySelector(sel)?.focus(); } catch (_) { /* ignore */ }
    }
    this._bind();
  }

  _bind() {
    const root = this.shadowRoot;
    const a = this._attrs();
    const e = this._ents();
    const ctrl = this._st(this._controlId());
    root.querySelectorAll("[data-act]").forEach((el) => {
      const act = el.dataset.act;
      if (el.tagName === "SELECT") {
        el.addEventListener("change", () => this._onSelect(el.dataset.key, el.value, ctrl));
        return;
      }
      if (act === "finish") {
        el.addEventListener("change", () => this._setFinish(el.value, e.finish_by));
        return;
      }
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (act === "more") this._moreInfo(this._controlId() || this._config.entity);
        else if (act === "commit") this._toggle(e.committed);
        else if (act === "pnow") this._toggle(e.priority_now);
        else if (act === "seg") this._onSegment(el.dataset.key, el.dataset.v, ctrl);
        else if (act === "step") this._onStep(el.dataset.key, Number(el.dataset.d), a, ctrl);
      });
    });
  }

  _onStep(key, d, a, ctrl) {
    const e = this._ents();
    if (key === "comfort") { const l = this._numLimits(e.comfort_temp, 10, 28, 0.5); this._step(key, a.comfort_temp, d, l.min, l.max, l.step, (v) => this._setNumber(e.comfort_temp, v)); }
    else if (key === "eco") { const l = this._numLimits(e.eco_temp, 5, 25, 0.5); this._step(key, a.eco_temp, d, l.min, l.max, l.step, (v) => this._setNumber(e.eco_temp, v)); }
    else if (key === "nominal") { const l = this._numLimits(e.nominal_temp, 40, 80, 1); this._step(key, a.nominal_temp, d, l.min, l.max, l.step, (v) => this._setNumber(e.nominal_temp, v)); }
    else if (key === "runtime") { const l = this._numLimits(e.runtime_hours, 0, 24, 1); this._step(key, a.runtime_required_h, d, l.min, l.max, Math.max(1, l.step), (v) => this._setNumber(e.runtime_hours, v)); }
    else if (key === "target_soc") { const l = this._numLimits(e.target_soc, 10, 100, 1); this._step(key, a.target_soc, d, l.min, l.max, 5, (v) => this._setNumber(e.target_soc, v)); }
    else if (key === "ctemp" && ctrl) {
      const at = ctrl.attributes;
      this._step(key, at.temperature, d, pcNum(at.min_temp) ?? 7, pcNum(at.max_temp) ?? 35, pcNum(at.target_temp_step) ?? 0.5,
        (v) => this._call("climate", "set_temperature", { entity_id: ctrl.entity_id, temperature: v }));
    } else if (key === "wtemp" && ctrl) {
      const at = ctrl.attributes;
      this._step(key, at.temperature, d, pcNum(at.min_temp) ?? 30, pcNum(at.max_temp) ?? 80, 1,
        (v) => this._call("water_heater", "set_temperature", { entity_id: ctrl.entity_id, temperature: v }));
    }
  }

  _onSegment(key, v, ctrl) {
    if (!ctrl) return;
    if (key === "hvac") this._call("climate", "set_hvac_mode", { entity_id: ctrl.entity_id, hvac_mode: v });
    else if (key === "op") this._call("water_heater", "set_operation_mode", { entity_id: ctrl.entity_id, operation_mode: v });
    else if (key === "onoff") this._call("homeassistant", v === "on" ? "turn_on" : "turn_off", { entity_id: ctrl.entity_id });
  }

  _onSelect(key, v, ctrl) {
    if (!ctrl) return;
    if (key === "preset") this._call("climate", "set_preset_mode", { entity_id: ctrl.entity_id, preset_mode: v });
    else if (key === "fan") this._call("climate", "set_fan_mode", { entity_id: ctrl.entity_id, fan_mode: v });
    else if (key === "swing") this._call("climate", "set_swing_mode", { entity_id: ctrl.entity_id, swing_mode: v });
    else if (key === "op") this._call("water_heater", "set_operation_mode", { entity_id: ctrl.entity_id, operation_mode: v });
  }

  _setFinish(hhmm, entityId) {
    if (!entityId || !/^\d{2}:\d{2}$/.test(hhmm)) return;
    const [h, m] = hhmm.split(":").map(Number);
    const d = new Date(); d.setSeconds(0, 0); d.setHours(h, m);
    if (d <= new Date()) d.setDate(d.getDate() + 1);   // next occurrence
    const p = (x) => String(x).padStart(2, "0");
    this._call("datetime", "set_value", { entity_id: entityId, datetime: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(h)}:${p(m)}:00` });
  }

  _css() {
    return `<style>
      :host{display:block}
      ha-card{padding:12px;container-type:inline-size;--ink:var(--primary-text-color);--mut:var(--secondary-text-color);
        --soft:color-mix(in srgb, var(--primary-text-color) 7%, transparent);--soft2:color-mix(in srgb, var(--primary-text-color) 11%, transparent);
        --r:var(--ha-card-border-radius, 12px)}
      button{font-family:inherit}
      button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}
      .hdr{display:flex;align-items:center;gap:12px;min-width:0}
      .ico{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;flex:none;background:var(--soft);color:var(--mut);border:0;cursor:pointer;padding:0}
      .ico.act{background:color-mix(in srgb, var(--c) 20%, transparent);color:var(--c)}
      .nm{min-width:0;flex:1;cursor:pointer}
      .nm b{display:block;font-size:14px;font-weight:600;line-height:1.25;overflow-wrap:anywhere;color:var(--ink)}
      .nm span{display:block;font-size:12.5px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}
      .badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;border-radius:999px;padding:5px 10px 5px 8px;border:1px solid var(--divider-color);background:var(--soft);color:var(--mut);cursor:pointer;flex:none;white-space:nowrap;--mdc-icon-size:16px}
      .badge[aria-pressed=true]{color:var(--success-color, #43a047);background:color-mix(in srgb, var(--success-color, #43a047) 14%, transparent);border-color:color-mix(in srgb, var(--success-color, #43a047) 40%, transparent)}
      .badge:disabled{cursor:default;opacity:.6}
      .dist{display:flex;align-items:center;gap:8px;margin-top:10px;font-size:12.5px;padding:7px 10px;border-radius:calc(var(--r) * .7);background:var(--soft);min-width:0}
      .dist .d{width:8px;height:8px;border-radius:50%;background:var(--s);flex:none;box-shadow:0 0 0 3px color-mix(in srgb, var(--s) 25%, transparent)}
      .dist.pulse .d{animation:pcp 1.6s ease-in-out infinite}
      @keyframes pcp{50%{box-shadow:0 0 0 6px color-mix(in srgb, var(--s) 5%, transparent)}}
      @media (prefers-reduced-motion:reduce){.dist.pulse .d{animation:none}}
      .dist b{color:var(--s);font-weight:600;white-space:nowrap}
      .dist .dt{color:var(--mut);flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .dist .kw{font-variant-numeric:tabular-nums;color:var(--ink);white-space:nowrap;font-weight:500}
      .meter{height:4px;border-radius:4px;background:var(--soft2);margin-top:6px;overflow:hidden;position:relative}
      .meter i{position:absolute;top:0;bottom:0;left:0;border-radius:4px}
      .strip{margin-top:10px}
      .bars{display:flex;align-items:flex-end;gap:2px;height:34px}
      .bars i{flex:1;border-radius:2px 2px 0 0;min-height:2px;background:var(--c)}
      .bars i.now{box-shadow:0 0 0 1.5px var(--ink)}
      .sl{display:flex;justify-content:space-between;font-size:10.5px;color:var(--mut);margin-top:3px}
      .nxt,.hint{font-size:12px;color:var(--mut)}
      .nxt{margin-top:6px}.nxt b{color:var(--ink);font-weight:600}
      .feat{display:flex;flex-direction:column;gap:8px;margin-top:10px}
      .seg{display:flex;gap:6px;background:var(--soft);border-radius:calc(var(--r) * .9)}
      .seg button{flex:1;height:44px;border:0;border-radius:calc(var(--r) * .9);background:transparent;color:var(--ink);cursor:pointer;display:grid;place-items:center}
      .seg button[aria-pressed=true]{background:var(--c);color:var(--text-primary-color, #fff)}
      .seg button[aria-pressed=true].off{background:color-mix(in srgb, var(--ink) 40%, transparent)}
      .stp{display:flex;align-items:center;height:44px;border-radius:calc(var(--r) * .9);background:var(--soft);padding:0 4px}
      .stp button{width:40px;height:40px;border:0;background:transparent;color:var(--ink);font-size:20px;cursor:pointer;border-radius:10px}
      .stp output{flex:1;text-align:center;font-size:14px;color:var(--ink);font-variant-numeric:tabular-nums}
      .stp output small{display:block;font-size:10.5px;color:var(--mut);line-height:1.1}
      .sel{display:flex;align-items:center;gap:10px;height:44px;border-radius:calc(var(--r) * .9);background:var(--soft);padding:0 12px;color:var(--mut);--mdc-icon-size:18px}
      .sel select,.sel input{flex:1;border:0;background:transparent;font:inherit;font-size:14px;padding:0;color:var(--ink);min-width:0;color-scheme:inherit}
      .sel select{appearance:none;cursor:pointer}
      .sel select option{background:var(--card-background-color);color:var(--ink)}
      .sel:has(select)::after{content:'';border:5px solid transparent;border-top-color:var(--mut);margin-top:5px}
      .row2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
      .acts{display:flex;gap:8px;align-items:center;justify-content:space-between;margin-top:10px;flex-wrap:wrap}
      .pn{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;border-radius:999px;padding:6px 12px;cursor:pointer;border:1px solid var(--divider-color);background:transparent;color:var(--mut);--mdc-icon-size:14px}
      .pn[aria-pressed=true]{background:var(--warning-color, #ffa600);border-color:var(--warning-color, #ffa600);color:#fff}
      .pn:disabled{opacity:.5;cursor:default}
      .socb{height:10px;border-radius:6px;background:var(--soft2);position:relative;overflow:hidden;margin-top:2px}
      .socb i{position:absolute;top:0;bottom:0;left:0;background:var(--c);border-radius:6px}
      .socb em{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--ink)}
      .warn{padding:8px;color:var(--error-color);font-size:13px}
      .sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
      @container (max-width:370px){.badge .bt{display:none}.badge{padding:6px}}
    </style>`;
  }
}

// ---------------------------------------------------------------------- editor
class PowerControllerObjectCardEditor extends HTMLElement {
  setConfig(config) { this._config = { ...config }; this._render(); }
  set hass(hass) { this._hass = hass; if (this._form) this._form.hass = hass; else this._render(); }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => ({
        entity: "Device plan sensor (sensor.pc_<device>_plan)", control_entity: "Control entity (optional, auto-detected)",
        name: "Name (optional)", icon: "Icon (optional)", show_plan: "Show 24-hour plan strip",
        show_distribution: "Show distribution status", show_targets: "Show target controls when committed",
      }[s.name] || s.name);
      this._form.addEventListener("value-changed", (ev) => {
        this._config = ev.detail.value;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = [
      { name: "entity", required: true, selector: { entity: { filter: { integration: "power_controller", domain: "sensor" } } } },
      { name: "control_entity", selector: { entity: { filter: [{ domain: ["climate", "water_heater", "switch", "input_boolean"] }] } } },
      { type: "grid", name: "", schema: [{ name: "name", selector: { text: {} } }, { name: "icon", selector: { icon: {} } }] },
      { name: "show_plan", selector: { boolean: {} } },
      { name: "show_distribution", selector: { boolean: {} } },
      { name: "show_targets", selector: { boolean: {} } },
    ];
    this._form.data = { show_plan: true, show_distribution: true, show_targets: true, ...this._config };
  }
}

if (!customElements.get("power-controller-object-card")) {
  customElements.define("power-controller-object-card", PowerControllerObjectCard);
  customElements.define("power-controller-object-card-editor", PowerControllerObjectCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "power-controller-object-card",
    name: "Power Controller object",
    description: "Controls one Power Controller device: committed/uncommitted, distribution status, 24 h plan and targets.",
    preview: true,
    documentationURL: "https://github.com/Kjelljk/Home-Energy-Management-System",
  });
  console.info(`%c POWER-CONTROLLER-OBJECT-CARD %c ${PC_CARD_VERSION} `, "background:#03a9f4;color:#fff", "");
}
