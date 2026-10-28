// <qse-sparks>: Fleet → Sparks (VIS-250, concept §8.6, the v3 prototype's Sparks pages). List: every
// Spark in a table (alias, status, what it serves, memory, GPU temperature, power, version, link,
// lease), sorted by any column and filtered by text, status and owned or rented; a row opens the
// Spark's page. Topology: the full graph of <qse-topology> with its legend; a Spark or the host opens
// its inspector in the right sidebar, a link its details with the last link test and Run again, and
// each link below the graph has the same. A drained Spark shows ◐ wherever it appears. Update fleet
// (OPS-234, views/fleet/update.ts) opens its plan; the update's progress shows above the list while
// it runs (and until dismissed); `#/sparks?update=1` (the mixed-version alert's action) opens it.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { describeError } from '../../api/client';
import { fleet, type FleetLink, type FleetTopology, type MetricKey, type NodeMemory } from '../../api/fleet';
import { openSelection, refreshContext, type ContextContent } from '../../lib/context';
import { refreshFleet } from '../../lib/fleet-state';
import { lastValue, liveValues } from '../../lib/monitor';
import type { View } from '../../lib/router';
import { controllerNode, hwShort } from '../../lib/topology';
import { filterRows, leaseWords, linkWords, sortRows, sparkRows, type KindFilter, type SortKey, type SparkRow, type StatusFilter } from '../../lib/sparks';
import { icon } from '../../ui/bits';
import { nodeChip, statusWords, STATUS_GLYPH } from '../../ui/node-chip';
import { MonitorPage } from '../monitor/base';
import { clock, fmt, inspector, memBar, serves, since, signInCard } from '../monitor/bits';
import { checkSession, fromFleet, needsSignIn, onSession } from '../monitor/data';
import { linkDetails } from '../monitor/overview';
import '../monitor/topology';
import type { TopoNow, TopoPick } from '../monitor/topology';
import { parseHash, setParams } from '../../lib/router';
import './update';

const NOW_KEYS: MetricKey[] = ['tps', 'temp', 'power', 'mem', 'gpu', 'lan', 'fabric'];
const COLS: [SortKey, string, string][] = [
  ['alias', 'Spark', ''],
  ['status', 'Status', ''],
  ['serves', 'Serves', ''],
  ['mem', 'Memory', 'GiB'],
  ['temp', 'GPU', '°C'],
  ['power', 'Power', 'W'],
  ['version', 'Version', ''],
  ['link', 'Link', ''],
  ['lease', 'Lease', ''],
];
const STATUS_OPTS: [StatusFilter, string][] = [
  ['all', 'All'],
  ['up', '● Up'],
  ['draining', '◐ Draining'],
  ['off', '○ Off'],
  ['alert', '⚠ Alert'],
];
const KIND_OPTS: [KindFilter, string][] = [
  ['all', 'All'],
  ['owned', 'Owned'],
  ['rented', 'Rented'],
];

@customElement('qse-sparks')
export class QseSparks extends MonitorPage {
  protected readonly view: View = 'sparks';
  /** `list` or `topology` */
  @property({ type: String }) tab: 'list' | 'topology' = 'list';
  @state() private topo: FleetTopology | null = null;
  @state() private now: Partial<Record<MetricKey, Record<string, number | null>>> = {};
  @state() private memory: NodeMemory[] = [];
  @state() private absent = false;
  @state() private sort: SortKey = 'alias';
  @state() private sortDir: 'asc' | 'desc' = 'asc';
  @state() private q = '';
  @state() private status: StatusFilter = 'all';
  @state() private kind: KindFilter = 'all';
  @state() private selected = '';
  @state() private testing = '';
  @state() private testErr: Record<string, string> = {};
  private offSession: (() => void) | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    void checkSession();
    this.offSession = onSession(() => this.requestUpdate());
  }

  updated(): void {
    // the mixed-version alert's action, a bookmark: the dialog opens once
    if (parseHash().params.get('update') === '1' && this.fleetState) {
      setParams({ update: null });
      this.openUpdate();
    }
  }

  private openUpdate(): void {
    void this.updateComplete.then(() => this.querySelector('qse-fleet-update')?.open());
  }
  disconnectedCallback(): void {
    this.offSession?.();
    super.disconnectedCallback();
  }

  protected async load(isCurrent: () => boolean): Promise<void> {
    const topo = await fromFleet(() => fleet.topology()).catch(() => null);
    if (topo === 'absent') {
      if (isCurrent()) this.absent = true;
      return;
    }
    const [nowAns, mem] = await Promise.all([Promise.all(NOW_KEYS.map((k) => fleet.metrics({ source: 'all', metric: k, range: '5m' }).catch(() => null))), fleet.memory().catch(() => null)]);
    if (!isCurrent()) return;
    this.absent = false;
    if (topo) this.topo = topo;
    const now: Partial<Record<MetricKey, Record<string, number | null>>> = {};
    NOW_KEYS.forEach((k, i) => {
      const m = nowAns[i];
      if (m) now[k] = Object.fromEntries(m.series.map((x) => [x.node, lastValue(x.values)]));
    });
    this.now = now;
    if (mem) this.memory = mem.nodes;
    refreshContext();
  }

  private nowOf(k: MetricKey): Record<string, number | null> {
    return liveValues(this.now[k] ?? {}, this.fleetState?.nodes ?? []);
  }

  protected context(): ContextContent {
    const f = this.fleetState;
    if (!f) return { title: 'Sparks', body: html`<p class="muted small">The fleet shows here once TandemApp’s controller answers on this server.</p>` };
    const drained = f.nodes.filter((n) => n.drain);
    const leased = f.nodes.filter((n) => n.lease_end != null);
    const sec = (t: string, body: TemplateResult) => html`<section class="csec"><div class="csec-h"><span class="eyebrow">${t}</span></div>${body}</section>`;
    return {
      title: 'Sparks',
      body: html`${sec(
        'Status',
        html`<div class="ctx-nodes">${f.nodes.map((n) => html`<a class="mini-now" href=${`#/sparks/${encodeURIComponent(n.id)}`} data-node=${n.id}><span>${nodeChip(n)}</span><span class="small muted">${statusWords(n)}</span></a>`)}</div>`,
      )}
      ${sec('Maintenance', drained.length ? html`<ul class="sp-ctx-list small">${drained.map((n) => html`<li>${nodeChip(n, { href: `#/sparks/${encodeURIComponent(n.id)}` })} <span class="muted">${n.status_text}</span></li>`)}</ul>` : html`<p class="small muted">No Spark is drained.</p>`)}
      ${sec('Leases', leased.length ? html`<ul class="sp-ctx-list small">${leased.map((n) => html`<li>${nodeChip(n, { status: false })} <span class="muted">${leaseWords(n)}${n.cost_per_hour ? ` · ${n.cost_per_hour.toFixed(2)}/h` : ''}</span></li>`)}</ul>` : html`<p class="small muted">Every Spark is owned.</p>`)}`,
    };
  }

  // ---- the list -------------------------------------------------------------------------------------
  private rows(): SparkRow[] {
    const f = this.fleetState!;
    const cap = Object.fromEntries(this.memory.map((m) => [m.node, m.usable]));
    const rows = sparkRows({ nodes: f.nodes, deployments: f.deployments, links: this.topo?.links ?? [], now: { mem: this.nowOf('mem'), temp: this.nowOf('temp'), power: this.nowOf('power') }, cap });
    return sortRows(filterRows(rows, { q: this.q, status: this.status, kind: this.kind }), this.sort, this.sortDir);
  }

  private sortBy(k: SortKey): void {
    if (this.sort === k) this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
    else {
      this.sort = k;
      this.sortDir = ['mem', 'temp', 'power'].includes(k) ? 'desc' : 'asc';
    }
  }

  private filters(): TemplateResult {
    const seg = <T extends string>(label: string, opts: [T, string][], cur: T, set: (v: T) => void, attr: string) =>
      html`<div class="seg" role="group" aria-label=${label}>${opts.map(([v, l]) => html`<button type="button" class="seg-btn ${cur === v ? 'is-on' : ''}" aria-pressed=${cur === v} data-filter=${`${attr}:${v}`} @click=${() => set(v)}>${l}</button>`)}</div>`;
    return html`<div class="sp-filters">
      <label class="field field-search sp-search"><span class="sr-only">Filter</span>${icon('search')}<input type="search" placeholder="Filter by name, tag, model, address" aria-label="Filter Sparks" .value=${this.q} data-sp-q @input=${(e: Event) => (this.q = (e.target as HTMLInputElement).value)} /></label>
      ${seg('Status', STATUS_OPTS, this.status, (v) => (this.status = v), 'status')}
      ${seg('Owned or rented', KIND_OPTS, this.kind, (v) => (this.kind = v), 'kind')}
      <label class="sp-sort-pick small"><span class="muted">Sort</span><select data-sort-pick aria-label="Sort by" @change=${(e: Event) => this.sortBy((e.target as HTMLSelectElement).value as SortKey)}>${COLS.map(([k, l]) => html`<option value=${k} ?selected=${this.sort === k}>${l}</option>`)}</select><button type="button" class="btn btn-sm btn-ghost" aria-label=${this.sortDir === 'asc' ? 'Ascending: turn around' : 'Descending: turn around'} @click=${() => (this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc')}>${this.sortDir === 'asc' ? '▲' : '▼'}</button></label>
    </div>`;
  }

  private table(): TemplateResult {
    const rows = this.rows();
    const all = this.fleetState!.nodes.length;
    const th = ([k, label, unit]: [SortKey, string, string]) => {
      const on = this.sort === k;
      return html`<th scope="col" class=${['mem', 'temp', 'power'].includes(k) ? 'r' : ''} aria-sort=${on ? (this.sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}><button type="button" class="sp-sort ${on ? 'is-on' : ''}" data-sort=${k} @click=${() => this.sortBy(k)}>${label}${unit ? html` <span class="faint">${unit}</span>` : nothing}<span class="sp-dir" aria-hidden="true">${on ? (this.sortDir === 'asc' ? '▲' : '▼') : ''}</span></button></th>`;
    };
    return html`<section class="panel sp-table-panel" data-panel="sparks-list">
      ${this.filters()}
      <div class="sp-table-wrap">
        <table class="sp-table" aria-label="Sparks">
          <thead><tr>${COLS.map(th)}</tr></thead>
          <tbody>
            ${rows.map((r) => {
              const n = r.node;
              const lw = linkWords(n, this.topo?.links ?? []);
              const tone = n.status === 'off' ? 'err-ink' : n.status === 'alert' ? 'warn-ink' : n.status === 'draining' ? 'drain-ink' : 'ok-ink';
              const go = () => (location.hash = `#/sparks/${encodeURIComponent(n.id)}`);
              return html`<tr class="sp-row is-${n.status}" data-node=${n.id} @click=${(e: Event) => !(e.target as HTMLElement).closest('a,button') && go()}>
                <td data-label="Spark">${nodeChip(n, { lg: true, href: `#/sparks/${encodeURIComponent(n.id)}`, status: false })}<div class="xs faint sp-sub" title=${`${n.dmi || n.hw}${n.tags.length ? ` · ${n.tags.join(', ')}` : ''}`}>${hwShort(n)}${n.tags.length ? ` · ${n.tags.join(', ')}` : ''}</div></td>
                <td data-label="Status"><span class="small nw ${tone}" data-status=${n.status}><span aria-hidden="true">${STATUS_GLYPH[n.status]}</span> ${n.status === 'draining' && n.drain?.state === 'drained' ? 'drained' : n.status}</span>${n.status === 'up' ? nothing : html`<div class="xs muted sp-why" title=${n.status_text}>${n.status_text.replace(/^(draining|drained) · /, '')}</div>`}</td>
                <td data-label="Serves" class="small">${serves(n, r.deps)}</td>
                <td data-label="Memory" class="r num"><span class="nw">${r.mem == null ? html`<span class="muted">—</span>` : html`${fmt(r.mem, 1)}${r.memCap ? html` <span class="faint">/ ${fmt(r.memCap, 1)}</span>` : nothing}`}</span><div class="sp-mem">${memBar(this.memory.find((m) => m.node === n.id), true)}</div></td>
                <td data-label="GPU °C" class="r num ${(r.temp ?? 0) >= 85 ? 'warn-ink' : ''}">${fmt(r.temp)}</td>
                <td data-label="Power W" class="r num">${fmt(r.power)}</td>
                <td data-label="Version" class="num small">${n.version || html`<span class="muted">—</span>`}</td>
                <td data-label="Link" class="small"><span class="nw">${lw.main}</span><div class="xs faint nw">${lw.fabric}</div></td>
                <td data-label="Lease" class="small ${n.lease_end != null && n.lease_end - Date.now() / 1000 < 3 * 86400 ? 'warn-ink' : ''}">${n.lease_end == null ? html`<span class="faint">${r.lease}</span>` : html`<span class="nw">⏲ ${r.lease}</span>${n.cost_per_hour ? html`<div class="xs faint">${n.cost_per_hour.toFixed(2)} an hour</div>` : nothing}`}</td>
              </tr>`;
            })}
          </tbody>
        </table>
        ${rows.length ? nothing : html`<p class="small muted sp-none" data-sp-none>No Spark matches${all ? ` (${all} in the fleet)` : ''}. <button type="button" class="btn btn-ghost btn-sm" @click=${() => ((this.q = ''), (this.status = 'all'), (this.kind = 'all'))}>Clear the filter</button></p>`}
      </div>
    </section>
    <p class="xs faint">A Spark is known by its id (its certificate and DMI serial), not by its address: when an address or a tunnel port changes, the row stays and the event log says “moved from … to …”.</p>`;
  }

  // ---- the topology ------------------------------------------------------------------------------
  private async runTest(l: FleetLink): Promise<void> {
    this.testing = l.id;
    this.testErr = { ...this.testErr, [l.id]: '' };
    if (this.selected === `link:${l.id}`) this.showLink(l.id);
    try {
      const r = await fleet.testLink(l.id);
      if (this.topo) this.topo = { ...this.topo, links: this.topo.links.map((x) => (x.id === l.id ? r.link : x)) };
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) await checkSession(true);
      this.testErr = { ...this.testErr, [l.id]: d.status === 401 ? 'Sign in first: a link test is a write.' : d.message };
    } finally {
      this.testing = '';
      refreshFleet();
      if (this.selected === `link:${l.id}`) this.showLink(l.id);
    }
  }

  /** a link's last test and Run again, in the sidebar and on its card */
  private testBlock(l: FleetLink): TemplateResult {
    const t = l.test;
    const busy = this.testing === l.id;
    const err = this.testErr[l.id];
    const own = this.topo ? controllerNode(this.topo) : null;
    const wire = !(l.kind === 'lan' && own != null && (l.a === own || l.b === own));
    return html`<div class="sp-test" data-link-test=${l.id}>
      <div class="small">${t ? html`<b class="num">${t.gbps == null ? 'no bandwidth' : `${t.gbps} Gb/s`}</b>${t.rtt_ms != null ? html` · RTT <span class="num">${t.rtt_ms} ms</span>` : nothing} <span class="muted">· ${clock(t.at)} (${since(t.at)})</span>${t.note ? html`<div class="xs muted">${t.note}</div>` : nothing}` : html`<span class="muted">${wire ? 'not tested yet' : 'the controller runs on this Spark: no wire between them to test'}</span>`}</div>
      ${wire ? html`<button type="button" class="btn btn-sm" data-run-test=${l.id} ?disabled=${busy} @click=${() => void this.runTest(l)}>${busy ? 'Testing…' : t ? 'Run again' : 'Run a test'}</button>` : nothing}
      ${err ? html`<p class="xs err-ink" data-test-error>${err}</p>` : nothing}
    </div>`;
  }

  private showLink(id: string): void {
    const t = this.topo;
    const f = this.fleetState;
    const l = t?.links.find((x) => x.id === id);
    if (!t || !f || !l) return;
    openSelection('Link', html`${linkDetails(l, t, f.nodes)}<div class="csec-h"><span class="eyebrow">Last link test</span></div>${this.testBlock(l)}${needsSignIn() ? signInCard('Link tests', false) : nothing}`);
  }

  private onPick(p: TopoPick): void {
    const f = this.fleetState;
    const t = this.topo;
    if (!f || !t) return;
    if (p.kind === 'node') {
      const n = f.nodes.find((x) => x.id === p.id);
      if (!n) return;
      this.selected = `node:${n.id}`;
      const now = Object.fromEntries(NOW_KEYS.map((k) => [k, this.nowOf(k)[n.id] ?? null]));
      openSelection(n.alias, inspector({ node: n, deps: f.deployments, now, memory: this.memory.find((m) => m.node === n.id) ?? null }));
    } else if (p.kind === 'host') {
      this.selected = 'host';
      openSelection(t.host.name, html`<dl class="kv">
          <div><dt>runs</dt><dd>TandemApp’s controller and the API</dd></div>
          <div><dt>address</dt><dd class="num">${t.host.address}</dd></div>
          <div><dt>API</dt><dd class="num">${t.host.api_url}/v1</dd></div>
          <div><dt>sleep</dt><dd class=${t.host.may_sleep ? 'warn-ink' : ''}>${t.host.may_sleep ? 'this machine may sleep: the API and the metrics stop while it does' : 'does not sleep'}</dd></div>
        </dl>
        <a class="btn btn-sm" href="#/hardware?focus=host">Host metrics</a>`);
    } else if (p.kind === 'link') {
      this.selected = `link:${p.id}`;
      this.showLink(p.id);
    }
  }

  private topology(): TemplateResult {
    const t = this.topo;
    const f = this.fleetState!;
    if (!t) return html`<section class="panel"><div class="skeleton" style="height:360px"></div></section>`;
    const now: TopoNow = { tps: this.nowOf('tps'), temp: this.nowOf('temp'), power: this.nowOf('power'), lan: this.nowOf('lan'), fabric: this.nowOf('fabric') };
    const end = (id: string) => {
      const n = f.nodes.find((x) => x.id === id);
      return n ? nodeChip(n) : html`<span class="sp-end">${t.infra.find((i) => i.id === id)?.label ?? id}</span>`;
    };
    const cards = t.links.filter((l) => [l.a, l.b].some((x) => f.nodes.some((n) => n.id === x)));
    return html`<section class="panel" data-panel="topology-full">
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Topology</h2><p class="panel-sub">Click a Spark, the host or a link for its details. Deployments are drawn over the Sparks that serve them.</p></div></header>
        <qse-topology .topo=${t} .deployments=${f.deployments} .now=${now} .selected=${this.selected} @qse-topo-pick=${(e: CustomEvent<TopoPick>) => this.onPick(e.detail)}></qse-topology>
      </section>
      <section class="mon-sec" data-panel="links">
        <div class="mon-sec-h"><span class="eyebrow">Links and their last test</span><span class="xs faint">${cards.length} link${cards.length === 1 ? '' : 's'} to a Spark</span></div>
        <div class="sp-links">
          ${cards.map(
            (l) => html`<article class="panel sp-link ${this.selected === `link:${l.id}` ? 'is-sel' : ''}" data-link=${l.id}>
              <button type="button" class="sp-link-h" @click=${() => this.onPick({ kind: 'link', id: l.id })} aria-label=${`Link ${l.id}: details`}>
                <span class="row small">${end(l.a)}<span class="faint" aria-hidden="true">⟷</span>${end(l.b)}</span>
                <span class="small ${l.state === 'up' ? 'ok-ink' : l.state === 'degraded' ? 'warn-ink' : 'err-ink'}"><span aria-hidden="true">${l.state === 'up' ? '●' : l.state === 'degraded' ? '◐' : '○'}</span> ${l.kind === 'wan' ? l.speed : `${l.kind === 'fabric' ? 'fabric' : 'LAN'} · ${l.speed}`}</span>
              </button>
              <div class="xs muted num">${[l.rtt_ms != null ? `RTT ${l.rtt_ms} ms` : 'RTT not timed', l.mtu ? `MTU ${l.mtu}` : '', l.note ?? ''].filter(Boolean).join(' · ')}</div>
              ${this.testBlock(l)}
            </article>`,
          )}
        </div>
      </section>
      ${needsSignIn() ? signInCard('Link tests', false) : nothing}`;
  }

  render() {
    const f = this.fleetState;
    const tabs = html`<nav class="sp-tabs" aria-label="Sparks views"><a href="#/sparks" class=${this.tab === 'list' ? 'is-on' : ''} aria-current=${this.tab === 'list' ? 'page' : 'false'}>List</a><a href="#/sparks/topology" class=${this.tab === 'topology' ? 'is-on' : ''} aria-current=${this.tab === 'topology' ? 'page' : 'false'}>Topology</a></nav>`;
    const lan = f?.nodes.filter((n) => n.transport === 'lan').length ?? 0;
    const tun = (f?.nodes.length ?? 0) - lan;
    const sub = f ? `${f.nodes.length} Spark${f.nodes.length === 1 ? '' : 's'} in this fleet${lan ? `: ${lan} on the LAN` : ''}${tun ? `${lan ? ',' : ':'} ${tun} rented through an SSH tunnel` : ''}.` : '';
    const head = html`<header class="page-head sp-head" data-page="sparks"><div><div class="eyebrow">Fleet</div><h1>Sparks</h1>${sub ? html`<p class="page-sub">${sub}</p>` : nothing}</div><div class="sp-head-acts">${f?.nodes.length ? html`<button type="button" class="btn" data-act="update-fleet" @click=${() => this.openUpdate()}>Update fleet…</button>` : nothing}<a class="btn btn-primary" href="#/add-spark">+ Add Spark</a></div></header>${tabs}`;
    if (!f) return html`${head}${this.absent ? html`<p class="page-soon">The fleet comes from TandemApp’s controller, which this server does not run yet.</p>` : html`<div class="skeleton" style="height:240px"></div>`}`;
    return html`${head}<qse-update-progress .nodes=${f.nodes}></qse-update-progress>${this.tab === 'topology' ? this.topology() : this.table()}<p class="xs faint mon-foot">read ${clock(Date.now() / 1000)} · every 10 s while live</p>
      <qse-fleet-update .nodes=${f.nodes} @qse-update-started=${() => this.querySelector('qse-update-progress')?.follow(1500)}></qse-fleet-update>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-sparks': QseSparks;
  }
}
