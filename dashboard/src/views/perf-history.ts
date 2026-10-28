// Performance → History (VIS-254, concept §8.2): over time. With a fleet, the fleet store's charts
// per Spark first (views/fleet-scope.ts <qse-fleet-history>: decode, TTFT and requests in flight
// over the scope bar's range, each with its chart mode); then, as before the fleet, this server's
// ledger: decode, TTFT, prefill, tokens per block and draft acceptance per hour or day over its own
// period (`?period=`, apart from the scope's range), each relayed backend beside the engine.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { api, describeError } from '../api/client';
import type { BackendRef, Usage } from '../api/types';
import { compact, ms } from '../lib/format';
import { onFleet, type FleetState } from '../lib/fleet-state';
import { backendName, relayedBackends } from '../lib/relayed';
import { poll, type Poller } from '../lib/poll';
import { setParams } from '../lib/router';
import { addDays, browserTz, todayKey } from '../lib/time';
import { isFleet } from '../lib/where';
import { LightElement, type Loadable } from '../ui/base';
import { panel, skeleton, errorState, emptyState } from '../ui/bits';

type HRange = '24h' | '7d' | '30d' | '365d';

let fleetChunk: Promise<unknown> | null = null;
/** the fleet parts' chunk (views/fleet-scope.ts): its elements upgrade in place once it loaded */
export const loadFleetScope = () => (fleetChunk ??= import('./fleet-scope'));

@customElement('qse-perf-history')
export class QsePerfHistory extends LightElement {
  @property({ attribute: false }) params: URLSearchParams = new URLSearchParams();
  @state() private history: Loadable<Usage> = { state: 'loading' };
  /** each relayed backend's history (a cloud model), drawn beside the engine's, never pooled */
  @state() private relayedHistory: { ref: BackendRef; usage: Usage }[] = [];
  @state() private fleet: FleetState | null = null;
  private tz = browserTz();
  private histPoll: Poller | null = null;
  private offFleet: (() => void) | null = null;
  private lastKey = '';

  /** the ledger's period; `range` is the scope bar's (5 min to 7 days) */
  private get range(): HRange {
    const r = this.params.get('period');
    return r === '24h' || r === '30d' || r === '365d' ? r : '7d';
  }
  /** with a fleet, the ledger's part says whose it is */
  private get ledgerTitle(): string {
    return isFleet(this.fleet) ? "This server's ledger" : 'History';
  }
  private get model() {
    return this.params.get('model');
  }
  private get client() {
    return this.params.get('client');
  }

  connectedCallback(): void {
    super.connectedCallback();
    this.histPoll = poll((isCurrent) => this.loadHistory(isCurrent), 60_000);
    this.histPoll.start();
    this.offFleet = onFleet((f) => (this.fleet = f));
  }
  disconnectedCallback(): void {
    this.histPoll?.stop();
    this.offFleet?.();
    super.disconnectedCallback();
  }
  updated(changed: Map<string, unknown>): void {
    if (changed.has('params')) {
      const key = `${this.range}|${this.model}|${this.client}`;
      if (key !== this.lastKey) {
        this.lastKey = key;
        this.history = { state: 'loading' };
        void this.histPoll?.refresh();
      }
    }
  }

  private async loadHistory(isCurrent: () => boolean): Promise<void> {
    const today = todayKey(this.tz);
    const range = this.range; // captured before the await: post-await reads of this.range would see a newer selection
    const days = range === '24h' ? 2 : range === '7d' ? 7 : range === '30d' ? 30 : 365;
    const bucket = days <= 31 ? 'hour' : 'day';
    const model = this.model, client = this.client;
    try {
      const q = { from: addDays(today, -(days - 1)), to: today, bucket, tz: this.tz, model, client } as const;
      // the engine's own requests; each relayed backend apart (0.11.3)
      const u = await api.usage({ ...q, own: true });
      if (!isCurrent()) return; // a newer refresh (params changed) owns the view now
      const others = await Promise.all(relayedBackends(u).map(async (ref) => ({ ref, usage: await api.usage({ ...q, backend: ref.id }) })));
      if (!isCurrent()) return;
      if (range === '24h') for (const x of [u, ...others.map((o) => o.usage)]) x.buckets = x.buckets.slice(-24);
      this.relayedHistory = others.filter((o) => o.usage.totals.requests > 0);
      this.history = { state: 'ready', data: u };
    } catch (e) {
      if (!isCurrent()) return;
      const d = describeError(e);
      this.history = { state: 'error', endpoint: d.endpoint, message: d.message };
    }
  }

  render() {
    const fleet = isFleet(this.fleet);
    if (fleet) void loadFleetScope();
    return html`<div class="view view-performance view-perf-history">
      ${fleet ? html`<qse-fleet-history></qse-fleet-history>` : nothing}
      ${this.renderHistory()}
    </div>`;
  }

  // ---- this server's ledger -----------------------------------------------------------------
  private renderHistory(): TemplateResult {
    const h = this.history;
    const rangeBtns = html`<div class="seg" role="radiogroup" aria-label="history range">
      ${(['24h', '7d', '30d', '365d'] as HRange[]).map((r) => html`<button role="radio" aria-checked=${this.range === r} class="seg-btn ${this.range === r ? 'is-on' : ''}" @click=${() => setParams({ period: r === '7d' ? null : r })}>${r.replace(/(\d+)([hd])/, '$1 $2')}</button>`)}
    </div>`;
    const dims = h.state === 'ready' ? h.data.dimensions : { models: [], clients: [] };
    const toolbar = html`<div class="toolbar">
      ${rangeBtns}
      <label class="field"><span>Model</span>
        <select @change=${(e: Event) => setParams({ model: (e.target as HTMLSelectElement).value || null })}>
          <option value="">all</option>${dims.models.map((m) => html`<option value=${m} ?selected=${m === this.model}>${m}</option>`)}
        </select></label>
      <label class="field"><span>Client</span>
        <select @change=${(e: Event) => setParams({ client: (e.target as HTMLSelectElement).value || null })}>
          <option value="">all</option>${dims.clients.map((c) => html`<option value=${c.id} ?selected=${c.id === this.client}>${c.label ?? c.id} (${c.kind})</option>`)}
        </select></label>
    </div>`;
    if (h.state === 'loading') return html`<section class="history"><div class="history-head"><h2 class="panel-title">${this.ledgerTitle}</h2>${toolbar}</div>${skeleton(6)}</section>`;
    if (h.state === 'error') return html`<section class="history"><div class="history-head"><h2 class="panel-title">${this.ledgerTitle}</h2>${toolbar}</div>${errorState(h.endpoint, h.message, () => this.histPoll?.refresh())}</section>`;
    if (h.state === 'empty') return html`<section class="history">${emptyState('No history')}</section>`;
    const u = h.data;
    const b = u.buckets;
    const labels = b.map((x) => (u.bucket === 'hour' ? (b.length > 48 ? x.start.slice(5, 13).replace('T', ' ') : x.start.slice(11, 16)) : x.start.slice(5, 10)));
    const rel = this.relayedHistory.filter((o) => o.usage.buckets.length === b.length);
    const src = `${u.bucket === 'hour' ? 'hourly' : 'daily'} buckets from the ledger (400 days kept; Prometheus keeps 15); this engine's requests${rel.length ? `, and ${rel.map((o) => backendName(o.ref)).join(', ')} relayed beside them (red)` : ''}`;
    const relDecode = rel.map((o) => ({ name: `${backendName(o.ref)} p50`, color: 'red' as const, values: o.usage.buckets.map((x) => x.decode_tps_p50) }));
    const relTtft = rel.map((o) => ({ name: `${backendName(o.ref)} p50`, color: 'red' as const, values: o.usage.buckets.map((x) => x.ttft_ms_p50) }));
    return html`<section class="history" id="history">
      <div class="history-head">
        <div class="panel-titles"><h2 class="panel-title">${this.ledgerTitle}</h2><p class="panel-sub">${src}</p></div>
        ${toolbar}
      </div>
      <div class="grid-2">
        ${panel('Decode speed', html`<qse-ribbon .series=${[{ name: rel.length ? 'engine p50' : 'p50', color: 'cobalt', values: b.map((x) => x.decode_tps_p50), band: b.map((x) => x.decode_tps_p90), bandName: rel.length ? 'engine p90' : 'p90' }, ...relDecode]} .labels=${labels} .format=${(v: number) => String(Math.round(v))} unit="tok/s" ?log=${rel.length > 0} .height=${200}></qse-ribbon>`, { sub: rel.length ? 'tok/s, p50 and p90, log scale; a relayed backend measured at the proxy' : 'tok/s, p50 and p90', id: 'history-decode' })}
        ${panel('Time to first token', html`<qse-ribbon .series=${[{ name: rel.length ? 'engine p50' : 'p50', color: 'orange', values: b.map((x) => x.ttft_ms_p50), band: b.map((x) => x.ttft_ms_p90), bandName: rel.length ? 'engine p90' : 'p90' }, ...relTtft]} .labels=${labels} .format=${(v: number) => ms(v)} log .height=${200}></qse-ribbon>`, { sub: rel.length ? 'p50 and p90, log scale; a relayed backend\'s holds the network and its queue' : 'p50 and p90, log scale', id: 'history-ttft' })}
        ${panel('Prefill speed', html`<qse-ribbon .series=${[{ name: 'p50', color: 'cobalt', values: b.map((x) => x.prefill_tps_p50) }]} .labels=${labels} .format=${(v: number) => compact(v)} unit="tok/s" .height=${180}></qse-ribbon>`, { sub: 'uncached prompt tokens per second, p50' })}
        ${panel('Tokens per block', html`<qse-ribbon .series=${[{ name: 'mean', color: 'violet', values: b.map((x) => x.tokens_per_block_mean) }]} .labels=${labels} .format=${(v: number) => v.toFixed(1)} .height=${180}></qse-ribbon>`, { sub: 'committed tokens per verified block, mean' })}
        ${panel('Draft acceptance', html`<qse-ribbon .series=${[{ name: 'acceptance', color: 'aqua', values: b.map((x) => (x.draft_acceptance == null ? null : x.draft_acceptance * 100)) }]} .labels=${labels} .format=${(v: number) => v.toFixed(0)} unit="%" .height=${180}></qse-ribbon>`, { sub: 'accepted / drafted tokens' })}
        ${panel(
          'Reading the numbers',
          html`<ul class="reading">
            <li><b>Tokens per block</b> is the number the engine is tuned on: how many tokens one verify pass commits. The row's mean speed moves with it.</li>
            <li><b>Acceptance falls first</b> when the drafter loses the workload — watch it before decode tok/s drops.</li>
            <li>A per-request tok/s is not the row number: the atlas row is 256-in / 256-out at temperature 0; chats are longer, cached and mixed.</li>
            <li>The block split (narrow / wide / deep arms) and the verify-time histogram are in Grafana.</li>
          </ul>`,
          { cls: 'panel-reading' },
        )}
      </div>
    </section>`;
  }
}
