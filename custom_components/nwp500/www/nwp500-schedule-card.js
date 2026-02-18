/**
 * NWP500 Reservation Schedule Card
 * Custom Lovelace card for managing Navien NWP500 water heater reservations.
 */

const CARD_VERSION = '1.1.0';

// Mode definitions
const MODES = {
  1: { name: 'Heat Pump', icon: 'mdi:heat-pump', color: '#00BCD4', short: 'HP' },
  2: { name: 'Electric', icon: 'mdi:flash', color: '#FF5722', short: 'ELEC' },
  3: { name: 'Energy Saver', icon: 'mdi:leaf', color: '#4CAF50', short: 'ECO' },
  4: { name: 'High Demand', icon: 'mdi:fire', color: '#FF9800', short: 'HIGH' },
  5: { name: 'Vacation', icon: 'mdi:palm-tree', color: '#9C27B0', short: 'VAC' },
  6: { name: 'Power Off', icon: 'mdi:power-off', color: '#607D8B', short: 'OFF' },
};

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
// MGPP Week Bitfield: Sun=bit7(128), Mon=bit6(64), ..., Sat=bit1(2). Bit0 unused.
const DAY_BITS = [128, 64, 32, 16, 8, 4, 2];
const DAY_NAMES_FULL = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

// Temperature conversion helpers
// Temperature helpers — param is half-degrees Celsius
function paramToC(param) { return param / 2.0; }
function paramToF(param) { return paramToC(param) * 9 / 5 + 32; }
function cToParam(c) { return Math.round(c * 2); }
function fToParam(f) { return Math.round((f - 32) * 5 / 9 * 2); }

// Decode week bitfield to array of day indices
function decodeDays(week) {
  const days = [];
  for (let i = 0; i < 7; i++) {
    if (week & DAY_BITS[i]) days.push(i);
  }
  return days;
}

// Encode array of day indices to week bitfield
function encodeDays(dayIndices) {
  let week = 0;
  for (const d of dayIndices) week |= DAY_BITS[d];
  return week;
}

function pad(n) { return String(n).padStart(2, '0'); }

class NWP500ScheduleCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass = null;
    this._reservations = [];
    this._reservationUse = 0;
    this._selectedDay = new Date().getDay();
    this._editingEntry = null; // null = not editing, -1 = adding new
    this._unsub = null;
    this._initialized = false;
    this._modalEl = null; // body-level modal container
  }

  static getConfigElement() {
    return document.createElement('nwp500-schedule-card-editor');
  }

  static getStubConfig() {
    return { device_id: '', title: 'Water Heater Schedule' };
  }

  setConfig(config) {
    if (!config.device_id) {
      throw new Error('device_id is required');
    }
    this._config = { title: 'Water Heater Schedule', ...config };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this._subscribeEvents();
      this._requestReservations();
    }
    this._render();
  }

  async _subscribeEvents() {
    if (!this._hass || !this._hass.connection) return;
    try {
      this._unsub = await this._hass.connection.subscribeEvents(
        (ev) => this._handleReservationEvent(ev),
        'nwp500_reservations_updated'
      );
    } catch (e) {
      console.warn('NWP500 Schedule Card: Could not subscribe to events', e);
    }
  }

  _handleReservationEvent(ev) {
    const data = ev.data;
    this._reservationUse = data.reservation_use || 0;
    this._reservations = data.reservations || [];
    this._render();
  }

  async _requestReservations() {
    if (!this._hass) return;
    try {
      await this._hass.callService('nwp500', 'request_reservations', {
        device_id: this._config.device_id,
      });
    } catch (e) {
      console.warn('NWP500 Schedule Card: Could not request reservations', e);
    }
  }

  _usesCelsius() {
    const unit = this._hass?.config?.unit_system?.temperature;
    return unit === '°C';
  }

  _formatTemp(param) {
    if (this._usesCelsius()) {
      return Math.round(paramToC(param)) + '°C';
    }
    return Math.round(paramToF(param)) + '°F';
  }

  async _updateReservations(reservations, enabled) {
    if (!this._hass) return;
    try {
      await this._hass.callService('nwp500', 'update_reservations', {
        device_id: this._config.device_id,
        reservations: reservations,
        enabled: enabled,
      });
      // Refresh after short delay
      setTimeout(() => this._requestReservations(), 1500);
    } catch (e) {
      console.error('NWP500 Schedule Card: Failed to update reservations', e);
    }
  }

  _getEntriesForDay(dayIndex) {
    return this._reservations
      .map((r, i) => ({ ...r, _idx: i }))
      .filter(r => r.week & DAY_BITS[dayIndex])
      .sort((a, b) => a.hour * 60 + a.min - (b.hour * 60 + b.min));
  }

  _deleteEntry(idx) {
    const updated = [...this._reservations];
    updated.splice(idx, 1);
    this._reservations = updated;
    this._updateReservations(updated, this._reservationUse === 2);
  }


  _toggleGlobalEnabled() {
    const newUse = this._reservationUse === 2 ? 1 : 2;
    this._reservationUse = newUse;
    this._updateReservations(this._reservations, newUse === 2);
  }

  _saveEntry(entry) {
    const updated = [...this._reservations];
    if (this._editingEntry === -1) {
      updated.push(entry);
    } else if (this._editingEntry !== null && this._editingEntry >= 0) {
      updated[this._editingEntry] = entry;
    }
    this._reservations = updated;
    this._editingEntry = null;
    this._updateReservations(updated, this._reservationUse === 2);
  }

  _copyDaySchedule(fromDay) {
    // Show copy modal
    this._showCopyModal = true;
    this._copyFromDay = fromDay;
    this._copyTargetDays = [];
    this._render();
  }

  _executeCopy() {
    const fromEntries = this._reservations.filter(r => r.week & DAY_BITS[this._copyFromDay]);
    const updated = [...this._reservations];

    for (const targetDay of this._copyTargetDays) {
      // Remove existing entries for target day that aren't shared with other non-target days
      for (let i = updated.length - 1; i >= 0; i--) {
        if (updated[i].week & DAY_BITS[targetDay]) {
          // Remove this day from the entry
          updated[i] = { ...updated[i], week: updated[i].week & ~DAY_BITS[targetDay] };
          if (updated[i].week === 0) updated.splice(i, 1);
        }
      }
      // Add copies of from-day entries for target day
      for (const entry of fromEntries) {
        updated.push({
          enable: entry.enable,
          week: DAY_BITS[targetDay],
          hour: entry.hour,
          min: entry.min,
          mode: entry.mode,
          param: entry.param,
        });
      }
    }

    this._reservations = updated;
    this._showCopyModal = false;
    this._updateReservations(updated, this._reservationUse === 2);
  }

  disconnectedCallback() {
    if (this._unsub) {
      this._unsub();
      this._unsub = null;
    }
    this._removeModal();
  }

  getCardSize() {
    return 6;
  }

  _render() {
    if (!this._hass) return;
    const entries = this._getEntriesForDay(this._selectedDay);

    this.shadowRoot.innerHTML = `
      <style>${this._getStyles()}</style>
      <ha-card>
        <div class="card-header">
          <div class="title-row">
            <span class="title">${this._config.title}</span>
            <div class="global-toggle">
              <span class="toggle-label">${this._reservationUse === 2 ? 'Active' : 'Inactive'}</span>
              <div class="toggle-switch ${this._reservationUse === 2 ? 'on' : ''}" id="globalToggle">
                <div class="toggle-thumb"></div>
              </div>
            </div>
          </div>
        </div>
        <div class="card-content">
          ${this._renderDayPills()}
          ${this._renderTimeline(entries)}
          ${this._renderEntryList(entries)}
          ${this._renderActions()}
        </div>
      </ha-card>
    `;
    this._attachCardListeners();

    // Show/hide body-level modal (escapes shadow DOM clipping)
    if (this._editingEntry !== null) {
      this._showModalOnBody(this._buildModalHTML());
    } else if (this._showCopyModal) {
      this._showModalOnBody(this._buildCopyModalHTML());
    } else {
      this._removeModal();
    }
  }

  _renderDayPills() {
    return `<div class="day-pills">${DAYS.map((d, i) => {
      const count = this._reservations.filter(r => r.week & DAY_BITS[i] && r.enable === 2).length;
      return `<button class="day-pill ${i === this._selectedDay ? 'selected' : ''}" data-day="${i}">
        <span class="day-name">${d}</span>
        ${count > 0 ? `<span class="day-badge">${count}</span>` : ''}
      </button>`;
    }).join('')}</div>`;
  }

  _renderTimeline(entries) {
    const hours = [0, 3, 6, 9, 12, 15, 18, 21];
    const markers = hours.map(h => `<div class="time-marker" style="left:${(h / 24) * 100}%"><span>${h === 0 ? '12a' : h < 12 ? h + 'a' : h === 12 ? '12p' : (h - 12) + 'p'}</span></div>`).join('');
    const blocks = entries.map(e => {
      const left = ((e.hour * 60 + e.min) / 1440) * 100;
      const mode = MODES[e.mode] || MODES[1];
      const tempDisplay = this._formatTemp(e.param);
      const opacity = 1;
      return `<div class="timeline-block" style="left:${left}%;background:${mode.color};opacity:${opacity}" data-edit="${e._idx}" title="${mode.name} at ${pad(e.hour)}:${pad(e.min)} \u2014 ${tempDisplay}">
        <ha-icon icon="${mode.icon}" style="--mdc-icon-size:14px;color:#fff"></ha-icon>
      </div>`;
    }).join('');
    return `<div class="timeline"><div class="timeline-track">${markers}${blocks}</div></div>`;
  }

  _renderEntryList(entries) {
    if (entries.length === 0) {
      return `<div class="empty-state">
        <ha-icon icon="mdi:calendar-blank" style="--mdc-icon-size:40px;color:var(--secondary-text-color)"></ha-icon>
        <p>No reservations for ${DAY_NAMES_FULL[this._selectedDay]}</p>
      </div>`;
    }
    return `<div class="entry-list">${entries.map(e => {
      const mode = MODES[e.mode] || MODES[1];
      const tempDisplay = this._formatTemp(e.param);
      const days = decodeDays(e.week);
      return `<div class="entry-row">
        <div class="entry-color" style="background:${mode.color}"></div>
        <div class="entry-info">
          <div class="entry-time">${pad(e.hour)}:${pad(e.min)}</div>
          <div class="entry-mode">
            <ha-icon icon="${mode.icon}" style="--mdc-icon-size:16px;color:${mode.color}"></ha-icon>
            <span>${mode.name}</span>
          </div>
        </div>
        <div class="entry-temp">${e.mode <= 4 ? tempDisplay : '\u2014'}</div>
        <div class="entry-days">${days.map(d => DAYS[d]).join(', ')}</div>
        <div class="entry-actions">
          <button class="icon-btn" data-edit="${e._idx}" title="Edit">
            <ha-icon icon="mdi:pencil" style="--mdc-icon-size:18px"></ha-icon>
          </button>
          <button class="icon-btn danger" data-delete="${e._idx}" title="Delete">
            <ha-icon icon="mdi:delete" style="--mdc-icon-size:18px"></ha-icon>
          </button>
        </div>
      </div>`;
    }).join('')}</div>`;
  }

  _renderActions() {
    return `<div class="actions-row">
      <button class="action-btn primary" id="addBtn">
        <ha-icon icon="mdi:plus" style="--mdc-icon-size:18px"></ha-icon> Add
      </button>
      <button class="action-btn" id="copyBtn">
        <ha-icon icon="mdi:content-copy" style="--mdc-icon-size:18px"></ha-icon> Copy Day
      </button>
      <button class="action-btn" id="refreshBtn">
        <ha-icon icon="mdi:refresh" style="--mdc-icon-size:18px"></ha-icon>
      </button>
    </div>`;
  }

  // --- Body-level modal (escapes all shadow DOM / overflow clipping) ---

  _removeModal() {
    if (this._modalEl) {
      this._modalEl.remove();
      this._modalEl = null;
    }
  }

  _showModalOnBody(html) {
    this._removeModal();
    const el = document.createElement('div');
    el.id = 'nwp500-modal-root';
    el.innerHTML = `<style>${this._getModalStyles()}</style>${html}`;
    document.body.appendChild(el);
    this._modalEl = el;
    this._attachModalListeners();
  }

  _getModalStyles() {
    return `
      .nwp-modal-overlay {
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.6); display: flex;
        align-items: center; justify-content: center; z-index: 9999;
        backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px);
      }
      .nwp-modal {
        background: rgb(35, 35, 40); border: 1px solid rgba(255,255,255,0.1);
        border-radius: 16px; width: 90%; max-width: 400px;
        box-shadow: 0 20px 60px rgba(0,0,0,0.5);
        animation: nwpModalIn 0.25s ease;
        color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        max-height: 90vh; display: flex; flex-direction: column;
      }
      @keyframes nwpModalIn { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }
      .nwp-modal-header {
        display: flex; align-items: center; justify-content: space-between;
        padding: 16px 20px; border-bottom: 1px solid rgba(255,255,255,0.06);
        font-size: 16px; font-weight: 600; color: #fff; flex-shrink: 0;
      }
      .nwp-modal-body { padding: 16px 20px; overflow-y: auto; flex: 1; }
      .nwp-modal-footer {
        padding: 12px 20px; border-top: 1px solid rgba(255,255,255,0.06);
        display: flex; justify-content: flex-end; gap: 8px; flex-shrink: 0;
      }
      .nwp-form-group { margin-bottom: 16px; }
      .nwp-form-group label { display: block; font-size: 12px; color: #999; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
      .nwp-time-picker { display: flex; align-items: center; gap: 8px; }
      .nwp-time-picker select {
        background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.12);
        border-radius: 8px; color: #fff; padding: 8px 12px; font-size: 16px;
        appearance: none; cursor: pointer;
      }
      .nwp-time-picker span { color: #fff; font-size: 20px; font-weight: 300; }
      .nwp-mode-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
      .nwp-mode-btn {
        display: flex; flex-direction: column; align-items: center; gap: 4px;
        padding: 10px 4px; border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.08); background: rgba(255,255,255,0.04);
        color: #aaa; cursor: pointer; transition: all 0.2s; font-size: 11px;
      }
      .nwp-mode-btn:hover { background: rgba(255,255,255,0.08); color: #ddd; }
      .nwp-mode-btn.selected { border-color: var(--mode-color); color: var(--mode-color); background: color-mix(in srgb, var(--mode-color) 20%, transparent); }
      .nwp-temp-slider {
        width: 100%; height: 6px; border-radius: 3px; appearance: none;
        background: linear-gradient(90deg, #00BCD4, #FF5722); outline: none; cursor: pointer;
      }
      .nwp-temp-slider::-webkit-slider-thumb {
        appearance: none; width: 20px; height: 20px; border-radius: 50%;
        background: #fff; box-shadow: 0 2px 6px rgba(0,0,0,0.3); cursor: pointer;
      }
      .nwp-mode-btn svg, .nwp-mode-btn ha-icon { width: 20px; height: 20px; }
      .nwp-day-checks { display: flex; gap: 6px; flex-wrap: wrap; }
      .nwp-day-check {
        display: flex; align-items: center; gap: 4px;
        padding: 6px 12px; border-radius: 8px; cursor: pointer;
        border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.04);
        color: #aaa; font-size: 13px; transition: all 0.2s;
      }
      .nwp-day-check:has(input:checked) { background: rgba(0,188,212,0.2); border-color: #00BCD4; color: #00BCD4; }
      .nwp-day-check input { display: none; }
      .nwp-action-btn {
        display: inline-flex; align-items: center; gap: 4px;
        padding: 7px 14px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.1);
        background: rgba(255,255,255,0.06); color: #ccc; font-size: 13px; cursor: pointer; transition: all 0.2s;
      }
      .nwp-action-btn:hover { background: rgba(255,255,255,0.12); color: #fff; }
      .nwp-action-btn.primary { background: rgba(0,188,212,0.2); border-color: rgba(0,188,212,0.4); color: #00BCD4; }
      .nwp-action-btn.primary:hover { background: rgba(0,188,212,0.35); }
      .nwp-icon-btn {
        background: none; border: none; color: #888; cursor: pointer; padding: 4px;
        border-radius: 6px; transition: all 0.2s; display: flex; align-items: center; font-size: 20px;
      }
      .nwp-icon-btn:hover { color: #fff; background: rgba(255,255,255,0.1); }
    `;
  }

  _buildModalHTML() {
    const isNew = this._editingEntry === -1;
    const useCelsius = this._usesCelsius();
    const defaultParam = useCelsius ? cToParam(49) : fToParam(120);
    const entry = isNew
      ? { enable: 2, week: DAY_BITS[this._selectedDay], hour: 6, min: 0, mode: 3, param: defaultParam }
      : { ...this._reservations[this._editingEntry] };
    const selectedDays = decodeDays(entry.week);
    const tempVal = useCelsius ? Math.round(paramToC(entry.param)) : Math.round(paramToF(entry.param));
    const tempUnit = useCelsius ? '°C' : '°F';
    const tempMin = useCelsius ? 35 : 80;
    const tempMax = useCelsius ? 65 : 150;
    const MI = { 1: '\u2744\ufe0f', 2: '\u26a1', 3: '\ud83c\udf3f', 4: '\ud83d\udd25', 5: '\ud83c\udf34', 6: '\u23fb' };

    return `<div class="nwp-modal-overlay" id="nwpModalOverlay">
      <div class="nwp-modal">
        <div class="nwp-modal-header">
          <span>${isNew ? 'Add Reservation' : 'Edit Reservation'}</span>
          <button class="nwp-icon-btn" id="nwpModalClose">\u2715</button>
        </div>
        <div class="nwp-modal-body">
          <div class="nwp-form-group">
            <label>Time</label>
            <div class="nwp-time-picker">
              <select id="nwpHourSelect">${Array.from({ length: 24 }, (_, i) => `<option value="${i}" ${i === entry.hour ? 'selected' : ''}>${pad(i)}</option>`).join('')}</select>
              <span>:</span>
              <select id="nwpMinSelect">${[0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55].map(m => `<option value="${m}" ${m === entry.min ? 'selected' : ''}>${pad(m)}</option>`).join('')}</select>
            </div>
          </div>
          <div class="nwp-form-group">
            <label>Mode</label>
            <div class="nwp-mode-grid">${Object.entries(MODES).map(([id, m]) =>
      `<button class="nwp-mode-btn ${Number(id) === entry.mode ? 'selected' : ''}" data-mode="${id}" style="--mode-color:${m.color}">
                <span style="font-size:20px">${MI[id]}</span><span>${m.short}</span>
              </button>`
    ).join('')}</div>
          </div>
          <div class="nwp-form-group" id="nwpTempGroup" style="${entry.mode > 4 ? 'display:none' : ''}">
            <label>Temperature: <span id="nwpTempValue">${tempVal}${tempUnit}</span></label>
            <input type="range" class="nwp-temp-slider" id="nwpTempSlider" min="${tempMin}" max="${tempMax}" value="${tempVal}" step="1">
          </div>
          <div class="nwp-form-group">
            <label>Days</label>
            <div class="nwp-day-checks">${DAYS.map((d, i) =>
      `<label class="nwp-day-check"><input type="checkbox" data-day="${i}" ${selectedDays.includes(i) ? 'checked' : ''}><span>${d}</span></label>`
    ).join('')}</div>
          </div>
        </div>
        <div class="nwp-modal-footer">
          <button class="nwp-action-btn" id="nwpModalCancel">Cancel</button>
          <button class="nwp-action-btn primary" id="nwpModalSave">Save</button>
        </div>
      </div>
    </div>`;
  }

  _buildCopyModalHTML() {
    return `<div class="nwp-modal-overlay" id="nwpCopyOverlay">
      <div class="nwp-modal" style="max-width:340px">
        <div class="nwp-modal-header">
          <span>Copy ${DAY_NAMES_FULL[this._copyFromDay]}'s Schedule</span>
          <button class="nwp-icon-btn" id="nwpCopyClose">\u2715</button>
        </div>
        <div class="nwp-modal-body">
          <p style="margin:0 0 12px;color:#999">Select target days:</p>
          <div class="nwp-day-checks">${DAYS.map((d, i) =>
      i === this._copyFromDay ? '' :
        `<label class="nwp-day-check"><input type="checkbox" data-copyday="${i}"><span>${d}</span></label>`
    ).join('')}</div>
        </div>
        <div class="nwp-modal-footer">
          <button class="nwp-action-btn" id="nwpCopyCancel">Cancel</button>
          <button class="nwp-action-btn primary" id="nwpCopyExec">Copy</button>
        </div>
      </div>
    </div>`;
  }

  _attachModalListeners() {
    const el = this._modalEl;
    if (!el) return;
    const $ = (s) => el.querySelector(s);
    const $$ = (s) => el.querySelectorAll(s);
    const closeEdit = () => { this._editingEntry = null; this._render(); };
    const closeCopy = () => { this._showCopyModal = false; this._render(); };

    const modalOverlay = $('#nwpModalOverlay');
    if (modalOverlay) {
      $('#nwpModalClose')?.addEventListener('click', closeEdit);
      $('#nwpModalCancel')?.addEventListener('click', closeEdit);
      modalOverlay.addEventListener('click', (e) => { if (e.target === modalOverlay) closeEdit(); });

      $$('.nwp-mode-btn').forEach(btn => btn.addEventListener('click', () => {
        $$('.nwp-mode-btn').forEach(b => b.classList.remove('selected'));
        btn.classList.add('selected');
        const tg = $('#nwpTempGroup');
        if (tg) tg.style.display = Number(btn.dataset.mode) > 4 ? 'none' : '';
      }));

      const slider = $('#nwpTempSlider');
      const tv = $('#nwpTempValue');
      const tempUnitStr = this._usesCelsius() ? '°C' : '°F';
      if (slider && tv) slider.addEventListener('input', () => { tv.textContent = slider.value + tempUnitStr; });

      $('#nwpModalSave')?.addEventListener('click', () => {
        const hour = Number($('#nwpHourSelect').value);
        const min = Number($('#nwpMinSelect').value);
        const mb = el.querySelector('.nwp-mode-btn.selected');
        const mode = mb ? Number(mb.dataset.mode) : 3;
        const sliderVal = Number($('#nwpTempSlider')?.value || (this._usesCelsius() ? 49 : 120));
        const param = this._usesCelsius() ? cToParam(sliderVal) : fToParam(sliderVal);
        let week = 0;
        $$('.nwp-day-checks input[type=checkbox]').forEach(cb => {
          if (cb.checked && cb.dataset.day !== undefined) week |= DAY_BITS[Number(cb.dataset.day)];
        });
        if (week === 0) { alert('Select at least one day'); return; }
        this._saveEntry({ enable: 2, week, hour, min, mode, param });
      });
    }

    const copyOverlay = $('#nwpCopyOverlay');
    if (copyOverlay) {
      $('#nwpCopyClose')?.addEventListener('click', closeCopy);
      $('#nwpCopyCancel')?.addEventListener('click', closeCopy);
      copyOverlay.addEventListener('click', (e) => { if (e.target === copyOverlay) closeCopy(); });
      $('#nwpCopyExec')?.addEventListener('click', () => {
        this._copyTargetDays = [];
        $$('[data-copyday]').forEach(cb => { if (cb.checked) this._copyTargetDays.push(Number(cb.dataset.copyday)); });
        if (this._copyTargetDays.length === 0) { alert('Select at least one target day'); return; }
        this._executeCopy();
      });
    }
  }

  _attachCardListeners() {
    const $ = (s) => this.shadowRoot.querySelector(s);
    const $$ = (s) => this.shadowRoot.querySelectorAll(s);

    $$('.day-pill').forEach(btn => btn.addEventListener('click', () => {
      this._selectedDay = Number(btn.dataset.day); this._render();
    }));
    const gt = $('#globalToggle');
    if (gt) gt.addEventListener('click', () => this._toggleGlobalEnabled());

    $$('[data-edit]').forEach(btn => btn.addEventListener('click', () => {
      this._editingEntry = Number(btn.dataset.edit); this._render();
    }));
    $$('[data-delete]').forEach(btn => btn.addEventListener('click', () => {
      if (confirm('Delete this reservation?')) this._deleteEntry(Number(btn.dataset.delete));
    }));

    const addBtn = $('#addBtn');
    if (addBtn) addBtn.addEventListener('click', () => { this._editingEntry = -1; this._render(); });
    const copyBtn = $('#copyBtn');
    if (copyBtn) copyBtn.addEventListener('click', () => this._copyDaySchedule(this._selectedDay));
    const refreshBtn = $('#refreshBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', () => this._requestReservations());
  }

  _getStyles() {
    return `
      :host { display: block; }
      ha-card {
        background: rgba(30, 30, 30, 0.85);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px;
        overflow: hidden;
        color: #e0e0e0;
        font-family: inherit;
      }
      .card-header {
        padding: 16px 20px 8px;
      }
      .title-row {
        display: flex; align-items: center; justify-content: space-between;
      }
      .title {
        font-size: 18px; font-weight: 600; color: #fff;
      }
      .global-toggle {
        display: flex; align-items: center; gap: 8px; cursor: pointer;
      }
      .toggle-label {
        font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;
        color: var(--secondary-text-color, #999);
      }
      .toggle-switch {
        width: 40px; height: 22px; border-radius: 11px;
        background: rgba(255,255,255,0.15); position: relative;
        transition: background 0.3s;
      }
      .toggle-switch.on { background: #4CAF50; }
      .toggle-thumb {
        width: 18px; height: 18px; border-radius: 50%; background: #fff;
        position: absolute; top: 2px; left: 2px; transition: transform 0.3s;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
      }
      .toggle-switch.on .toggle-thumb { transform: translateX(18px); }
      .card-content { padding: 8px 16px 16px; }

      /* Day Pills */
      .day-pills {
        display: flex; gap: 4px; margin-bottom: 14px; justify-content: center;
      }
      .day-pill {
        position: relative; padding: 6px 10px; border-radius: 20px;
        border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.05);
        color: #aaa; font-size: 12px; font-weight: 500; cursor: pointer;
        transition: all 0.25s; display: flex; align-items: center; gap: 3px;
        flex: 0 1 auto; min-width: 0;
      }
      .day-pill:hover { background: rgba(255,255,255,0.1); color: #ddd; }
      .day-pill.selected {
        background: rgba(0, 188, 212, 0.2); border-color: #00BCD4;
        color: #00BCD4; font-weight: 600;
      }
      .day-badge {
        min-width: 14px; height: 14px; border-radius: 7px;
        background: #00BCD4; color: #000; font-size: 9px; font-weight: 700;
        display: flex; align-items: center; justify-content: center;
      }
      .day-pill.selected .day-badge { background: #fff; }

      /* Timeline */
      .timeline {
        background: rgba(255,255,255,0.03); border-radius: 10px;
        padding: 12px 12px 24px; margin-bottom: 12px;
        border: 1px solid rgba(255,255,255,0.05);
      }
      .timeline-track {
        position: relative; height: 36px;
        background: linear-gradient(90deg, rgba(255,255,255,0.02) 0%, rgba(255,255,255,0.05) 50%, rgba(255,255,255,0.02) 100%);
        border-radius: 6px; overflow: visible;
      }
      .time-marker {
        position: absolute; top: 0; height: 100%;
        border-left: 1px solid rgba(255,255,255,0.08);
      }
      .time-marker span {
        position: absolute; bottom: -18px; left: -10px;
        font-size: 9px; color: rgba(255,255,255,0.3); white-space: nowrap;
      }
      .timeline-block {
        position: absolute; top: 4px; width: 28px; height: 28px;
        border-radius: 50%; display: flex; align-items: center; justify-content: center;
        cursor: pointer; transform: translateX(-14px);
        transition: transform 0.2s, box-shadow 0.2s;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
      }
      .timeline-block:hover { transform: translateX(-14px) scale(1.2); box-shadow: 0 4px 16px rgba(0,0,0,0.5); }

      /* Entry List */
      .entry-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
      .entry-row {
        display: flex; align-items: center; gap: 10px;
        padding: 10px 12px; border-radius: 10px;
        background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06);
        transition: background 0.2s;
      }
      .entry-row:hover { background: rgba(255,255,255,0.07); }
      .entry-row.disabled { opacity: 0.45; }
      .entry-color { width: 4px; height: 36px; border-radius: 2px; flex-shrink: 0; }
      .entry-info { flex: 1; min-width: 0; }
      .entry-time { font-size: 16px; font-weight: 600; color: #fff; font-variant-numeric: tabular-nums; }
      .entry-mode { display: flex; align-items: center; gap: 4px; font-size: 12px; color: #aaa; margin-top: 2px; }
      .entry-temp { font-size: 14px; font-weight: 500; color: #ccc; min-width: 48px; text-align: right; }
      .entry-days { font-size: 11px; color: #888; min-width: 70px; }
      .entry-actions { display: flex; gap: 2px; }

      /* Empty state */
      .empty-state {
        display: flex; flex-direction: column; align-items: center;
        padding: 28px 0; gap: 8px;
      }
      .empty-state p { margin: 0; color: var(--secondary-text-color, #777); font-size: 14px; }

      /* Actions */
      .actions-row {
        display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px;
      }
      .action-btn {
        display: inline-flex; align-items: center; gap: 4px;
        padding: 7px 12px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.1);
        background: rgba(255,255,255,0.06); color: #ccc;
        font-size: 12px; cursor: pointer; transition: all 0.2s;
        white-space: nowrap;
      }
      .action-btn:hover { background: rgba(255,255,255,0.12); color: #fff; }
      .action-btn.primary {
        background: rgba(0, 188, 212, 0.2); border-color: rgba(0, 188, 212, 0.4); color: #00BCD4;
      }
      .action-btn.primary:hover { background: rgba(0, 188, 212, 0.35); }
      .icon-btn {
        background: none; border: none; color: #888; cursor: pointer;
        padding: 4px; border-radius: 6px; transition: all 0.2s;
        display: flex; align-items: center;
      }
      .icon-btn:hover { color: #fff; background: rgba(255,255,255,0.1); }
      .icon-btn.danger:hover { color: #ef5350; background: rgba(239,83,80,0.15); }

      /* Modal */
      .modal-overlay {
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.6); display: flex;
        align-items: center; justify-content: center; z-index: 999;
        backdrop-filter: blur(4px);
      }
      .modal {
        background: rgb(35, 35, 40); border: 1px solid rgba(255,255,255,0.1);
        border-radius: 16px; width: 90%; max-width: 400px;
        box-shadow: 0 20px 60px rgba(0,0,0,0.5);
        animation: modalIn 0.25s ease;
      }
      @keyframes modalIn { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }
      .modal-header {
        display: flex; align-items: center; justify-content: space-between;
        padding: 16px 20px; border-bottom: 1px solid rgba(255,255,255,0.06);
        font-size: 16px; font-weight: 600; color: #fff;
      }
      .modal-body { padding: 16px 20px; max-height: 60vh; overflow-y: auto; }
      .modal-footer {
        padding: 12px 20px; border-top: 1px solid rgba(255,255,255,0.06);
        display: flex; justify-content: flex-end; gap: 8px;
      }
      .form-group { margin-bottom: 16px; }
      .form-group label { display: block; font-size: 12px; color: #999; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
      .time-picker {
        display: flex; align-items: center; gap: 8px;
      }
      .time-picker select {
        background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.12);
        border-radius: 8px; color: #fff; padding: 8px 12px; font-size: 16px;
        appearance: none; cursor: pointer;
      }
      .time-picker span { color: #fff; font-size: 20px; font-weight: 300; }

      /* Mode grid */
      .mode-grid {
        display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px;
      }
      .mode-btn {
        display: flex; flex-direction: column; align-items: center; gap: 4px;
        padding: 10px 4px; border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.08); background: rgba(255,255,255,0.04);
        color: #aaa; cursor: pointer; transition: all 0.2s; font-size: 11px;
      }
      .mode-btn:hover { background: rgba(255,255,255,0.08); color: #ddd; }
      .mode-btn.selected {
        background: color-mix(in srgb, var(--mode-color) 20%, transparent);
        border-color: var(--mode-color); color: var(--mode-color);
      }

      /* Temp slider */
      input[type=range] {
        width: 100%; height: 6px; border-radius: 3px; appearance: none;
        background: linear-gradient(90deg, #00BCD4, #FF5722);
        outline: none; cursor: pointer;
      }
      input[type=range]::-webkit-slider-thumb {
        appearance: none; width: 20px; height: 20px; border-radius: 50%;
        background: #fff; box-shadow: 0 2px 6px rgba(0,0,0,0.3); cursor: pointer;
      }

      /* Day checks */
      .day-checks { display: flex; gap: 6px; flex-wrap: wrap; }
      .day-check {
        display: flex; align-items: center; gap: 4px;
        padding: 6px 12px; border-radius: 8px; cursor: pointer;
        border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.04);
        color: #aaa; font-size: 13px; transition: all 0.2s;
      }
      .day-check:has(input:checked) {
        background: rgba(0, 188, 212, 0.2); border-color: #00BCD4; color: #00BCD4;
      }
      .day-check input { display: none; }
    `;
  }
}

// Card Editor
class NWP500ScheduleCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
  }

  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) { this._hass = hass; }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        .editor { padding: 16px; }
        .field { margin-bottom: 12px; }
        .field label { display: block; margin-bottom: 4px; font-size: 13px; font-weight: 500; }
        .field input { width: 100%; padding: 8px; box-sizing: border-box; border: 1px solid var(--divider-color); border-radius: 6px; background: var(--card-background-color); color: var(--primary-text-color); }
      </style>
      <div class="editor">
        <div class="field">
          <label>Device ID (required)</label>
          <input id="deviceId" value="${this._config.device_id || ''}" placeholder="abc123...">
        </div>
        <div class="field">
          <label>Title</label>
          <input id="title" value="${this._config.title || 'Water Heater Schedule'}">
        </div>
      </div>
    `;
    this.shadowRoot.getElementById('deviceId')?.addEventListener('input', (e) => {
      this._config = { ...this._config, device_id: e.target.value };
      this._dispatch();
    });
    this.shadowRoot.getElementById('title')?.addEventListener('input', (e) => {
      this._config = { ...this._config, title: e.target.value };
      this._dispatch();
    });
  }

  _dispatch() {
    this.dispatchEvent(new CustomEvent('config-changed', { detail: { config: this._config } }));
  }
}

customElements.define('nwp500-schedule-card', NWP500ScheduleCard);
customElements.define('nwp500-schedule-card-editor', NWP500ScheduleCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'nwp500-schedule-card',
  name: 'NWP500 Schedule Card',
  description: 'Manage Navien NWP500 water heater reservation schedules',
  preview: true,
  documentationURL: 'https://github.com/eman/ha_nwp500',
});

console.info(
  `%c NWP500-SCHEDULE-CARD %c v${CARD_VERSION} `,
  'color: #fff; background: #00BCD4; font-weight: 700; padding: 2px 6px; border-radius: 4px 0 0 4px;',
  'color: #00BCD4; background: #1e1e1e; font-weight: 700; padding: 2px 6px; border-radius: 0 4px 4px 0;'
);
