// <qse-mode-switch>: Total · Stacked · Lines · Split · Table for one chart (VIS-242). The choice is kept
// per chart id (charts/modes.ts savedMode) and announced as a `qse-mode` event; Stacked is off for
// values that do not add up, with the reason in its title.

import { html } from 'lit';
import { customElement, property } from 'lit/decorators.js';
import type { Agg } from '../api/fleet';
import { LightElement } from '../ui/base';
import { CHART_MODES, modeRefusal, saveMode, type ChartMode } from './modes';

export interface ModeEvent {
  id: string;
  mode: ChartMode;
}

@customElement('qse-mode-switch')
export class QseModeSwitch extends LightElement {
  @property({ type: String, attribute: 'chart-id' }) chartId = '';
  @property({ type: String }) agg: Agg = 'sum';
  @property({ type: String }) value: ChartMode = 'lines';

  private pick(mode: ChartMode) {
    if (mode === this.value) return;
    this.value = mode;
    if (this.chartId) saveMode(this.chartId, mode);
    this.dispatchEvent(new CustomEvent<ModeEvent>('qse-mode', { detail: { id: this.chartId, mode }, bubbles: true }));
  }

  render() {
    return html`<div class="seg mode-switch" role="group" aria-label="Chart mode">
      ${CHART_MODES.map(({ mode, label }) => {
        const why = modeRefusal(mode, this.agg);
        return html`<button type="button" class="seg-btn ${this.value === mode ? 'is-on' : ''}" aria-pressed=${this.value === mode} ?disabled=${why != null} title=${why ?? label} @click=${() => this.pick(mode)}>${label}</button>`;
      })}
    </div>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-mode-switch': QseModeSwitch;
  }
}
