/**
 * NWP500 Visual Status Card
 * Visualizes water heater status using a device image with data overlays.
 */

const CARD_VERSION = '2.1.6';

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
        <div class="card-content">
          <div class="visual-container">
            <!-- Background Image -->
            <img src="/nwp500/nwp500-visual-card.png?v=2.0.11" class="bg-image" crossOrigin="anonymous" id="bgImage">

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

            <!-- Current Mode Indicator (Bottom Center of Image - Optional context) -->
            <!-- Removing from image area as requested to avoid overlap -->
          </div>

          <!-- Stats Footer -->
          <div class="stats-container">
            <!-- DHW Outlet -->
            <div class="stat-item">
              <div class="stat-icon-circle"><ha-icon icon="mdi:thermometer"></ha-icon></div>
              <div class="stat-info">
                <div class="stat-label">Outlet</div>
                <div class="stat-value">${dhwTemp}</div>
              </div>
            </div>

            <!-- DHW Charge -->
            <div class="stat-item">
              <div class="stat-icon-circle"><ha-icon icon="mdi:water-percent"></ha-icon></div>
              <div class="stat-info">
                <div class="stat-label">Charge</div>
                <div class="stat-value">${dhwCharge}</div>
              </div>
            </div>

            <!-- Lower Tank -->
            <div class="stat-item">
              <div class="stat-icon-circle"><ha-icon icon="mdi:thermometer-low"></ha-icon></div>
              <div class="stat-info">
                <div class="stat-label">Lower</div>
                <div class="stat-value">${tankLower}</div>
              </div>
            </div>

            <!-- Mode (Moved from image) -->
            <div class="stat-item mode-stat">
              <div class="stat-icon-circle"><ha-icon icon="${modeData.icon}"></ha-icon></div>
              <div class="stat-info">
                <div class="stat-label">Mode</div>
                <div class="stat-value">${modeFriendly}</div>
              </div>
            </div>
          </div>
        </div>
      </ha-card>
    `;

    // Image Analysis for correct positioning
    const img = this.shadowRoot.getElementById('bgImage');
    const screenEl = this.shadowRoot.getElementById('screenArea');
    const heatingEl = this.shadowRoot.getElementById('heatingStatus'); // Might be null if not burning

    img.onload = () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);

        const w = canvas.width;
        const h = canvas.height;
        const imageData = ctx.getImageData(0, 0, w, h).data;

        // Strategy: Scan vertical center line to find the dark screen box
        // We expect it roughly in the top half.
        // Dark threshold: R,G,B all < 40 (tuned for black screen)

        const centerX = Math.floor(w / 2);
        let firstY = -1;
        let lastY = -1;

        // 1. Find vertical bounds (top/bottom)
        for (let y = 0; y < h; y++) {
          const idx = (y * w + centerX) * 4;
          const r = imageData[idx];
          const g = imageData[idx + 1];
          const b = imageData[idx + 2];

          const isDark = r < 50 && g < 50 && b < 50;

          if (isDark) {
            if (firstY === -1) firstY = y;
            lastY = y;
          } else if (firstY !== -1 && y > firstY + 20) {
            // If we found a block and now hit light again (and block was big enough), stop.
            // This prevents finding the bottom footer or feet.
            break;
          }
        }

        if (firstY !== -1 && lastY !== -1) {
          // 2. Find horizontal bounds (left/right) at the vertical center of the detected block
          const centerY = Math.floor((firstY + lastY) / 2);
          let firstX = -1;
          let lastX = -1;

          for (let x = 0; x < w; x++) {
            const idx = (centerY * w + x) * 4;
            const r = imageData[idx];
            const g = imageData[idx + 1];
            const b = imageData[idx + 2];
            const isDark = r < 50 && g < 50 && b < 50;

            if (isDark) {
              if (firstX === -1) firstX = x;
              lastX = x;
            } else if (firstX !== -1 && x > firstX + 20) {
              // End of screen
              break;
            }
          }

          if (firstX !== -1 && lastX !== -1) {
            // Refine edges slightly (shrink by 1-2% to be safe inside bezel)
            const padX = (lastX - firstX) * 0.05;
            const padY = (lastY - firstY) * 0.05;

            const finalTop = ((firstY + padY) / h) * 100;
            const finalHeight = ((lastY - firstY - (padY * 2)) / h) * 100;
            const finalLeft = ((firstX + padX) / w) * 100;
            const finalWidth = ((lastX - firstX - (padX * 2)) / w) * 100;

            // Apply to screen element
            screenEl.style.top = `${finalTop}%`;
            screenEl.style.height = `${finalHeight}%`;
            screenEl.style.left = `${finalLeft + (finalWidth / 2)}%`; // Styles use center transform
            screenEl.style.width = `${finalWidth}%`;

            // Apply to heating element (just below bottom edge)
            if (heatingEl) {
              const heatingTop = ((lastY / h) * 100) + 2; // 2% margin below screen
              heatingEl.style.top = `${heatingTop}%`;
            }
          }
        }
      } catch (e) {
        console.warn('NWP500 Card: Auto-detection failed (likely CORS), using CSS fallback.', e);
      }
    };

    // Attach listeners
    this.shadowRoot.getElementById('screenArea').addEventListener('click', () => {
      this._showControlModal(stateObj, targetTemp, minTemp, maxTemp, settingFriendly);
    });

    if (this._modalEl) {
      // Modal handling
    }
  }

  _getStyles() {
    return `
      :host { display: block; }
      ha-card {
        overflow: hidden;
        background: var(--ha-card-background, var(--card-background-color, white));
        border-radius: var(--ha-card-border-radius, 4px);
        box-shadow: var(--ha-card-box-shadow, 0px 2px 1px -1px rgba(0,0,0,0.2), 0px 1px 1px 0px rgba(0,0,0,0.14), 0px 1px 3px 0px rgba(0,0,0,0.12));
      }
      .card-content {
        padding: 0;
        display: flex;
        flex-direction: column;
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
      .screen-overlay {
        top: 17%;
        left: 50%;
        transform: translateX(-50%);
        width: 24%;
        height: 20%;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .screen-content {
        color: #fff;
        text-shadow: 0 0 10px rgba(0,255,0,0.4);
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        width: 100%;
        height: 100%;
      }
      .screen-temp { font-size: clamp(12px, 3.0vw, 22px); font-weight: 700; line-height: 1; letter-spacing: 1px; margin-bottom: 2px; }
      .screen-mode { 
        font-size: clamp(7px, 1.0vw, 10px); 
        text-transform: uppercase; 
        font-weight: 500; 
        opacity: 0.9;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 3px;
      }
      .screen-mode ha-icon { --mdc-icon-size: 14px; }
      .screen-mode span { line-height: 1; margin-top: 1px; }
      
      /* Heating Status - Independent Fire Icon */
      .heating-status {
        top: 38%;
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

      /* Stats Footer */
      .stats-container {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 8px;
        padding: 16px;
        background: var(--primary-background-color, #fafafa);
        border-top: 1px solid var(--divider-color, #e0e0e0);
      }
      .stat-item {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: flex-start;
        text-align: center;
        cursor: pointer;
      }
      .stat-icon-circle {
        background: var(--card-background-color, white);
        border: 1px solid var(--divider-color, #e0e0e0);
        border-radius: 50%;
        width: 36px;
        height: 36px;
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 4px;
        color: var(--primary-color, #03a9f4);
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
      }
      .stat-icon-circle ha-icon { --mdc-icon-size: 18px; }
      
      .stat-info {
        display: flex;
        flex-direction: column;
      }
      .stat-label {
        font-size: 10px;
        text-transform: uppercase;
        color: var(--secondary-text-color, #727272);
        margin-bottom: 2px;
      }
      .stat-value {
        font-size: 13px;
        font-weight: 500;
        color: var(--primary-text-color, #212121);
      }
      
      /* Mode stat specific style if needed */
      .mode-stat .stat-value {
        text-transform: capitalize;
        font-size: 12px;
        line-height: 1.1;
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

    config = {
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
