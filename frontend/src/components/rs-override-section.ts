import { LitElement, html, css, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import type { HomeAssistant, RoomConfig, ClimateMode, OverrideType } from "../types";
import { localize } from "../utils/localize";
import {
  formatTemp,
  tempUnit,
  toDisplay,
  toCelsius,
  tempStep,
  tempRange,
} from "../utils/temperature";

@customElement("rs-override-section")
export class RsOverrideSection extends LitElement {
  @property({ attribute: false }) public hass!: HomeAssistant;
  @property({ attribute: false }) public config!: RoomConfig;
  @property() public climateMode: ClimateMode = "auto";
  @property({ type: Number }) public comfortHeat = 21.0;
  @property({ type: Number }) public comfortCool = 24.0;
  @property({ type: Number }) public ecoHeat = 17.0;
  @property({ type: Number }) public ecoCool = 27.0;
  @property() public language = "en";

  @state() private _overridePending: OverrideType | null = null;
  @state() private _overrideCustomTemp = 21;
  @state() private _overrideCustomHeat = 21;
  @state() private _overrideCustomCool = 24;
  @state() private _overrideError = "";
  @state() private _optimisticOverride: {
    type: OverrideType;
    temp: number | null;
    heatTemp: number | null;
    coolTemp: number | null;
    until: number | null;
  } | null = null;
  @state() private _optimisticClear = false;

  static styles = css`
    :host {
      display: block;
    }

    .override-divider {
      border: none;
      border-top: 1px solid var(--divider-color, #e0e0e0);
      margin: 16px 0 12px;
    }

    .override-label {
      font-size: 13px;
      font-weight: 500;
      color: var(--secondary-text-color);
      margin-bottom: 10px;
    }

    .override-presets {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .override-preset {
      cursor: pointer;
      border: 1px solid var(--divider-color, #e0e0e0);
      border-radius: 8px;
      padding: 6px 12px;
      font-size: 13px;
      background: transparent;
      color: var(--primary-text-color);
      display: flex;
      align-items: center;
      gap: 6px;
      transition:
        background 0.15s,
        border-color 0.15s;
    }

    .override-preset:hover {
      background: rgba(0, 0, 0, 0.04);
    }

    .override-preset.pending {
      border-color: var(--primary-color);
      background: rgba(var(--rgb-primary-color, 33, 150, 243), 0.08);
    }

    .override-preset.active.boost {
      border-color: var(--warning-color, #ff9800);
      background: rgba(255, 152, 0, 0.15);
      color: var(--warning-color, #ff9800);
    }

    .override-preset.active.eco {
      border-color: #4caf50;
      background: rgba(76, 175, 80, 0.15);
      color: #4caf50;
    }

    .override-preset.active.custom {
      border-color: #2196f3;
      background: rgba(33, 150, 243, 0.15);
      color: #2196f3;
    }

    .override-preset:disabled {
      opacity: 0.35;
      cursor: default;
    }

    .override-preset:disabled:hover {
      background: transparent;
    }

    .override-preset ha-icon {
      --mdc-icon-size: 16px;
    }

    .override-duration {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
      align-items: center;
    }

    .override-duration-label {
      font-size: 12px;
      color: var(--secondary-text-color);
    }

    .override-dur-chip {
      cursor: pointer;
      border: 1px solid var(--divider-color, #e0e0e0);
      border-radius: 16px;
      padding: 4px 12px;
      font-size: 12px;
      background: transparent;
      color: var(--primary-text-color);
      transition: background 0.15s;
    }

    .override-dur-chip:hover {
      background: rgba(0, 0, 0, 0.04);
    }

    .override-dur-chip:disabled {
      opacity: 0.5;
      pointer-events: none;
    }

    .override-custom-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 8px;
    }

    .override-custom-row input {
      width: 56px;
      padding: 4px 8px;
      border: 1px solid var(--divider-color, #e0e0e0);
      border-radius: 8px;
      font-size: 13px;
      text-align: center;
      background: transparent;
      color: var(--primary-text-color);
    }

    .override-custom-row span {
      font-size: 12px;
      color: var(--secondary-text-color);
    }

    .override-error {
      color: var(--error-color, #d32f2f);
      font-size: 12px;
      margin-top: 6px;
    }
  `;

  updated(changedProps: Map<string, unknown>) {
    // Clear optimistic override state once server data catches up
    if (changedProps.has("config") && this.config?.live) {
      const live = this.config.live;
      if (this._optimisticOverride && live.override_active) {
        this._optimisticOverride = null;
      }
      if (this._optimisticClear && !live.override_active) {
        this._optimisticClear = false;
      }
    }
  }

  /** Compute effective override state from optimistic + server data. */
  getEffectiveOverride(): {
    active: boolean;
    type: OverrideType | null;
    temp: number | null;
    heatTemp: number | null;
    coolTemp: number | null;
    until: number | null;
  } {
    if (this._optimisticClear) {
      return { active: false, type: null, temp: null, heatTemp: null, coolTemp: null, until: null };
    }
    if (this._optimisticOverride) {
      return {
        active: true,
        type: this._optimisticOverride.type,
        temp: this._optimisticOverride.temp,
        heatTemp: this._optimisticOverride.heatTemp,
        coolTemp: this._optimisticOverride.coolTemp,
        until: this._optimisticOverride.until,
      };
    }
    const live = this.config?.live;
    if (live?.override_active && live.override_type) {
      return {
        active: true,
        type: live.override_type,
        temp: live.override_temp,
        heatTemp: live.override_heat_temp ?? null,
        coolTemp: live.override_cool_temp ?? null,
        until: live.override_until,
      };
    }
    return { active: false, type: null, temp: null, heatTemp: null, coolTemp: null, until: null };
  }

  render() {
    const ov = this.getEffectiveOverride();

    return html`
      <hr class="override-divider" />
      <div class="override-label">${localize("override.label", this.language)}</div>
      ${this._renderOverrideButtons(ov)}
      ${this._overrideError
        ? html`<div class="override-error">${this._overrideError}</div>`
        : nothing}
    `;
  }

  private _renderOverrideButtons(ov: ReturnType<typeof this.getEffectiveOverride>) {
    const activeType = ov.active ? ov.type : null;
    const showDuration = !activeType && this._overridePending;
    const supportsRange = this._supportsTargetRange();

    return html`
      <div class="override-presets">
        ${(["boost", "eco", "custom"] as OverrideType[]).map((t) => {
          const isActive = activeType === t;
          const isDisabled = activeType !== null && !isActive;
          const isPending = !activeType && this._overridePending === t;

          return html`
            <button
              class="override-preset ${t} ${isActive ? "active" : ""} ${isPending ? "pending" : ""}"
              ?disabled=${isDisabled}
              @click=${() => (isActive ? this._onClearOverride() : this._onOverridePreset(t))}
            >
              <ha-icon
                icon=${t === "boost" ? "mdi:fire" : t === "eco" ? "mdi:leaf" : "mdi:thermometer"}
              ></ha-icon>
              ${t === "boost"
                ? `${localize("override.comfort", this.language)} ${this._formatOverrideSummary(this.comfortHeat, this.comfortCool, supportsRange)}`
                : t === "eco"
                  ? `${localize("override.eco", this.language)} ${this._formatOverrideSummary(this.ecoHeat, this.ecoCool, supportsRange)}`
                  : localize("override.custom", this.language)}
            </button>
          `;
        })}
      </div>
      ${showDuration
        ? html`
            ${this._overridePending === "custom"
              ? html`
                  ${supportsRange
                    ? html`
                        <div class="override-custom-row">
                          <span>${localize("override.target_heat", this.language)}</span>
                          <input
                            type="number"
                            min=${tempRange(5, 35, this.hass).min}
                            max=${tempRange(5, 35, this.hass).max}
                            step=${tempStep(this.hass)}
                            .value=${String(toDisplay(this._overrideCustomHeat, this.hass))}
                            @input=${this._onOverrideCustomHeatInput}
                          />
                          <span>${tempUnit(this.hass)}</span>
                        </div>
                        <div class="override-custom-row">
                          <span>${localize("override.target_cool", this.language)}</span>
                          <input
                            type="number"
                            min=${tempRange(5, 35, this.hass).min}
                            max=${tempRange(5, 35, this.hass).max}
                            step=${tempStep(this.hass)}
                            .value=${String(toDisplay(this._overrideCustomCool, this.hass))}
                            @input=${this._onOverrideCustomCoolInput}
                          />
                          <span>${tempUnit(this.hass)}</span>
                        </div>
                      `
                    : html`
                        <div class="override-custom-row">
                          <span>${localize("override.target", this.language)}</span>
                          <input
                            type="number"
                            min=${tempRange(5, 35, this.hass).min}
                            max=${tempRange(5, 35, this.hass).max}
                            step=${tempStep(this.hass)}
                            .value=${String(toDisplay(this._overrideCustomTemp, this.hass))}
                            @input=${this._onOverrideCustomTempInput}
                          />
                          <span>${tempUnit(this.hass)}</span>
                        </div>
                      `}
                `
              : nothing}
            <div class="override-duration">
              <span class="override-duration-label"
                >${localize("override.activate_for", this.language)}</span
              >
              ${[
                { label: "1h", hours: 1 },
                { label: "2h", hours: 2 },
                { label: "4h", hours: 4 },
              ].map(
                (opt) => html`
                  <button
                    class="override-dur-chip"
                    @click=${() => this._onOverrideActivate(opt.hours)}
                  >
                    ${opt.label}
                  </button>
                `,
              )}
            </div>
          `
        : nothing}
    `;
  }

  private _onOverridePreset(type: OverrideType): void {
    if (this._overridePending === type) {
      this._overridePending = null;
    } else {
      this._overridePending = type;
      if (type === "custom") {
        this._overrideCustomTemp = this.climateMode === "cool_only" ? this.comfortCool : this.comfortHeat;
        this._overrideCustomHeat = this.comfortHeat;
        this._overrideCustomCool = this.comfortCool;
      }
    }
    this._overrideError = "";
  }

  private _onOverrideCustomTempInput(e: Event): void {
    this._overrideCustomTemp = toCelsius(
      Number((e.target as HTMLInputElement).value) || toDisplay(21, this.hass),
      this.hass,
    );
  }

  private _onOverrideCustomHeatInput(e: Event): void {
    this._overrideCustomHeat = toCelsius(
      Number((e.target as HTMLInputElement).value) || toDisplay(this.comfortHeat, this.hass),
      this.hass,
    );
  }

  private _onOverrideCustomCoolInput(e: Event): void {
    this._overrideCustomCool = toCelsius(
      Number((e.target as HTMLInputElement).value) || toDisplay(this.comfortCool, this.hass),
      this.hass,
    );
  }

  private async _onOverrideActivate(hours: number): Promise<void> {
    if (!this._overridePending || !this.config) return;

    const pendingType = this._overridePending;
    const supportsRange = this._supportsTargetRange();
    let temp: number | null = null;
    let heatTemp: number | null = null;
    let coolTemp: number | null = null;
    if (pendingType === "boost") {
      if (supportsRange) {
        heatTemp = this.comfortHeat;
        coolTemp = this.comfortCool;
      } else {
        temp = this.climateMode === "cool_only" ? this.comfortCool : this.comfortHeat;
      }
    } else if (pendingType === "eco") {
      if (supportsRange) {
        heatTemp = this.ecoHeat;
        coolTemp = this.ecoCool;
      } else {
        temp = this.climateMode === "cool_only" ? this.ecoCool : this.ecoHeat;
      }
    } else {
      if (supportsRange) {
        heatTemp = Math.min(this._overrideCustomHeat, this._overrideCustomCool);
        coolTemp = Math.max(this._overrideCustomHeat, this._overrideCustomCool);
      } else {
        temp = this._overrideCustomTemp;
      }
    }

    this._optimisticOverride = {
      type: pendingType,
      temp,
      heatTemp,
      coolTemp,
      until: Date.now() / 1000 + hours * 3600,
    };
    this._optimisticClear = false;
    this._overridePending = null;
    this._overrideError = "";

    const msg: Record<string, unknown> = {
      type: "roommind/override/set",
      area_id: this.config.area_id,
      override_type: pendingType,
      duration: hours,
    };
    if (pendingType === "custom") {
      if (supportsRange) {
        msg.target_temp_low = heatTemp;
        msg.target_temp_high = coolTemp;
      } else {
        msg.temperature = temp;
      }
    }

    try {
      await this.hass.callWS(msg);
      this._fireRoomUpdated();
    } catch (err) {
      this._optimisticOverride = null;
      this._overrideError =
        err instanceof Error ? err.message : localize("override.error_set", this.language);
      // eslint-disable-next-line no-console
      console.error("Override set failed:", err);
    }
  }

  private async _onClearOverride(): Promise<void> {
    if (!this.config) return;

    this._optimisticClear = true;
    this._optimisticOverride = null;
    this._overrideError = "";

    try {
      await this.hass.callWS({
        type: "roommind/override/clear",
        area_id: this.config.area_id,
      });
      this._fireRoomUpdated();
    } catch (err) {
      this._optimisticClear = false;
      this._overrideError =
        err instanceof Error ? err.message : localize("override.error_clear", this.language);
      // eslint-disable-next-line no-console
      console.error("Override clear failed:", err);
    }
  }

  private _fireRoomUpdated(): void {
    this.dispatchEvent(new CustomEvent("room-updated", { bubbles: true, composed: true }));
  }

  private _supportsTargetRange(): boolean {
    const liveClimate = this.config?.live?.climate;
    if (liveClimate?.supports_target_range !== undefined) {
      return Boolean(liveClimate.supports_target_range);
    }
    if (liveClimate?.target_temperature_low != null || liveClimate?.target_temperature_high != null) {
      return true;
    }
    if (this.config?.live?.heat_target != null || this.config?.live?.cool_target != null) {
      return this.config.live.heat_target != null && this.config.live.cool_target != null;
    }
    const devices = this.config?.devices ?? [];
    const hasHeat = Boolean(devices.some((device) => device.type === "trv") || this.config?.thermostats?.length);
    const hasCool = Boolean(devices.some((device) => device.type === "ac") || this.config?.acs?.length);
    return hasHeat && hasCool;
  }

  private _formatOverrideSummary(heat: number, cool: number, supportsRange: boolean): string {
    if (!supportsRange) {
      const value = this.climateMode === "cool_only" ? cool : heat;
      return `${formatTemp(value, this.hass)}${tempUnit(this.hass)}`;
    }
    if (Math.abs(heat - cool) <= 0.05) {
      return `${formatTemp(heat, this.hass)}${tempUnit(this.hass)}`;
    }
    return `${formatTemp(heat, this.hass)}-${formatTemp(cool, this.hass)}${tempUnit(this.hass)}`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "rs-override-section": RsOverrideSection;
  }
}
