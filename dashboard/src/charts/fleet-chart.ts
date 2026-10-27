// <qse-fleet-chart>: one metric over time for every node in scope (VIS-242), in the mode the user
// picks with its <qse-mode-switch>: Total (the aggregate in signal orange so it never reads as a
// node, glowing; max with the mean dashed for temperatures), Stacked (sums only), Lines (node
// colours, dashed for colours 5..8), Split (small multiples, one shared y axis) or Table. A capacity
// line when the metric has one, event markers, a crosshair with a tooltip that lists every node and
// the aggregate, arrow keys to walk it. The series come from the fleet API as they are; modes.ts
// computes the rest.

import { html, nothing, svg, type SVGTemplateResult, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import type { Agg } from '../api/fleet';
import { isHollow, nodeChip, nodeColour } from '../ui/node-chip';
import { ChartElement, nextId } from './chart-base';
import { AGG_OP, chartModel, modeNote, savedMode, stats, tableRows, timeLabel, type ChartMode, type ChartModel, type NodeSeries } from './modes';
import './mode-switch';
import type { ModeEvent } from './mode-switch';
import { labelIndices, linear, monotonePath, bandPath, nearestIndex, niceTicks, type Pt } from './svg';

export interface ChartEvent {
  at: number;
  text: string;
  alert?: boolean;
}

interface Line {
  values: (number | null)[];
  colour: string;
  glow?: boolean;
  dashed?: boolean;
  width?: number;
}

interface Area {
  lo: (number | null)[];
  hi: (number | null)[];
  colour: string;
  opacity: number;
  edge?: boolean;
}

/** one plot area: a cell of Split, or the whole chart */
interface Cell {
  gx: number;
  gy: number;
  w: number;
  h: number;
  yLab: boolean;
  xLab: boolean;
  lines: Line[];
  areas: Area[];
  title?: SVGTemplateResult;
}

const TOTAL = 'var(--orange)';
const PAD = { l: 44, r: 10, t: 10, b: 24 };

@customElement('qse-fleet-chart')
export class QseFleetChart extends ChartElement {
  /** the chart's id: its chosen mode is kept under it */
  @property({ type: String, attribute: 'chart-id' }) chartId = '';
  @property({ attribute: false }) series: NodeSeries[] = [];
  @property({ type: String }) agg: Agg = 'sum';
  @property({ type: String }) unit = '';
  /** the first sample's time (Unix seconds) and the step between samples */
  @property({ type: Number }) t0 = 0;
  @property({ type: Number }) step = 1;
  @property({ type: Number }) decimals = 0;
  /** per-node capacity (memory), drawn dashed */
  @property({ attribute: false }) cap: number | null = null;
  /** a fixed axis top (percentages) */
  @property({ attribute: false }) max: number | null = null;
  @property({ attribute: false }) events: ChartEvent[] = [];
  /** the mode until the user picks one */
  @property({ type: String }) mode: ChartMode = 'lines';
  @property({ type: Boolean, attribute: 'no-switch' }) noSwitch = false;
  @property({ type: Number, attribute: 'split-height' }) splitHeight = 104;
  @property({ type: String, attribute: 'aria-label' }) ariaLabelText = '';
  @state() private chosen: ChartMode | null = null;
  @state() private hover: number | null = null;
  /** the Split cell under the pointer: the tooltip sits beside it */
  @state() private hoverCell = 0;
  private glowId = nextId('fc');

  protected willUpdate(changed: Map<string, unknown>): void {
    if (changed.has('chartId')) this.chosen = this.chartId ? savedMode(this.chartId) : null;
  }

  private fmt = (v: number | null | undefined): string => (v == null ? '—' : v.toLocaleString('en-US', { minimumFractionDigits: this.decimals, maximumFractionDigits: this.decimals }));
  private withUnit = (v: number | null | undefined) => (v == null ? '—' : `${this.fmt(v)}${this.unit ? ' ' + this.unit : ''}`);

  private tick(t: number, top: number): string {
    if (top >= 10000) return `${(t / 1000).toLocaleString('en-US', { maximumFractionDigits: top >= 100000 ? 0 : 1 })}k`;
    return t.toLocaleString('en-US', { maximumFractionDigits: top < 3 ? 2 : top < 12 ? 1 : 0 });
  }

  private onKey = (e: KeyboardEvent, count: number) => {
    if (!count) return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      const cur = this.hover ?? count - 1;
      this.hover = Math.max(0, Math.min(count - 1, cur + (e.key === 'ArrowLeft' ? -1 : 1)));
    } else if (e.key === 'Home') this.hover = 0;
    else if (e.key === 'End') this.hover = count - 1;
    else if (e.key === 'Escape') this.hover = null;
  };

  /** pointer → sample index, in whichever cell the pointer is over */
  private track(e: MouseEvent | TouchEvent, cells: Cell[], count: number) {
    const rect = (e.currentTarget as Element).getBoundingClientRect();
    const p = 'touches' in e ? e.touches[0] : e;
    if (!p) return;
    const px = p.clientX - rect.left;
    const py = p.clientY - rect.top;
    const at = cells.findIndex((k) => px >= k.gx && px <= k.gx + k.w && py >= k.gy - 18 && py <= k.gy + k.h);
    const c = cells[Math.max(0, at)];
    this.hoverCell = Math.max(0, at);
    const x0 = c.gx + (c.yLab ? PAD.l : 6);
    this.hover = nearestIndex(px, x0, c.gx + c.w - PAD.r, count);
  }

  private cell(c: Cell, m: ChartModel, hover: number | null): SVGTemplateResult {
    const x0 = c.gx + (c.yLab ? PAD.l : 6);
    const x1 = c.gx + c.w - PAD.r;
    const pt = c.gy + PAD.t;
    const pb = c.gy + c.h - (c.xLab ? PAD.b : 6);
    const x = linear([0, Math.max(1, m.count - 1)], [x0, x1]);
    const y = linear([0, m.top], [pb, pt]);
    const pts = (vals: (number | null)[]): (Pt | null)[] => vals.map((v, i) => (v == null || !Number.isFinite(v) ? null : { x: x(i), y: y(Math.min(v, m.top)) }));
    const span = this.step * Math.max(1, m.count - 1);
    const tEnd = this.t0 + span;
    const ticks = niceTicks(0, m.top, c.h < 130 ? 2 : 4);
    const xl = c.xLab ? labelIndices(m.count, Math.max(2, Math.min(7, Math.floor((x1 - x0) / 92)))) : [];
    const evs = this.events.filter((e) => e.at >= this.t0 && e.at <= tEnd);
    const capY = m.cap && m.cap.v <= m.top ? y(m.cap.v) : null;
    return svg`
      ${c.title ?? nothing}
      ${ticks.map((t) => svg`<line class="grid" x1=${x0} x2=${x1} y1=${y(t)} y2=${y(t)} />${c.yLab ? svg`<text class="axis" x=${x0 - 6} y=${y(t) + 3.5} text-anchor="end">${this.tick(t, m.top)}</text>` : nothing}`)}
      ${xl.map((i) => svg`<text class="axis" x=${x(i)} y=${c.gy + c.h - 7} text-anchor=${i === 0 ? 'start' : i === m.count - 1 ? 'end' : 'middle'}>${timeLabel(this.t0 + i * this.step, span)}</text>`)}
      ${capY != null ? svg`<line class="fc-cap" x1=${x0} x2=${x1} y1=${capY} y2=${capY} /><text class="axis" x=${x1 - 2} y=${capY - 4} text-anchor="end">capacity ${this.fmt(m.cap!.v)} ${this.unit}${m.cap!.perNode && this.series.length > 1 ? ' per Spark' : ''}</text>` : nothing}
      ${c.areas.map((a) => svg`<path class="fc-area" d=${bandPath(pts(a.hi), pts(a.lo))} fill=${a.colour} opacity=${a.opacity} />${a.edge ? svg`<path d=${monotonePath(pts(a.hi))} fill="none" stroke=${a.colour} stroke-width="1.3" />` : nothing}`)}
      ${evs.map((e) => {
        const ex = x0 + ((e.at - this.t0) / span) * (x1 - x0);
        return svg`<line class="fc-ev" x1=${ex} x2=${ex} y1=${pt} y2=${pb} /><path class="fc-ev-mark ${e.alert ? 'is-alert' : ''}" d="M${ex} ${pt - 4}l4 4-4 4-4-4z"><title>${e.text}</title></path>`;
      })}
      ${c.lines.map((l) => {
        const d = monotonePath(pts(l.values));
        return svg`${l.glow ? svg`<path d=${d} fill="none" stroke=${l.colour} stroke-width="9" stroke-linecap="round" stroke-linejoin="round" class="fc-glow" filter="url(#${this.glowId}-glow)" />` : nothing}
          <path d=${d} fill="none" stroke=${l.colour} stroke-width=${l.width ?? 2} stroke-linecap="round" stroke-linejoin="round" stroke-dasharray=${l.dashed ? '5 4' : nothing} />`;
      })}
      ${hover != null
        ? svg`<line class="crosshair" x1=${x(hover)} x2=${x(hover)} y1=${pt} y2=${pb} />
          ${c.lines.map((l) => (l.values[hover] == null ? nothing : svg`<circle class="ring" cx=${x(hover)} cy=${y(Math.min(l.values[hover] as number, m.top))} r="4" fill=${l.colour} />`))}
          ${c.areas.map((a) => (a.hi[hover] == null ? nothing : svg`<circle class="ring" cx=${x(hover)} cy=${y(Math.min(a.hi[hover] as number, m.top))} r="3.5" fill=${a.colour} />`))}`
        : nothing}
    `;
  }

  private cells(m: ChartModel, W: number): { cells: Cell[]; H: number } {
    const one = this.series.length === 1;
    const node = (s: NodeSeries): Line => ({ values: s.values, colour: nodeColour(s.colour), dashed: isHollow(s.colour) });
    if (m.mode === 'split') {
      const cols = Math.min(this.series.length, W >= 880 ? 4 : W >= 460 ? 2 : 1);
      const rows = Math.ceil(this.series.length / cols);
      const mh = this.splitHeight;
      const th = 18;
      const gap = 16;
      const cw = (W - (cols - 1) * gap) / cols;
      const cells = this.series.map((s, k): Cell => {
        const c = k % cols;
        const r = Math.floor(k / cols);
        const gx = c * (cw + gap);
        const gy = r * (mh + th + gap);
        return {
          gx, gy: gy + th, w: cw, h: mh, yLab: c === 0, xLab: r === rows - 1,
          lines: [node(s)],
          areas: [{ lo: s.values.map((v) => (v == null ? null : 0)), hi: s.values, colour: nodeColour(s.colour), opacity: 0.12 }],
          title: svg`<circle cx=${gx + 5} cy=${gy + 8} r="4" fill=${isHollow(s.colour) ? 'none' : nodeColour(s.colour)} stroke=${nodeColour(s.colour)} stroke-width="1.5" /><text class="fc-mini-t" x=${gx + 14} y=${gy + 12}>${s.alias}</text><text class="axis" x=${gx + cw - 2} y=${gy + 12} text-anchor="end">${this.withUnit(stats(s.values).now)}</text>`,
        };
      });
      return { cells, H: rows * (mh + th) + (rows - 1) * gap };
    }
    const lines: Line[] = [];
    const areas: Area[] = [];
    if (m.mode === 'total') {
      lines.push({ values: m.total, colour: TOTAL, glow: true, width: 2.2 });
      if (m.mean) lines.push({ values: m.mean, colour: 'var(--ink-2)', dashed: true, width: 1.4 });
    } else if (m.mode === 'stacked') {
      m.layers.forEach((l, k) => areas.push({ lo: l.lo, hi: l.hi, colour: nodeColour(this.series[k].colour), opacity: 0.78, edge: true }));
      lines.push({ values: m.total, colour: 'var(--ink)', width: 1.1, glow: true });
    } else {
      for (const s of this.series) lines.push({ ...node(s), glow: one });
      if (one) areas.push({ lo: this.series[0].values.map((v) => (v == null ? null : 0)), hi: this.series[0].values, colour: nodeColour(this.series[0].colour), opacity: 0.1 });
    }
    return { cells: [{ gx: 0, gy: 0, w: W, h: this.height, yLab: true, xLab: true, lines, areas }], H: this.height };
  }

  private legend(m: ChartModel): TemplateResult {
    const n = this.series.length;
    const now = (v: (number | null)[]) => stats(v).now;
    if (n === 1) return html`<div class="legend"><span class="legend-item">now <b class="num">${this.withUnit(now(this.series[0].values))}</b></span></div>`;
    const op = AGG_OP[this.agg === 'maxmean' ? 'max' : this.agg];
    if (m.mode === 'total') {
      return html`<div class="legend">
        <span class="legend-item"><span class="swatch fc-sw-line" style="background:${TOTAL}"></span>${op} <b class="num">${this.withUnit(now(m.total))}</b></span>
        ${m.mean ? html`<span class="legend-item"><span class="swatch swatch-dash" style="border-color:var(--ink-2)"></span>mean <b class="num">${this.withUnit(now(m.mean))}</b></span>` : nothing}
      </div>`;
    }
    return html`<div class="legend">
      ${this.series.map(
        (s) => html`<span class="legend-item"
          ><span class="swatch ${isHollow(s.colour) ? 'swatch-outline' : ''}" style="${isHollow(s.colour) ? 'border-color' : 'background'}:${nodeColour(s.colour)}"></span>${s.alias} <b class="num">${this.fmt(now(s.values))}</b></span
        >`,
      )}
      <span class="legend-item fc-agg">${op} <b class="num">${this.withUnit(now(m.total))}</b></span>${m.mean ? html`<span class="legend-item fc-agg">mean <b class="num">${this.withUnit(now(m.mean))}</b></span>` : nothing}
    </div>`;
  }

  private table(): TemplateResult {
    const { rows, total } = tableRows(this.series, this.agg);
    const cells = (s: { now: number | null; min: number | null; mean: number | null; max: number | null }) => [s.now, s.min, s.mean, s.max].map((v) => html`<td class="num">${this.fmt(v)}</td>`);
    return html`<div class="fc-table-wrap">
      <table class="fc-table">
        <thead>
          <tr><th scope="col">Spark</th><th scope="col" class="num">now</th><th scope="col" class="num">min</th><th scope="col" class="num">mean</th><th scope="col" class="num">max</th></tr>
        </thead>
        <tbody>
          ${rows.map((r) => html`<tr><th scope="row">${nodeChip(r, { status: false })}</th>${cells(r.stats)}</tr>`)}
        </tbody>
        ${rows.length > 1 ? html`<tfoot><tr><th scope="row">${total.label}${this.unit ? ` (${this.unit})` : ''}</th>${cells(total)}</tr></tfoot>` : nothing}
      </table>
    </div>`;
  }

  private tip(m: ChartModel, i: number, left: number, W: number): TemplateResult {
    const t = this.t0 + i * this.step;
    const span = this.step * Math.max(1, m.count - 1);
    const near = this.events.filter((e) => Math.abs(e.at - t) <= this.step * 1.5);
    const n = this.series.length;
    return html`<div class="tip fc-tip" style="left:${Math.min(Math.max(left, 120), W - 120)}px" role="status">
      <div class="tip-title">${timeLabel(t, span)}</div>
      ${this.series.map(
        (s) => html`<div class="tip-row"><span class="swatch ${isHollow(s.colour) ? 'swatch-outline' : ''}" style="${isHollow(s.colour) ? 'border-color' : 'background'}:${nodeColour(s.colour)}"></span><span>${s.alias}</span><span class="num">${this.withUnit(s.values[i])}</span></div>`,
      )}
      ${n > 1
        ? html`<div class="tip-row tip-row-total"><span class="swatch" style="background:${TOTAL}"></span><span>${AGG_OP[this.agg === 'maxmean' ? 'max' : this.agg]} ${n} Sparks</span><span class="num">${this.withUnit(m.total[i])}</span></div>
            ${m.mean ? html`<div class="tip-row tip-row-sub"><span class="swatch swatch-dash" style="border-color:var(--ink-2)"></span><span>mean</span><span class="num">${this.withUnit(m.mean[i])}</span></div>` : nothing}`
        : nothing}
      ${near.map((e) => html`<div class="tip-body fc-tip-ev">◆ ${e.text}</div>`)}
    </div>`;
  }

  render() {
    const W = this.width || 600;
    const n = this.series.length;
    const m = chartModel({ series: this.series, agg: this.agg, mode: this.chosen ?? this.mode, cap: this.cap, max: this.max });
    const head = html`<div class="fc-head">
      <span class="fc-note">${modeNote(m.mode, this.agg, n)}</span>
      ${!this.noSwitch && n >= 2
        ? html`<qse-mode-switch
            chart-id=${this.chartId}
            .agg=${this.agg}
            .value=${m.mode}
            @qse-mode=${(e: CustomEvent<ModeEvent>) => {
              this.chosen = e.detail.mode;
              this.hover = null;
            }}
          ></qse-mode-switch>`
        : nothing}
    </div>`;
    if (m.mode === 'table') return html`<div class="fleet-chart">${head}${this.table()}</div>`;
    const { cells, H } = this.cells(m, W);
    const hover = this.hover != null && this.hover < m.count ? this.hover : null;
    const tipLeft = hover == null ? 0 : (() => {
      const c = cells[this.hoverCell] ?? cells[0];
      const x0 = c.gx + (c.yLab ? PAD.l : 6);
      return x0 + (hover / Math.max(1, m.count - 1)) * (c.gx + c.w - PAD.r - x0);
    })();
    const empty = !this.series.some((s) => s.values.some((v) => v != null));
    return html`<div class="fleet-chart">
      ${head}
      <div
        class="chart chart-fleet"
        tabindex="0"
        role="img"
        aria-label=${this.ariaLabelText || `${this.unit} per Spark over time, ${modeNote(m.mode, this.agg, n)}`}
        @keydown=${(e: KeyboardEvent) => this.onKey(e, m.count)}
      >
        <svg
          width=${W}
          height=${H}
          viewBox="0 0 ${W} ${H}"
          @mousemove=${(e: MouseEvent) => this.track(e, cells, m.count)}
          @mouseleave=${() => (this.hover = null)}
          @touchstart=${(e: TouchEvent) => this.track(e, cells, m.count)}
          @touchmove=${(e: TouchEvent) => this.track(e, cells, m.count)}
        >
          <defs>
            <filter id="${this.glowId}-glow" x="-5%" y="-40%" width="110%" height="180%"><feGaussianBlur stdDeviation="4" /></filter>
          </defs>
          ${empty ? svg`<text class="axis chart-nodata" x=${W / 2} y=${H / 2} text-anchor="middle">no samples in this range</text>` : cells.map((c) => this.cell(c, m, hover))}
        </svg>
        ${hover != null && !empty ? this.tip(m, hover, tipLeft, W) : nothing}
      </div>
      ${this.legend(m)}
    </div>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-fleet-chart': QseFleetChart;
  }
}
