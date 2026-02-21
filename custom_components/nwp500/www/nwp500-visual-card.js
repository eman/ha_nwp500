/**
 * NWP500 Visual Status Card
 * Visualizes water heater status using a device image with data overlays.
 */

const CARD_VERSION = '2.4.0';

class NWP500VisualCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass = null;
    this._modalEl = null;
  }

  static getConfigElement() {
    return document.createElement('nwp500-visual-card-editor');
  }

  static getStubConfig() {
    return {
      entity: 'water_heater.navien_nwp500',
      dhw_temp: 'sensor.navien_nwp500_dhw_outlet_temperature',
      dhw_charge: 'sensor.navien_nwp500_dhw_charge_percentage',
      tank_lower: 'sensor.navien_nwp500_tank_lower_temperature'
    };
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error('Entity (water_heater) is required');
    }
    const defaults = {
      dhw_temp: 'sensor.navien_nwp500_dhw_outlet_temperature',
      dhw_charge: 'sensor.navien_nwp500_dhw_charge_percentage',
      tank_lower: 'sensor.navien_nwp500_tank_lower_temperature'
    };
    this._config = { ...defaults, ...config };
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 4;
  }

  _render() {
    if (!this._hass) return;
    const entityId = this._config.entity;
    const stateObj = this._hass.states[entityId];

    if (!stateObj) {
      this.shadowRoot.innerHTML = `
        <ha-card>
          <div style="padding:16px;color:red">
            Entity not found: ${entityId}
          </div>
        </ha-card>`;
      return;
    }

    // Get sensor states
    const dhwTempState = this._config.dhw_temp ? this._hass.states[this._config.dhw_temp] : null;
    const dhwChargeState = this._config.dhw_charge ? this._hass.states[this._config.dhw_charge] : null;
    const tankLowerState = this._config.tank_lower ? this._hass.states[this._config.tank_lower] : null;

    // Attributes
    const currentModeRaw = stateObj.attributes.dhw_mode_setting || stateObj.state;
    // Map modes to icons and friendly names
    const MODE_MAP = {
      'heat_pump': { label: 'Heat Pump', icon: 'mdi:heat-pump' },
      'electric': { label: 'Electric', icon: 'mdi:flash' },
      'eco': { label: 'Eco', icon: 'mdi:leaf' },
      'high_demand': { label: 'High Demand', icon: 'mdi:lightning-bolt' },
      'vacation': { label: 'Vacation', icon: 'mdi:palm-tree' },
      'off': { label: 'Off', icon: 'mdi:power-off' },
      'standby': { label: 'Standby', icon: 'mdi:power-sleep' },
      'HEAT_PUMP': { label: 'Heat Pump', icon: 'mdi:heat-pump' },
      'ELECTRIC': { label: 'Electric', icon: 'mdi:flash' },
      'ECO': { label: 'Eco', icon: 'mdi:leaf' },
      'HIGH_DEMAND': { label: 'High Demand', icon: 'mdi:lightning-bolt' },
      'VACATION': { label: 'Vacation', icon: 'mdi:palm-tree' },
    };

    const modeData = MODE_MAP[currentModeRaw] || MODE_MAP[currentModeRaw.toLowerCase()] || { label: currentModeRaw.replace(/_/g, ' '), icon: 'mdi:information' };
    const settingFriendly = modeData.label;

    // Check units
    const useCelsius = this._hass.config.unit_system.temperature === '°C';
    const tempUnit = useCelsius ? '°C' : '°F';

    // Default limits based on unit system
    const minTempDefault = useCelsius ? 35 : 100; // ~95F / 100F
    const maxTempDefault = useCelsius ? 60 : 140; // ~140F

    const targetTemp = stateObj.attributes.temperature;
    const minTemp = stateObj.attributes.min_temp || minTempDefault;
    const maxTemp = stateObj.attributes.max_temp || maxTempDefault;

    // Cache bust image


    // Format values
    const dhwTemp = dhwTempState ? `${Math.round(Number(dhwTempState.state))}${tempUnit}` : '--';
    const dhwCharge = dhwChargeState ? `${Math.round(Number(dhwChargeState.state))}%` : '--';
    const tankLower = tankLowerState ? `${Math.round(Number(tankLowerState.state))}${tempUnit}` : '--';
    const targetDisplay = targetTemp ? `${Math.round(targetTemp)}${tempUnit}` : '--';

    // Status colors
    const isError = stateObj.attributes.error_code && stateObj.attributes.error_code !== '000';
    const isBurning = stateObj.attributes.compressor_running || stateObj.attributes.upper_element_on || stateObj.attributes.lower_element_on;

    this.shadowRoot.innerHTML = `
      <style>${this._getStyles()}</style>
      <ha-card>
        <div class="visual-container">
          <!-- Background Image -->
          <img src="/nwp500/nwp500-visual-card.png?v=2.4.1" class="bg-image" crossOrigin="anonymous" id="bgImage">

          <!-- Overlays -->
          
          <!-- Screen Area (Control Panel) -->
          <div class="overlay screen-overlay" id="screenArea">
            <div class="screen-content ${isError ? 'error' : ''}">
              <div class="screen-temp">${targetDisplay}</div>
              <div class="screen-mode">
                <ha-icon icon="${modeData.icon}"></ha-icon>
                <span>${modeData.label}</span>
              </div>
            </div>
          </div>

          <!-- Heating Status (Fire Icon) - Independent Overlay -->
          ${isBurning ? `
          <div class="overlay heating-status" id="heatingStatus">
            <ha-icon icon="mdi:fire" class="burning-icon"></ha-icon>
          </div>` : ''}

          <!-- Debug Overlays Removed -->

          <!-- DHW Outlet (Top Left approx) -->
          <div class="overlay badge outlet-badge">
            <ha-icon icon="mdi:thermometer"></ha-icon>
            <div class="badge-label">Outlet</div>
            <div class="badge-value">${dhwTemp}</div>
          </div>

          <!-- DHW Charge (Top Right approx) -->
          <div class="overlay badge charge-badge">
            <ha-icon icon="mdi:water-percent"></ha-icon>
            <div class="badge-label">Charge</div>
            <div class="badge-value">${dhwCharge}</div>
          </div>

          <!-- Lower Tank (Bottom Right approx) -->
          <div class="overlay badge lower-badge">
            <ha-icon icon="mdi:thermometer-low"></ha-icon>
            <div class="badge-label">Lower</div>
            <div class="badge-value">${tankLower}</div>
          </div>
          
          <!-- Current Mode Indicator Removed -->
        </div>
      </ha-card>
    `;

    // Image Analysis for correct positioning
    const img = this.shadowRoot.getElementById('bgImage');
    const screenEl = this.shadowRoot.getElementById('screenArea');
    const heatingEl = this.shadowRoot.getElementById('heatingStatus'); // Might be null if not burning



    // Attach listeners
    this.shadowRoot.getElementById('screenArea').addEventListener('click', () => {
      this._showControlModal(stateObj, targetTemp, minTemp, maxTemp, settingFriendly);
    });

    const outletBadge = this.shadowRoot.querySelector('.outlet-badge');
    if (outletBadge) {
      outletBadge.addEventListener('click', (e) => {
        e.stopPropagation();
        this._showHistoryModal('DHW Outlet Temperature', dhwTemp, '', this._config.dhw_temp);
      });
    }

    const chargeBadge = this.shadowRoot.querySelector('.charge-badge');
    if (chargeBadge) {
      chargeBadge.addEventListener('click', (e) => {
        e.stopPropagation();
        this._showHistoryModal('DHW Charge', dhwCharge, '', this._config.dhw_charge);
      });
    }

    const lowerBadge = this.shadowRoot.querySelector('.lower-badge');
    if (lowerBadge) {
      lowerBadge.addEventListener('click', (e) => {
        e.stopPropagation();
        this._showHistoryModal('Lower Tank Temperature', tankLower, '', this._config.tank_lower);
      });
    }


    if (this._modalEl) {
      // Re-render modal if open? (Simple way: close it on re-render or keep logic separate. 
      // For now, let's close it on re-render to avoid stale state, or implement update logic)
      // Better: don't re-render modal on every state change if interacting.
      // But render() is called on state change. 
      // We'll leave modal handling to separate methods.
    }
  }

  _getStyles() {
    return `
      :host { display: block; }
      ha-card {
        overflow: hidden;
        background: var(--ha-card-background, var(--card-background-color, white));
        border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color, #e0e0e0));
        box-shadow: var(--ha-card-box-shadow, 0px 2px 1px -1px rgba(0,0,0,0.2), 0px 1px 1px 0px rgba(0,0,0,0.14), 0px 1px 3px 0px rgba(0,0,0,0.12));
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 16px 0;
      }
      .visual-container {
        position: relative;
        width: 100%;
        max-width: 400px;
        margin: 0 auto;
        display: inline-block; /* Let image dictate height */
      }
      .bg-image {
        width: 100%;
        display: block;
        height: auto;
      }
      
      .overlay {
        position: absolute;
        cursor: pointer;
        transition: transform 0.2s;
      }
      .overlay:hover {
        transform: scale(1.05);
        z-index: 10;
      }

      /* Screen Area - The Black Panel */
      /* Targeted for v2.0.0 image (stout/square) */
      .screen-overlay {
        top: 22%; /* Moved down to match new physical screen */
        left: 50%;
        transform: translateX(-50%);
        width: 35%;
        height: 25%; /* Expanded for larger display */
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .screen-content {
        color: #fff;
        text-shadow: 0 0 10px rgba(0,255,0,0.4); /* Slight green glow hint for "active" look? Or just white. Let's stick to white/crisp. */
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        width: 100%;
        height: 100%;
      }
      .screen-temp { font-size: clamp(20px, 5.0vw, 36px); font-weight: 700; line-height: 1; letter-spacing: 1px; margin-bottom: 2px; }
      .screen-mode { 
        font-size: clamp(10px, 1.5vw, 14px); 
        text-transform: uppercase; 
        font-weight: 500; 
        opacity: 0.9;
        display: flex;
        align-items: center;
        gap: 3px;
      }
      .screen-mode ha-icon { --mdc-icon-size: 14px; }
      .screen-mode span { line-height: 1; margin-top: 1px; }
      
      /* Badges */
      .badge {
        background: rgba(255, 255, 255, 0.15); /* Frosted white for black screen */
        backdrop-filter: blur(4px);
        border: 1px solid rgba(255,255,255,0.25);
        border-radius: 50%;
        width: 45px; height: 45px; /* Fixed circle size for compactness */
        padding: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        color: #fff;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        transition: all 0.2s ease;
      }
      .badge:hover { background: rgba(0, 0, 0, 0.8); transform: scale(1.1); border-color: rgba(255,255,255,0.4); z-index: 20; }
      
      /* Icon small or hidden? Let's keep icon small */
      .badge ha-icon { --mdc-icon-size: 14px; margin-bottom: 0; color: #80cbc4; opacity: 0.9; }
      .badge-label { display: none; /* Hide label for compact look, use icon */ }
      .badge-value { font-size: 11px; font-weight: 700; line-height: 1; margin-top: 1px; }

      /* Debug Overlays Removed */

      /* Positions on the Tank Image */
      /* Outlet (Hot) - Left side of black screen */
      .outlet-badge { top: 52%; left: 30%; }
      
      /* Charge - Right side of black screen */
      .charge-badge { top: 52%; right: 30%; }
      
      /* Lower Temp - Bottom center of black screen */
      .lower-badge { top: 58%; left: 50%; transform: translateX(-50%); } 

      /* Mode Indicator Removed (Redundant) */
      .mode-label { display: none; }
      .mode-value { font-size: 10px; color: #ccc; letter-spacing: 0.5px; }
      /* Heating Status - Independent Fire Icon */
      .heating-status {
        top: 48%; /* Just below the screen */
        left: 50%;
        transform: translateX(-50%);
        color: #ff9800;
        z-index: 5;
        pointer-events: none;
      }
      .burning-icon {
        --mdc-icon-size: 32px;
        filter: drop-shadow(0 0 4px rgba(255, 87, 34, 0.6));
        animation: pulse 2s infinite ease-in-out;
      }
      
      @keyframes pulse {
        0% { opacity: 0.7; transform: scale(0.9); }
        50% { opacity: 1; transform: scale(1.1); }
        100% { opacity: 0.7; transform: scale(0.9); }
      }
    `;
  }

  // Reuse modal logic from schedule card logic (simplified)
  _showControlModal(stateObj, currentTemp, minTemp, maxTemp, currentMode) {
    if (this._modalEl) return;

    // Styles for modal (embedded to avoid dependency)
    const modalStyle = `
      .nwp-modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.6); z-index: 9999; display: flex; align-items: center; justify-content: center; backdrop-filter: blur(4px); }
      .nwp-modal { background: #222; border: 1px solid #444; border-radius: 12px; width: 90%; max-width: 350px; padding: 20px; color: #fff; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
      .nwp-h { font-size: 18px; margin-bottom: 20px; font-weight: 600; display: flex; justify-content: space-between; }
      .nwp-row { margin-bottom: 20px; }
      .nwp-label { display: block; color: #aaa; font-size: 12px; text-transform: uppercase; margin-bottom: 8px; }
      .nwp-btn-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
      .nwp-btn { background: #333; border: 1px solid #444; color: #ccc; padding: 10px; border-radius: 8px; cursor: pointer; text-align: center; }
      .nwp-btn.active { background: #00BCD4; color: #000; border-color: #00BCD4; font-weight: bold; }
      .nwp-slider { width: 100%; height: 6px; background: linear-gradient(90deg, #2196F3, #F44336); border-radius: 3px; appearance: none; outline: none; }
      .nwp-slider::-webkit-slider-thumb { appearance: none; width: 20px; height: 20px; background: #fff; border-radius: 50%; box-shadow: 0 2px 4px rgba(0,0,0,0.3); }
      .nwp-val { font-size: 24px; font-weight: 300; text-align: center; margin-bottom: 10px; }
      .nwp-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 10px; }
      .nwp-action { padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; font-weight: 500; }
      .nwp-primary { background: #00BCD4; color: #000; }
      .nwp-cancel { background: transparent; color: #aaa; border: 1px solid #444; }
    `;

    // Op Modes (map to service values)
    const MODES = [
      { id: 'heat_pump', label: 'Heat Pump', icon: 'mdi:heat-pump' },
      { id: 'electric', label: 'Electric', icon: 'mdi:flash' },
      { id: 'eco', label: 'Eco', icon: 'mdi:leaf' },
      { id: 'high_demand', label: 'High Demand', icon: 'mdi:fire' },
      { id: 'vacation', label: 'Vacation', icon: 'mdi:palm-tree' },
      { id: 'off', label: 'Off', icon: 'mdi:power-off' }
    ];

    // Current setting logic
    // We assume `currentMode` passed is the friendly name, need to match to ID
    // Actually cleaner to look at stateObj.attributes.dhw_mode_setting (raw enum value?)
    // or just match label.
    // Let's use `stateObj.attributes.operation_mode` or similar if available, or just string match.
    // The `settingFriendly` passed in is "Eco", "Heat Pump", etc.

    // Create DOM
    const div = document.createElement('div');
    div.innerHTML = `
      <style>${modalStyle}</style>
      <div class="nwp-modal-overlay">
        <div class="nwp-modal">
          <div class="nwp-h">
            <span>Settings</span>
            <span style="cursor:pointer" id="nwpClose">✕</span>
          </div>
          
          <div class="nwp-row">
            <span class="nwp-label">Mode</span>
            <div class="nwp-btn-row">
              ${MODES.map(m => `<button class="nwp-btn ${m.label.toLowerCase() === currentMode.toLowerCase() ? 'active' : ''}" data-mode="${m.id}">${m.label}</button>`).join('')}
            </div>
          </div>

          <div class="nwp-row" id="tempRow">
            <span class="nwp-label">Target Temperature</span>
            <div class="nwp-val" id="tempDisplay">${currentTemp}°</div>
            <input type="range" class="nwp-slider" id="tempSlider" min="${minTemp}" max="${maxTemp}" value="${currentTemp}" step="1">
          </div>

          <div class="nwp-actions">
            <button class="nwp-action nwp-cancel" id="nwpCancel">Cancel</button>
            <button class="nwp-action nwp-primary" id="nwpSave">Save</button>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(div);
    this._modalEl = div;

    // Handlers
    const close = () => { div.remove(); this._modalEl = null; };
    div.querySelector('#nwpClose').addEventListener('click', close);
    div.querySelector('#nwpCancel').addEventListener('click', close);

    const slider = div.querySelector('#tempSlider');
    const display = div.querySelector('#tempDisplay');
    slider.addEventListener('input', (e) => { display.textContent = e.target.value + '°'; });

    const btns = div.querySelectorAll('.nwp-btn');
    btns.forEach(b => b.addEventListener('click', () => {
      btns.forEach(btn => btn.classList.remove('active'));
      b.classList.add('active');
      // Show/hide temp slider based on mode? (Off/Vacation might not allow temp set)
    }));

    div.querySelector('#nwpSave').addEventListener('click', async () => {
      const modeBtn = div.querySelector('.nwp-btn.active');
      const mode = modeBtn ? modeBtn.dataset.mode : null;
      const temp = slider.value;

      if (mode) {
        // Set mode
        if (mode === 'vacation') {
          await this._hass.callService('water_heater', 'set_away_mode', {
            entity_id: stateObj.entity_id,
            away_mode: true
          });
        } else if (mode === 'off') {
          // Use turn_off service for Off mode
          await this._hass.callService('water_heater', 'turn_off', {
            entity_id: stateObj.entity_id
          });
        } else {
          // For other modes, ensure away mode is off first if active
          if (stateObj.attributes.away_mode === 'on') {
            await this._hass.callService('water_heater', 'set_away_mode', {
              entity_id: stateObj.entity_id,
              away_mode: false
            });
          }
          await this._hass.callService('water_heater', 'set_operation_mode', {
            entity_id: stateObj.entity_id,
            operation_mode: mode
          });
        }
      }

      if (temp != currentTemp) {
        await this._hass.callService('water_heater', 'set_temperature', {
          entity_id: stateObj.entity_id,
          temperature: Number(temp)
        });
      }
      close();
    });
  }

  async _fetchHistory(entityId) {
    const end = new Date();
    const start = new Date(end.getTime() - 24 * 60 * 60 * 1000); // 24h ago
    const startStr = start.toISOString();
    const endStr = end.toISOString();

    // minimal_response to save bandwidth, significant_changes_only=0 for resolution
    try {
      const history = await this._hass.callApi('GET', `history/period/${startStr}?filter_entity_id=${entityId}&end_time=${endStr}&minimal_response`);
      return history[0] || [];
    } catch (e) {
      console.warn('NWP500 Card: Failed to fetch history', e);
      return [];
    }
  }

  _renderSparkline(history, width, height) {
    if (!history || history.length < 2) return '';

    // Parse data
    const data = history.map(h => ({
      t: new Date(h.last_changed).getTime(),
      v: Number(h.state)
    })).filter(d => !isNaN(d.v));

    if (data.length === 0) return '';

    const minTime = data[0].t;
    const maxTime = data[data.length - 1].t;
    const timeRange = maxTime - minTime;

    // Find min/max value for scaling
    let minVal = data[0].v;
    let maxVal = data[0].v;
    data.forEach(d => {
      if (d.v < minVal) minVal = d.v;
      if (d.v > maxVal) maxVal = d.v;
    });

    // Add some padding
    const range = maxVal - minVal;
    const padding = range * 0.1 || 1; // Avoid divide by zero if flat
    const plotMin = minVal - padding;
    const plotMax = maxVal + padding;
    const plotRange = plotMax - plotMin;

    // Generate path
    // X = (t - minTime) / timeRange * width
    // Y = height - (v - plotMin) / plotRange * height

    let path = `M 0,${height} `; // Start bottom-left

    data.forEach((d, i) => {
      const x = ((d.t - minTime) / timeRange) * width;
      const y = height - ((d.v - plotMin) / plotRange) * height;
      if (i === 0) path = `M ${x},${y} `;
      else path += `L ${x},${y} `;
    });

    return path;
  }

  async _showHistoryModal(title, state, unit, entityId) {
    if (this._modalEl) return;

    // Fetch history first (show loading?)
    // Let's create modal with loading state
    const modalStyle = `
      .nwp-modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.6); z-index: 9999; display: flex; align-items: center; justify-content: center; backdrop-filter: blur(4px); }
      .nwp-modal { background: #222; border: 1px solid #444; border-radius: 12px; width: 90%; max-width: 400px; padding: 20px; color: #fff; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
      .nwp-h { font-size: 18px; margin-bottom: 20px; font-weight: 600; display: flex; justify-content: space-between; }
      .nwp-stat-row { display: flex; align-items: baseline; gap: 8px; margin-bottom: 10px; }
      .nwp-stat-val { font-size: 32px; font-weight: 300; }
      .nwp-stat-unit { font-size: 16px; color: #aaa; }
      .nwp-chart-container { width: 100%; height: 150px; background: rgba(255,255,255,0.05); border-radius: 8px; overflow: hidden; position: relative; }
      .nwp-chart-svg { width: 100%; height: 100%; }
      .nwp-chart-path { fill: none; stroke: #00BCD4; stroke-width: 2; vector-effect: non-scaling-stroke; }
      .nwp-loading { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #aaa; font-size: 12px; }
    `;

    const div = document.createElement('div');
    div.innerHTML = `
      <style>${modalStyle}</style>
      <div class="nwp-modal-overlay">
        <div class="nwp-modal">
          <div class="nwp-h">
            <span>${title}</span>
            <span style="cursor:pointer" id="nwpClose">✕</span>
          </div>
          <div class="nwp-stat-row">
            <span class="nwp-stat-val">${state}</span>
            <span class="nwp-stat-unit">${unit}</span>
          </div>
          <div class="nwp-chart-container">
            <div class="nwp-loading">Loading 24h History...</div>
            <svg class="nwp-chart-svg" viewBox="0 0 400 150" preserveAspectRatio="none">
              <path class="nwp-chart-path" d="" />
            </svg>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(div);
    this._modalEl = div;

    const close = () => { div.remove(); this._modalEl = null; };
    div.querySelector('#nwpClose').addEventListener('click', close);
    div.querySelector('.nwp-modal-overlay').addEventListener('click', (e) => {
      if (e.target === div.querySelector('.nwp-modal-overlay')) close();
    });

    // Fetch and render
    const history = await this._fetchHistory(entityId);
    const path = this._renderSparkline(history, 400, 150);

    const svgPath = div.querySelector('.nwp-chart-path');
    const loading = div.querySelector('.nwp-loading');

    if (loading) loading.style.display = 'none';
    if (svgPath) {
      if (path) svgPath.setAttribute('d', path);
      else if (loading) {
        loading.textContent = 'No data available';
        loading.style.display = 'block';
      }
    }
  }
}

class NWP500VisualCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this.innerHTML = `
      <div class="card-config">
        <div>
          <paper-input label="Entity" .value="${config.entity}" class="config-entity"></paper-input>
        </div>
      </div>
    `;
    this.querySelector('.config-entity').addEventListener('change', this._valueChanged.bind(this));
  }

  _valueChanged(ev) {
    if (!this._config || !this._hass) return;
    const target = ev.target;
    // Simple single value update for now
    if (this._config.entity === target.value) return;

    const config = {
      ...this._config,
      entity: target.value
    };
    const event = new Event("config-changed", {
      bubbles: true,
      composed: true
    });
    event.detail = { config };
    this.dispatchEvent(event);
  }
}

customElements.define('nwp500-visual-card-editor', NWP500VisualCardEditor);
customElements.define('nwp500-visual-card', NWP500VisualCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "nwp500-visual-card",
  name: "NWP500 Visual Card",
  preview: true,
  description: "Visual representation of the NWP500 water heater status"
});
