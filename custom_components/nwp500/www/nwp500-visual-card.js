/**
 * NWP500 Visual Status Card
 * Visualizes water heater status using a device image with data overlays.
 */

const CARD_VERSION = '2.1.1';

class NWP500VisualCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass = null;
    this._modalEl = null;
  }

  static getConfigElement() {
    // Just a stub for now, use YAML
    return document.createElement('div');
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
    const modeFriendly = modeData.label;
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
    const now = Date.now();

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
          <img src="/nwp500/nwp500-visual-card.png?v=2.0.11" class="bg-image">

          <!-- Overlays -->
          
          <!-- Screen Area (Control Panel) -->
          <div class="overlay screen-overlay" id="screenArea">
            <div class="screen-content ${isError ? 'error' : ''}">
              <div class="screen-temp">${targetDisplay}</div>
              <div class="screen-mode">
                <ha-icon icon="${modeData.icon}"></ha-icon>
                <span>${modeData.label}</span>
              </div>
              ${isBurning ? '<ha-icon icon="mdi:fire" class="burning-icon"></ha-icon>' : ''}
            </div>
          </div>

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
          
          <!-- Current Mode Indicator (Bottom Center) -->
          <div class="overlay mode-indicator">
            <span class="mode-label">Running:</span>
            <span class="mode-value">${modeFriendly}</span>
          </div>

        </div>
      </ha-card>
    `;

    // Attach listeners
    this.shadowRoot.getElementById('screenArea').addEventListener('click', () => {
      this._showControlModal(stateObj, targetTemp, minTemp, maxTemp, settingFriendly);
    });

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
        background: none;
        border: none;
        box-shadow: none;
      }
      .visual-container {
        position: relative;
        width: 100%;
        max-width: 400px;
        margin: 0 auto;
        aspect-ratio: 1; /* Square-ish for new image */
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
        top: 17%; /* Moved down to match physical screen top edge */
        left: 50%;
        transform: translateX(-50%);
        width: 24%;
        height: 20%; /* Expanded height to allow true vertical centering */
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
      .screen-temp { font-size: clamp(14px, 3.5vw, 28px); font-weight: 700; line-height: 1; letter-spacing: 1px; margin-bottom: 2px; }
      .screen-mode { 
        font-size: clamp(8px, 1.2vw, 12px); 
        text-transform: uppercase; 
        font-weight: 500; 
        opacity: 0.9;
        display: flex;
        align-items: center;
        gap: 4px;
      }
      .screen-mode ha-icon { --mdc-icon-size: 14px; }
      .screen-mode span { line-height: 1; margin-top: 1px; }
      
      /* Badges - On the Tank */
      .badge {
        background: rgba(0, 0, 0, 0.6); /* Darker, more transparent */
        backdrop-filter: blur(4px);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 50%; /* Circular or pill? Let's try compact pills or circles */
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

      /* Positions on the Tank Image */
      /* Outlet (Hot) - Left Shoulder */
      .outlet-badge { top: 25%; left: 22%; }
      
      /* Charge - Right Shoulder or Center */
      /* Let's put Charge on the right shoulder matching outlet */
      .charge-badge { top: 25%; right: 22%; }
      
      /* Lower Temp - Bottom area */
      .lower-badge { bottom: 25%; left: 50%; transform: translateX(-50%); } 

      /* Mode Indicator - Keep at bottom or remove if redundant with screen? */
      /* User wanted 'other measurements resized'. Mode is on screen too. */
      /* Let's keep the pill at the very bottom overlapping the tank feet/base */
      .mode-indicator {
        bottom: 5%; left: 50%; transform: translateX(-50%);
        background: rgba(0,0,0,0.8);
        padding: 4px 12px; border-radius: 12px;
        border: none;
      }
      .mode-label { display: none; }
      .mode-value { font-size: 10px; color: #ccc; letter-spacing: 0.5px; }
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
}

customElements.define('nwp500-visual-card', NWP500VisualCard);
