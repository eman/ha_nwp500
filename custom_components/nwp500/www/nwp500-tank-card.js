/**
 * NWP500 Tank Forecast Card
 *
 * The tank's upper and lower temperatures: as recorded over the last
 * hours (solid) and as the hot water simulator models them over the next
 * day (dashed). Running, the model shows them rising and when the tank
 * comes to temperature; idle, it shows them falling and when the
 * compressor next starts.
 *
 * The recorded side is Home Assistant's history of the heater's two tank
 * sensors. The modelled side is the integration's
 * `nwp500/simulator/subscribe_path` websocket subscription, which sends
 * the path again with each new simulation. Needs the simulator on.
 */

const TANK_CARD_VERSION = '1.0.0';

// Recorded history is read again this often, and live states appended
// between reads.
const HISTORY_REFRESH_MS = 5 * 60 * 1000;
// A refused subscription is tried again, from this wait doubling to the
// last: the simulator starts in the background after the integration, so
// a card opened at start-up can ask before it runs.
const RETRY_FIRST_MS = 5 * 1000;
const RETRY_LAST_MS = 5 * 60 * 1000;

// Why a heating ends short of its target, as the path's `stop` says.
const STOP_LABELS = {
  cap: 'TOU cap stops it',
  mode: 'mode change stops it',
  write: 'setpoint change stops it',
};

const COLORS = {
  upper: '#e8743b',
  lower: '#3b8fe8',
  setpoint: 'var(--secondary-text-color, #888)',
  heating: 'rgba(232, 116, 59, 0.12)',
  now: 'var(--primary-text-color, #222)',
};

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[c]);
}

/** A time as the user's locale shows a clock time. */
function clock(hass, date) {
  const locale = hass?.locale?.language || navigator.language;
  return date.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' });
}

/** A time ahead, said as a clock time, with the day when it isn't today. */
function when(hass, date) {
  const now = new Date();
  const locale = hass?.locale?.language || navigator.language;
  const sameDay = date.toDateString() === now.toDateString();
  const day = sameDay ? '' : `${date.toLocaleDateString(locale, { weekday: 'short' })} `;
  return `${day}${clock(hass, date)}`;
}

/** `value` in `from`'s unit, as `to` reads it: °F and °C. */
function convert(value, from, to) {
  if (!from || !to || from === to) return value;
  if (from === '°F' && to === '°C') return (value - 32) / 1.8;
  if (from === '°C' && to === '°F') return value * 1.8 + 32;
  return value;
}

/** The heater's tank sensors, found on the same device as `entityId`. */
function tankSensors(hass, entityId) {
  const entities = hass.entities || {};
  const device = entities[entityId]?.device_id;
  const found = { upper: null, lower: null };
  if (!device) return found;
  for (const [id, entry] of Object.entries(entities)) {
    if (entry.device_id !== device || entry.platform !== 'nwp500') continue;
    const key = entry.translation_key || '';
    if (key === 'tank_upper_temperature' || id.endsWith('_tank_upper_temperature')) found.upper = id;
    if (key === 'tank_lower_temperature' || id.endsWith('_tank_lower_temperature')) found.lower = id;
  }
  return found;
}

class NWP500TankCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass = null;
    this._path = null;
    this._history = { upper: [], lower: [] };
    // Changes seen live since the last history read, per sensor: the
    // recorder has them too, but only the next read brings them.
    this._live = { upper: [], lower: [] };
    this._historyAt = 0;
    this._historyToken = 0;
    this._unsubscribe = null;
    this._subscribedTo = null;
    this._error = null;
    this._retryMs = RETRY_FIRST_MS;
    this._retryTimer = null;
    // Each subscribe's token: a late answer to an earlier one is dropped.
    this._token = 0;
  }

  static getConfigElement() {
    return document.createElement('nwp500-tank-card-editor');
  }

  static getStubConfig(hass) {
    const heater = Object.keys(hass?.states || {}).find(
      (id) => id.startsWith('water_heater.') && hass.entities?.[id]?.platform === 'nwp500'
    );
    return { entity: heater || 'water_heater.navien_nwp500' };
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error('Entity (the water heater) is required');
    }
    this._config = { hours_back: 6, hours_ahead: 24, ...config };
    this._resubscribe();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first || this._subscribedTo !== this._config.entity) this._resubscribe();
    if (Date.now() - this._historyAt > HISTORY_REFRESH_MS) this._fetchHistory();
    // Home Assistant sets hass on every state change anywhere: draw again
    // only when the heater or its tank sensors changed.
    const { upper, lower } = this._sensors();
    const seen = [this._config.entity, upper, lower].map((id) => hass.states[id]);
    if (first || seen.some((s, i) => s !== this._seen?.[i])) {
      this._seen = seen;
      this._noteLive();
      this._render();
    }
  }

  connectedCallback() {
    if (this._hass) this._resubscribe();
  }

  disconnectedCallback() {
    this._unsubscribeNow();
  }

  getCardSize() {
    return 5;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: 5, min_rows: 4 };
  }

  _sensors() {
    const found = this._hass ? tankSensors(this._hass, this._config.entity) : {};
    return {
      upper: this._config.upper_entity || found.upper,
      lower: this._config.lower_entity || found.lower,
    };
  }

  _unsubscribeNow() {
    this._token += 1;
    clearTimeout(this._retryTimer);
    this._retryTimer = null;
    if (this._unsubscribe) {
      this._unsubscribe.then((unsub) => unsub && unsub()).catch(() => {});
    }
    this._unsubscribe = null;
    this._subscribedTo = null;
  }

  _resubscribe() {
    if (!this._hass || !this._config.entity || !this.isConnected) return;
    this._unsubscribeNow();
    this._subscribedTo = this._config.entity;
    this._error = null;
    const token = ++this._token;
    this._unsubscribe = this._hass.connection
      .subscribeMessage(
        (path) => {
          if (token !== this._token) return;
          this._retryMs = RETRY_FIRST_MS;
          this._path = path;
          this._render();
        },
        { type: 'nwp500/simulator/subscribe_path', entity_id: this._config.entity }
      )
      .catch((err) => {
        if (token !== this._token) return null;
        this._error = err?.message || String(err);
        this._path = null;
        this._render();
        // Not running yet, or a heater not loaded yet: ask again later.
        // A heater the user may not read is not asked about again.
        if (err?.code !== 'unauthorized') {
          const entity = this._config.entity;
          this._retryTimer = setTimeout(() => {
            if (this._config.entity === entity) this._resubscribe();
          }, this._retryMs);
          this._retryMs = Math.min(this._retryMs * 2, RETRY_LAST_MS);
        }
        return null;
      });
    this._historyAt = 0;
    this._live = { upper: [], lower: [] };
    this._fetchHistory();
  }

  async _fetchHistory() {
    if (!this._hass) return;
    const { upper, lower } = this._sensors();
    const ids = [upper, lower].filter(Boolean);
    this._historyAt = Date.now();
    // A read answered after a newer one began (other sensors, say) is
    // dropped, success or failure.
    const token = ++this._historyToken;
    if (!ids.length) return;
    const end = new Date();
    const start = new Date(end.getTime() - this._config.hours_back * 3600 * 1000);
    try {
      const result = await this._hass.callWS({
        type: 'history/history_during_period',
        start_time: start.toISOString(),
        end_time: end.toISOString(),
        entity_ids: ids,
        minimal_response: true,
        no_attributes: true,
      });
      const series = (id) =>
        (result[id] || [])
          // Unavailable or unknown is a gap (null), not a reading.
          .map((row) => {
            const v = Number(row.s);
            return { t: (row.lu ?? row.lc) * 1000, v: Number.isFinite(v) ? v : null };
          });
      if (token !== this._historyToken) return;
      this._history = { upper: upper ? series(upper) : [], lower: lower ? series(lower) : [] };
      // What the read brought needs no live copy. Only to its last row:
      // a change the recorder hadn't committed yet stays live.
      for (const key of ['upper', 'lower']) {
        const rows = this._history[key];
        const covered = rows.length ? rows[rows.length - 1].t : -Infinity;
        this._live[key] = this._live[key].filter((p) => p.t > covered);
      }
      this._render();
    } catch (err) {
      if (token !== this._historyToken) return;
      // History is optional: the model still draws.
      this._history = { upper: [], lower: [] };
    }
  }

  // Keep each change of the tank sensors seen live, by when it changed.
  _noteLive() {
    const ids = this._sensors();
    for (const key of ['upper', 'lower']) {
      const state = ids[key] ? this._hass.states[ids[key]] : null;
      if (!state) continue;
      const n = Number(state.state);
      const v = Number.isFinite(n) ? n : null;
      const t = Date.parse(state.last_changed);
      const live = this._live[key];
      const last = live.length ? live[live.length - 1] : null;
      if (!last || (t > last.t && v !== last.v)) live.push({ t, v });
      // Bounded while history reads fail: none older than the chart shows.
      const oldest = Date.now() - this._config.hours_back * 3600 * 1000;
      while (live.length > 1 && live[1].t <= oldest) live.shift();
    }
  }

  /**
   * The recorded series from `t0`, in `unit`, with the live state appended.
   *
   * A recorded value holds until the next: the last before `t0` is the
   * value at `t0`. Each sensor is converted from its own unit, which an
   * entity's settings can make other than Home Assistant's.
   */
  _recorded(key, entityId, t0, unit) {
    const state = entityId ? this._hass.states[entityId] : null;
    const from = state?.attributes?.unit_of_measurement;
    const points = [];
    const recorded = this._history[key] || [];
    const lastRecorded = recorded.length ? recorded[recorded.length - 1].t : -Infinity;
    // The read's, then the changes seen live since, each held to the next.
    for (const p of [...recorded, ...(this._live[key] || []).filter((q) => q.t > lastRecorded)]) {
      if (p.t <= t0) points[0] = { t: t0, v: p.v };
      else points.push(p);
    }
    // The live state at now; an unavailable probe is a gap to now.
    const v = state ? Number(state.state) : NaN;
    points.push({ t: Date.now(), v: Number.isFinite(v) ? v : null });
    return points.map((p) => ({ t: p.t, v: p.v === null ? null : convert(p.v, from, unit) }));
  }

  _status(path) {
    const hass = this._hass;
    if (this._error) return { text: this._error, kind: 'error' };
    if (!path) return { text: 'Waiting for the simulator…', kind: 'muted' };
    if (path.error) return { text: 'The simulation failed; the log says why.', kind: 'error' };
    if (path.waiting === 'simulator_off') return { text: 'The hot water simulator is off.', kind: 'muted' };
    if (path.waiting) return { text: 'Waiting for the heater to report.', kind: 'muted' };
    // How the heating it is in ends: at temperature, or stopped short.
    const ending = () => {
      if (path.ready_at) return ` · at temperature by ${when(hass, new Date(path.ready_at))}`;
      if (path.heating_end) {
        const why = STOP_LABELS[path.stop] || 'it stops';
        return ` · ${why} at ${when(hass, new Date(path.heating_end))}, short of temperature`;
      }
      return ' · not at temperature within the forecast';
    };
    if (path.compressor_running) return { text: `Heat pump running${ending()}`, kind: 'heating' };
    if (path.state === 'heating') return { text: `Element heating${ending()}`, kind: 'heating' };
    if (path.state === 'disabled') return { text: 'Heating is off (Vacation or powered off)', kind: 'muted' };
    // A start at the walk's own start is now: the heater about to report it.
    if (path.compressor_start && Date.parse(path.compressor_start) <= Date.parse(path.computed_at)) {
      return { text: 'Heat pump starting now', kind: 'heating' };
    }
    if (path.compressor_start) {
      return { text: `Heat pump off · next start ${when(hass, new Date(path.compressor_start))}`, kind: 'idle' };
    }
    if (path.heating_start) {
      return { text: `Next heating ${when(hass, new Date(path.heating_start))}`, kind: 'idle' };
    }
    return { text: `No heating within the next ${this._config.hours_ahead} hours`, kind: 'idle' };
  }

  // What the hot water is enough for now, and when a bath is ready if
  // it isn't: "Hot water for 1 bath and 1 refill", "… · bath by 7:10 PM".
  _hotWater(path) {
    const hw = path && !path.error && !path.waiting ? path.hot_water : null;
    if (!hw) return '';
    const bath = !hw.bath_ready && hw.bath_ready_at
      ? ` · a bath by ${when(this._hass, new Date(hw.bath_ready_at))}`
      : '';
    return `Hot water for ${hw.summary.charAt(0).toLowerCase()}${hw.summary.slice(1)}${bath}`;
  }

  _render() {
    if (!this._hass) return;
    const hass = this._hass;
    const stateObj = hass.states[this._config.entity];
    const name = this._config.name || stateObj?.attributes?.friendly_name || 'Water heater';
    // A heater that doesn't report has no forecast to show: its last
    // path would claim what it can't know.
    const heaterDown = !stateObj || stateObj.state === 'unavailable';
    const path = heaterDown ? null : this._path;
    const status = heaterDown
      ? { text: 'The water heater is unavailable.', kind: 'muted' }
      : this._status(path);
    const hotWater = this._hotWater(path);
    const unit = path?.unit || hass.config.unit_system.temperature;
    const { upper: upperId, lower: lowerId } = this._sensors();

    const now = Date.now();
    const t0 = now - this._config.hours_back * 3600 * 1000;
    const t1 = now + this._config.hours_ahead * 3600 * 1000;

    const modelled = (path?.points || [])
      .map((p) => ({ t: Date.parse(p.at), upper: p.upper, lower: p.lower, setpoint: p.setpoint, heating: p.heating }))
      .filter((p) => p.t <= t1);
    const recUpper = this._recorded('upper', upperId, t0, unit);
    const recLower = this._recorded('lower', lowerId, t0, unit);

    const values = [
      ...recUpper.map((p) => p.v ?? NaN),
      ...recLower.map((p) => p.v ?? NaN),
      ...modelled.flatMap((p) => [p.upper, p.lower, p.setpoint]),
    ].filter(Number.isFinite);
    if (!values.length) values.push(unit === '°C' ? 40 : 100, unit === '°C' ? 60 : 140);
    let vMin = Math.min(...values);
    let vMax = Math.max(...values);
    const pad = Math.max((vMax - vMin) * 0.1, unit === '°C' ? 1 : 2);
    vMin = Math.floor(vMin - pad);
    vMax = Math.ceil(vMax + pad);

    const W = 600;
    const H = 240;
    const L = 40;
    const R = 12;
    const T = 10;
    const B = 26;
    const x = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
    const y = (v) => T + (1 - (v - vMin) / (vMax - vMin)) * (H - T - B);
    const line = (pts, key) =>
      pts
        .filter((p) => Number.isFinite(p[key]))
        .map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p[key]).toFixed(1)}`)
        .join(' ');
    // A recorded value holds until the next: drawn as steps.
    // A gap (null) ends the line where it starts; the next reading
    // begins a new one.
    const steps = (pts) => {
      let d = '';
      let drawing = false;
      pts.forEach((p) => {
        const px = x(p.t).toFixed(1);
        if (p.v === null) {
          if (drawing) d += ` H${px}`;
          drawing = false;
          return;
        }
        const py = y(p.v).toFixed(1);
        d += drawing ? ` H${px} V${py}` : `${d ? ' ' : ''}M${px},${py}`;
        drawing = true;
      });
      return d;
    };

    // Heating spans along the model.
    const spans = [];
    for (let i = 1; i < modelled.length; i += 1) {
      if (modelled[i].heating) {
        spans.push(`<rect x="${x(modelled[i - 1].t).toFixed(1)}" y="${T}" width="${Math.max(0, x(modelled[i].t) - x(modelled[i - 1].t)).toFixed(1)}" height="${H - T - B}" fill="${COLORS.heating}" />`);
      }
    }

    // Axis ticks: every 3 hours on the clock, every 2 degrees' worth.
    const ticks = [];
    const first = new Date(t0);
    first.setMinutes(0, 0, 0);
    for (let t = first.getTime(); t <= t1; t += 3600 * 1000) {
      const d = new Date(t);
      if (t < t0 || d.getHours() % 3) continue;
      const label = d.getHours() === 0
        ? d.toLocaleDateString(hass.locale?.language || navigator.language, { weekday: 'short' })
        : clock(hass, d).replace(':00', '');
      ticks.push(`<line x1="${x(t)}" x2="${x(t)}" y1="${T}" y2="${H - B}" class="grid" /><text x="${x(t)}" y="${H - 8}" class="tick" text-anchor="middle">${esc(label)}</text>`);
    }
    const ySteps = 4;
    for (let i = 0; i <= ySteps; i += 1) {
      const v = vMin + ((vMax - vMin) * i) / ySteps;
      ticks.push(`<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid" /><text x="${L - 6}" y="${y(v) + 4}" class="tick" text-anchor="end">${Math.round(v)}°</text>`);
    }

    // A label's row: the second of two markers close together sits below.
    const marker = (iso, label, color, row = 0) => {
      if (!iso) return '';
      const t = Date.parse(iso);
      if (!(t >= t0 && t <= t1)) return '';
      const px = x(t);
      const anchor = px > W - 90 ? 'end' : 'start';
      const dx = anchor === 'end' ? -4 : 4;
      return `<line x1="${px}" x2="${px}" y1="${T}" y2="${H - B}" stroke="${color}" stroke-width="1.5" stroke-dasharray="2 3" />
        <text x="${px + dx}" y="${T + 12 + row * 13}" class="marker" fill="${color}" text-anchor="${anchor}">${esc(label)}</text>`;
    };
    // The heating's end: at temperature, or what stops it short.
    const endAt = path?.ready_at || path?.heating_end;
    const readyMarker = marker(
      endAt,
      path?.ready_at ? 'at temperature' : STOP_LABELS[path?.stop] || 'heating stops',
      COLORS.upper,
    );
    const startAt = path && !path.compressor_running ? path.compressor_start : null;
    const close = startAt && endAt
      && Math.abs(x(Date.parse(startAt)) - x(Date.parse(endAt))) < 120;
    const startMarker = marker(startAt, 'compressor starts', COLORS.lower, close ? 1 : 0);

    const svg = `
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Tank temperatures, recorded and modelled">
        ${spans.join('')}
        ${ticks.join('')}
        <path d="${line(modelled, 'setpoint')}" stroke="${COLORS.setpoint}" stroke-width="1" stroke-dasharray="1 4" fill="none" />
        <path d="${steps(recUpper)}" stroke="${COLORS.upper}" stroke-width="2" fill="none" />
        <path d="${steps(recLower)}" stroke="${COLORS.lower}" stroke-width="2" fill="none" />
        <path d="${line(modelled, 'upper')}" stroke="${COLORS.upper}" stroke-width="2" stroke-dasharray="6 4" fill="none" />
        <path d="${line(modelled, 'lower')}" stroke="${COLORS.lower}" stroke-width="2" stroke-dasharray="6 4" fill="none" />
        <line x1="${x(now)}" x2="${x(now)}" y1="${T}" y2="${H - B}" stroke="${COLORS.now}" stroke-width="1" opacity="0.5" />
        ${readyMarker}
        ${startMarker}
      </svg>`;

    const missing = !upperId || !lowerId
      ? '<div class="note">The tank sensors weren\'t found on this heater; set upper_entity and lower_entity to show their history.</div>'
      : '';

    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card>
        <div class="header">
          <div class="name">${esc(name)}</div>
          <div class="status ${status.kind}">${esc(status.text)}</div>
          ${hotWater ? `<div class="capacity">${esc(hotWater)}</div>` : ''}
        </div>
        <div class="chart">${svg}</div>
        <div class="legend">
          <span><i style="background:${COLORS.upper}"></i>Upper</span>
          <span><i style="background:${COLORS.lower}"></i>Lower</span>
          <span><i class="dash"></i>Modelled</span>
          <span><i class="heat"></i>Heating</span>
          <span class="unit">${esc(unit)}</span>
        </div>
        ${missing}
      </ha-card>`;
  }

  _styles() {
    return `
      :host { display: block; }
      ha-card { padding: 12px 12px 8px; }
      .header { display: flex; flex-direction: column; gap: 2px; margin-bottom: 6px; }
      .name { font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
      .status { font-size: 13px; color: var(--secondary-text-color); }
      .status.heating { color: ${COLORS.upper}; }
      .status.idle { color: var(--primary-text-color); }
      .status.error { color: var(--error-color, #db4437); }
      .capacity { font-size: 13px; color: var(--primary-text-color); }
      .chart svg { width: 100%; height: 200px; display: block; }
      .grid { stroke: var(--divider-color, #e0e0e0); stroke-width: 0.5; }
      .tick { font-size: 10px; fill: var(--secondary-text-color, #888); }
      .marker { font-size: 10px; font-weight: 600; }
      .legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 11px; color: var(--secondary-text-color); margin-top: 4px; }
      .legend span { display: inline-flex; align-items: center; gap: 4px; }
      .legend i { display: inline-block; width: 14px; height: 3px; border-radius: 2px; }
      .legend i.dash { background: repeating-linear-gradient(90deg, var(--secondary-text-color) 0 4px, transparent 4px 7px); }
      .legend i.heat { height: 10px; background: ${COLORS.heating}; }
      .legend .unit { margin-left: auto; }
      .note { font-size: 11px; color: var(--secondary-text-color); margin-top: 4px; }
    `;
  }
}

class NWP500TankCardEditor extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
  }

  setConfig(config) {
    this._config = config;
    this._render();
  }

  _render() {
    if (!this._form) {
      this._form = document.createElement('ha-form');
      this._form.computeLabel = (s) => ({
        entity: 'Water heater',
        name: 'Name',
        hours_back: 'Hours of history',
        hours_ahead: 'Hours of forecast',
        upper_entity: 'Upper tank sensor (found automatically)',
        lower_entity: 'Lower tank sensor (found automatically)',
      })[s.name] || s.name;
      this._form.addEventListener('value-changed', (ev) => {
        const event = new Event('config-changed', { bubbles: true, composed: true });
        event.detail = { config: ev.detail.value };
        this.dispatchEvent(event);
      });
      this.appendChild(this._form);
    }
    this._form.schema = [
      { name: 'entity', required: true, selector: { entity: { domain: 'water_heater', integration: 'nwp500' } } },
      { name: 'name', selector: { text: {} } },
      { name: 'hours_back', selector: { number: { min: 1, max: 48, mode: 'box' } } },
      { name: 'hours_ahead', selector: { number: { min: 1, max: 24, mode: 'box' } } },
      { name: 'upper_entity', selector: { entity: { domain: 'sensor', device_class: 'temperature' } } },
      { name: 'lower_entity', selector: { entity: { domain: 'sensor', device_class: 'temperature' } } },
    ];
    this._form.data = { hours_back: 6, hours_ahead: 24, ...this._config };
    if (this._hass) this._form.hass = this._hass;
  }
}

customElements.define('nwp500-tank-card', NWP500TankCard);
customElements.define('nwp500-tank-card-editor', NWP500TankCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'nwp500-tank-card',
  name: 'NWP500 Tank Forecast',
  preview: true,
  description: 'The tank\'s temperatures, recorded and modelled by the hot water simulator: when it comes to temperature, or when the compressor next starts.',
});

console.info(`%c NWP500-TANK-CARD %c ${TANK_CARD_VERSION} `, 'color: white; background: #e8743b; font-weight: 700;', 'color: #e8743b; background: white; font-weight: 700;');
