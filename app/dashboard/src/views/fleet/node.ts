// <qse-spark-node>: one Spark's page (VIS-250, concept §8.6 Node detail, the v3 prototype's node page).
// The header with its colour, alias, status, icon and tags, edited in place (an alias or a colour
// another Spark has is refused); its drain or alert said above everything; quick charts of its decode,
// GPU temperature, power and memory with the fleet's events marked; its memory split; the actions
// (drain or undrain, restart the engine, reboot, update, logs, forget or release the lease), each
// through the controller's plan in <qse-node-action>; its links with their last test; what it
// serves; its inventory as its agent read it (absent fields said so, never 0); its latest events.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { describeError } from '../../api/client';
import { fleet, METRICS, type FleetEvent, type FleetLink, type FleetNode, type MetricKey, type MetricSeries, type NodeMemory, type Range } from '../../api/fleet';
import type { ChartEvent } from '../../charts/fleet-chart';
import '../../charts/fleet-chart';
import { refreshContext, type ContextContent } from '../../lib/context';
import { refreshFleet } from '../../lib/fleet-state';
import { lastValue } from '../../lib/monitor';
import type { View } from '../../lib/router';
import { colourChoices, drainLine, editPatch, leaseWords, linksOf, nodeActions, type EditDraft } from '../../lib/sparks';
import { openWindow } from '../../ui/workspace';
import { icon } from '../../ui/bits';
import { isHollow, nodeChip, nodeColour, statusWords, STATUS_GLYPH } from '../../ui/node-chip';
import { MonitorPage } from '../monitor/base';
import { clock, eventRow, fmt, memBar, memLegend, since, signInCard, span, typeBadge } from '../monitor/bits';
import { chartEvents, checkSession, fromFleet, needsSignIn, onSession } from '../monitor/data';
import './action-dialog';
import type { QseNodeAction } from './action-dialog';

const CHARTS: MetricKey[] = ['tps', 'temp', 'power', 'mem'];
const RANGES: Range[] = ['5m', '1h', '24h'];
const ICONS: FleetNode['icon'][] = ['desk', 'cloud', 'rack'];
const NOT_READ = 'not read from this Spark';

@customElement('qse-spark-node')
export class QseSparkNode extends MonitorPage {
  protected machines = true;
  protected readonly view: View = 'sparks';
  @property({ type: String }) nodeId = '';
  @state() private range: Range = '1h';
  @state() private charts: Partial<Record<MetricKey, MetricSeries>> = {};
  @state() private marks: ChartEvent[] = [];
  @state() private memory: NodeMemory | null = null;
  @state() private links: FleetLink[] = [];
  @state() private events: FleetEvent[] = [];
  @state() private gone = false;
  @state() private edit: EditDraft | null = null;
  @state() private editErr = '';
  @state() private saving = false;
  @state() private testing = '';
  @state() private testErr: Record<string, string> = {};
  private get dialog(): QseNodeAction | null {
    return this.querySelector('qse-node-action');
  }
  private offSession: (() => void) | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    void checkSession();
    this.offSession = onSession(() => this.requestUpdate());
  }
  disconnectedCallback(): void {
    this.offSession?.();
    super.disconnectedCallback();
  }
  protected updated(ch: Map<string, unknown>): void {
    if (ch.has('nodeId') && ch.get('nodeId') !== undefined) {
      this.edit = null;
      this.charts = {};
      this.reload();
    }
  }

  private get node(): FleetNode | null {
    return this.fleetState?.nodes.find((n) => n.id === this.nodeId) ?? null;
  }

  protected async load(isCurrent: () => boolean): Promise<void> {
    const id = this.nodeId;
    if (!id) return;
    const src = `node:${id}`;
    const [series, mem, topo, evs] = await Promise.all([
      Promise.all(CHARTS.map((k) => fleet.metrics({ source: src, metric: k, range: this.range }).catch(() => null))),
      fleet.memory().catch(() => null),
      fromFleet(() => fleet.topology()).catch(() => null),
      fleet.events({ node: id, limit: 8 }).catch(() => null),
    ]);
    const t0 = series.find(Boolean)?.t0;
    const marks = t0 != null ? await chartEvents(t0).catch(() => []) : [];
    if (!isCurrent() || id !== this.nodeId) return;
    const charts: Partial<Record<MetricKey, MetricSeries>> = {};
    CHARTS.forEach((k, i) => {
      if (series[i]) charts[k] = series[i]!;
    });
    this.charts = charts;
    this.marks = marks;
    this.memory = mem?.nodes.find((m) => m.node === id) ?? null;
    if (topo && topo !== 'absent') this.links = linksOf(id, topo.links);
    if (evs) this.events = evs.events;
    this.gone = !!this.fleetState && !this.node;
    refreshContext();
  }

  protected context(): ContextContent {
    const n = this.node;
    if (!n) return { title: 'Spark', body: html`<p class="small muted">${this.gone ? 'This Spark is not in the fleet any more.' : 'Reading the fleet…'}</p>` };
    const now = (k: MetricKey) => lastValue(this.charts[k]?.series[0]?.values ?? []);
    return {
      title: n.alias,
      body: html`<section class="csec"><div class="csec-h"><span class="eyebrow">Now</span></div>
          <div class="insp-figs">
            <div><span>tok/s</span><b class="num">${n.status === 'off' ? '—' : fmt(now('tps'))}</b></div>
            <div><span>GPU °C</span><b class="num">${n.status === 'off' ? '—' : fmt(now('temp'))}</b></div>
            <div><span>W</span><b class="num">${n.status === 'off' ? '—' : fmt(now('power'))}</b></div>
            <div><span>GiB</span><b class="num">${n.status === 'off' ? '—' : fmt(now('mem'), 1)}</b></div>
          </div></section>
        <section class="csec"><div class="csec-h"><span class="eyebrow">Latest events</span><a class="small" href="#/events">All</a></div>${this.events.length ? html`<div class="ev-list is-compact">${this.events.slice(0, 5).map((e) => eventRow(e, this.fleetState!.nodes, this.fleetState!.deployments))}</div>` : html`<p class="small muted">No events of ${n.alias} yet.</p>`}</section>`,
    };
  }

  // ---- edit in place -------------------------------------------------------------------------------
  private startEdit(): void {
    const n = this.node!;
    this.edit = { alias: n.alias, colour: n.colour, icon: n.icon, tags: n.tags.join(', ') };
    this.editErr = '';
  }

  private async save(): Promise<void> {
    const n = this.node;
    const d = this.edit;
    if (!n || !d || this.saving) return;
    const { patch, error } = editPatch(n, d, this.fleetState!.nodes);
    if (error) {
      this.editErr = error;
      return;
    }
    if (!patch) {
      this.edit = null;
      return;
    }
    this.saving = true;
    try {
      await fleet.patchNode(n.id, patch);
      this.edit = null;
      this.editErr = '';
      refreshFleet();
    } catch (e) {
      const x = describeError(e);
      if (x.status === 401) await checkSession(true);
      this.editErr = x.status === 401 ? 'Sign in with the admin password to change a Spark.' : x.message;
    } finally {
      this.saving = false;
    }
  }

  private editPanel(n: FleetNode): TemplateResult {
    const d = this.edit!;
    const set = (p: Partial<EditDraft>) => ((this.edit = { ...d, ...p }), (this.editErr = ''));
    return html`<form class="panel sp-edit" data-sp-edit @submit=${(e: Event) => (e.preventDefault(), void this.save())}>
      <div class="sp-edit-grid">
        <label class="field"><span>Alias, used in every chart, log line and the x-tandem-node header</span><input data-edit-alias maxlength="40" .value=${d.alias} @input=${(e: Event) => set({ alias: (e.target as HTMLInputElement).value })} /></label>
        <label class="field"><span>Tags, comma-separated</span><input data-edit-tags .value=${d.tags} placeholder="desk, rented, provider" @input=${(e: Event) => set({ tags: (e.target as HTMLInputElement).value })} /></label>
        <fieldset class="field sp-swatches"><legend>Colour, its identity only: status is never a colour</legend>
          <div class="sp-sw-row">${colourChoices(this.fleetState!.nodes, n.id).map(
            (c) => html`<button type="button" class="sp-sw ${isHollow(c.colour) ? 'is-hollow' : ''} ${d.colour === c.colour ? 'is-on' : ''}" style=${`--c:${nodeColour(c.colour)}`} data-colour=${c.colour} aria-pressed=${d.colour === c.colour} ?disabled=${!!c.usedBy} title=${c.usedBy ? `colour ${c.colour}: ${c.usedBy}’s` : `colour ${c.colour}${isHollow(c.colour) ? ', hollow, dashed in charts' : ''}`} aria-label=${c.usedBy ? `colour ${c.colour}, ${c.usedBy}’s` : `colour ${c.colour}`} @click=${() => set({ colour: c.colour })}></button>`,
          )}</div>
          <span class="xs faint">Colours 5 to 8 repeat the four hues hollow; their chart lines are dashed.</span>
        </fieldset>
        <label class="field"><span>Icon</span><select data-edit-icon @change=${(e: Event) => set({ icon: (e.target as HTMLSelectElement).value as FleetNode['icon'] })}>${ICONS.map((i) => html`<option value=${i} ?selected=${d.icon === i}>${i}</option>`)}</select></label>
      </div>
      ${this.editErr ? html`<p class="small err-ink" data-edit-error>${this.editErr}</p>` : nothing}
      ${needsSignIn() ? signInCard('Changing a Spark', false) : nothing}
      <div class="row"><button type="submit" class="btn btn-primary" data-edit-save ?disabled=${this.saving}>${this.saving ? 'Saving…' : 'Save'}</button><button type="button" class="btn btn-ghost" @click=${() => (this.edit = null)}>Cancel</button></div>
    </form>`;
  }

  // ---- parts ---------------------------------------------------------------------------------------
  private header(n: FleetNode): TemplateResult {
    const st = n.status;
    const tone = st === 'off' ? 'err-ink' : st === 'alert' ? 'warn-ink' : st === 'draining' ? 'drain-ink' : 'ok-ink';
    const lease = n.lease_end != null ? html` · lease <span class=${n.lease_end - Date.now() / 1000 < 3 * 86400 ? 'warn-ink' : ''}>${leaseWords(n)}</span>${n.cost_per_hour ? ` (${n.cost_per_hour.toFixed(2)} an hour)` : ''}` : nothing;
    return html`<nav class="xs muted sp-crumb" aria-label="Breadcrumb"><a href="#/sparks">Sparks</a> / <span>${n.alias}</span></nav>
      <header class="page-head sp-node-head" data-page="spark" data-node=${n.id}>
        <span class="sp-node-sw ${isHollow(n.colour) ? 'is-hollow' : ''}" style=${`--c:${nodeColour(n.colour)}`} aria-hidden="true"></span>
        <div class="sp-node-t">
          <div class="eyebrow">Fleet · Spark · ${n.icon}</div>
          <div class="row sp-node-title">
            <h1 data-node-alias>${n.alias}</h1>
            <span class="sp-status ${tone}" data-status=${st}><span aria-hidden="true">${STATUS_GLYPH[st]}</span> ${statusWords(n)}</span>
            ${n.tags.map((t) => html`<span class="tag">${t}</span>`)}
          </div>
          <p class="page-sub">${n.dmi || n.hw} · ${n.transport === 'tunnel' ? 'SSH tunnel' : 'LAN'} <span class="num">${n.address}</span> · TandemLLM <span class="num">${n.version || '—'}</span>${lease}</p>
        </div>
        ${this.edit ? nothing : html`<button type="button" class="btn btn-sm" data-edit @click=${() => this.startEdit()}>${icon('edit')} Edit alias, colour, icon, tags</button>`}
      </header>`;
  }

  private banners(n: FleetNode): TemplateResult {
    const d = drainLine(n);
    return html`${d ? html`<div class="panel sp-banner is-drain" data-drain=${n.drain!.state} role="status"><span class="sp-banner-g" aria-hidden="true">◐</span><div><b>${d.title}</b><p class="small muted">${d.text}${n.drain!.by ? ` Drained by ${n.drain!.by} at ${clock(n.drain!.since)}.` : ''}</p></div>${n.drain!.state === 'drained' ? html`<button type="button" class="btn btn-sm" @click=${() => this.dialog?.open(n, 'undrain')}>Undrain</button>` : nothing}</div>` : nothing}
      ${n.status === 'alert' ? html`<div class="panel sp-banner is-alert" role="status"><span class="sp-banner-g" aria-hidden="true">⚠</span><div><b>${n.status_text}</b><p class="small muted">See the alert in <a href="#/events">Events &amp; Alerts</a>${n.drain ? '' : `, or drain ${n.alias} until it is fixed`}.</p></div></div>` : nothing}
      ${n.status === 'off' && !n.drain ? html`<div class="panel sp-banner is-off" role="status"><span class="sp-banner-g" aria-hidden="true">○</span><div><b>${n.alias} is off</b><p class="small muted">${n.status_text}</p></div></div>` : nothing}`;
  }

  private chartsPanel(n: FleetNode): TemplateResult {
    return html`<section class="panel" data-panel="quick-charts">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Quick charts</h2><p class="panel-sub">${n.alias} only · ◆ events</p></div>
        <div class="panel-tools"><div class="seg" role="radiogroup" aria-label="Range">${RANGES.map((r) => html`<button type="button" role="radio" class="seg-btn ${this.range === r ? 'is-on' : ''}" aria-checked=${this.range === r} data-range=${r} @click=${() => ((this.range = r), (this.charts = {}), this.reload())}>${r}</button>`)}</div></div></header>
      <div class="sp-charts">${CHARTS.map((k) => {
        const m = this.charts[k];
        const info = METRICS[k];
        return html`<div class="sp-chart" data-chart=${k}><div class="row-between xs muted"><span>${info.label}</span><span>${info.unit}</span></div>
          ${m
            ? html`<qse-fleet-chart chart-id=${`node-${k}`} .series=${m.series.map((s) => ({ id: s.node, alias: n.alias, colour: n.colour, values: s.values }))} agg=${m.agg} unit=${m.unit} .t0=${m.t0} .step=${m.step_s} .decimals=${info.decimals} .cap=${m.cap} .max=${info.max ?? null} .events=${this.marks} mode="lines" no-switch .height=${130} aria-label=${`${n.alias} ${info.label} over ${m.range}`}></qse-fleet-chart>`
            : html`<div class="skeleton" style="height:130px"></div>`}</div>`;
      })}</div>
      ${this.memory ? html`<div class="sp-memory"><div class="small muted">Unified memory</div>${memBar(this.memory)}${memLegend(this.memory)}</div>` : html`<p class="small muted">No memory split from ${n.alias}${n.status === 'off' ? ' while it is off' : ''}.</p>`}
    </section>`;
  }

  private actionsPanel(n: FleetNode): TemplateResult {
    const acts = nodeActions(n);
    return html`<section class="panel" data-panel="actions">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Actions</h2><p class="panel-sub">Each one says first what it stops, removes and keeps.</p></div></header>
      <div class="sp-acts">
        ${acts.filter((a) => !a.danger).map((a) => html`<button type="button" class="btn" data-action=${a.action} @click=${() => this.dialog?.open(n, a.action)}>${a.label}</button>`)}
        <button type="button" class="btn" data-action="logs" @click=${() => openWindow('logs', { node: n.id })}>${icon('doc')} Logs</button>
        <span class="grow"></span>
        ${acts.filter((a) => a.danger).map((a) => html`<button type="button" class="btn btn-danger-ghost" data-action=${a.action} @click=${() => this.dialog?.open(n, a.action)}>${a.label}</button>`)}
      </div>
    </section>`;
  }

  private async runTest(l: FleetLink): Promise<void> {
    this.testing = l.id;
    this.testErr = { ...this.testErr, [l.id]: '' };
    try {
      const r = await fleet.testLink(l.id);
      this.links = this.links.map((x) => (x.id === l.id ? r.link : x));
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) await checkSession(true);
      this.testErr = { ...this.testErr, [l.id]: d.status === 401 ? 'Sign in first: a link test is a write.' : d.message };
    } finally {
      this.testing = '';
    }
  }

  private linksPanel(n: FleetNode): TemplateResult {
    const nodes = this.fleetState!.nodes;
    const end = (id: string) => {
      const x = nodes.find((k) => k.id === id);
      return x ? nodeChip(x, { status: false }) : html`<span class="sp-end">${id === 'lan' ? 'LAN' : id === 'gw' ? 'rental gateway' : id === 'sw' ? 'fabric switch' : id}</span>`;
    };
    const own = (l: FleetLink) => l.kind === 'lan' && l.rtt_ms == null && (l.note ?? '').includes('controller runs');
    return html`<section class="panel" data-panel="links">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Links</h2><p class="panel-sub">${n.inventory.cx7 == null ? 'no ConnectX port: no fabric on this Spark' : n.inventory.cx7}</p></div><div class="panel-tools"><a class="btn btn-ghost btn-sm" href="#/sparks/topology">Topology</a></div></header>
      ${this.links.length
        ? this.links.map((l) => {
            const t = l.test;
            return html`<div class="sp-lrow" data-link=${l.id}>
              <div class="row small">${end(l.a === n.id ? l.b : l.a)}<span class="muted">${l.kind === 'wan' ? l.speed : `${l.kind === 'fabric' ? 'fabric' : 'LAN'} · ${l.speed}`} · <span class=${l.state === 'up' ? 'ok-ink' : 'warn-ink'}>${l.state}</span></span></div>
              <div class="xs muted">${t ? html`last test <b class="num">${t.gbps == null ? 'no bandwidth' : `${t.gbps} Gb/s`}</b>${t.rtt_ms != null ? ` · RTT ${t.rtt_ms} ms` : ''} · ${since(t.at)}${t.note ? html` · ${t.note}` : nothing}` : own(l) ? 'the controller runs on this Spark: no wire between them to test' : 'not tested yet'}</div>
              ${own(l) ? nothing : html`<button type="button" class="btn btn-sm" data-run-test=${l.id} ?disabled=${this.testing === l.id} @click=${() => void this.runTest(l)}>${this.testing === l.id ? 'Testing…' : t ? 'Run again' : 'Link test'}</button>`}
              ${this.testErr[l.id] ? html`<p class="xs err-ink" data-test-error>${this.testErr[l.id]}</p>` : nothing}
            </div>`;
          })
        : html`<p class="small muted">No link reported for ${n.alias}.</p>`}
    </section>`;
  }

  private servesPanel(n: FleetNode): TemplateResult {
    const f = this.fleetState!;
    const deps = f.deployments.filter((d) => d.members.includes(n.id));
    return html`<section class="panel" data-panel="serves">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Serves</h2></div><div class="panel-tools"><a class="btn btn-ghost btn-sm" href="#/models">Models</a></div></header>
      ${deps.length
        ? deps.map((d) => {
            const i = d.members.indexOf(n.id);
            const others = d.members.filter((m) => m !== n.id).map((m) => f.nodes.find((x) => x.id === m)?.alias ?? m);
            const where = d.type === 'split' ? `stage ${i + 1} of ${d.members.length}${d.stages?.[i] ? ` · ${d.stages[i].layers} · ${d.stages[i].gib} GiB` : ''}` : d.type === 'replicas' ? `member ${i + 1} of ${d.members.length}, with ${others.join(', ')}` : d.type === 'role' ? (d.role ?? 'a role') : 'on this Spark alone';
            return html`<div class="sp-dep" data-dep=${d.id}><div class="row">${typeBadge(d.type)}<b>${d.name}</b></div><div class="xs muted">${where} · ${d.pack || d.model} · <span class=${d.status === 'serving' ? 'ok-ink' : 'warn-ink'}>${d.status_text}</span></div></div>`;
          })
        : html`<p class="small muted">${n.alias} serves nothing.</p>`}
    </section>`;
  }

  private inventoryPanel(n: FleetNode): TemplateResult {
    const inv = n.inventory;
    const v = (x: string | null) => (x == null ? html`<span class="muted">${NOT_READ}</span>` : x);
    const p = n.pairing;
    const trust = p.state === 'local' ? 'this machine: the controller runs on it' : p.state === 'paired' ? html`paired by ${p.via}${p.paired_at ? ` ${since(p.paired_at)}` : ''}<div class="xs faint num">${p.fingerprint ? `${p.fingerprint.slice(0, 16)}…` : ''}${p.host_key ? ` · ${p.host_key}` : ''}</div>` : html`<span class="warn-ink">not paired</span>`;
    return html`<section class="panel" data-panel="inventory">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Inventory</h2><p class="panel-sub">as its TandemNode agent read it</p></div></header>
      <dl class="kv">
        <div><dt>DMI model</dt><dd>${n.dmi || NOT_READ}</dd></div>
        <div><dt>hardware</dt><dd>${n.hw}</dd></div>
        <div><dt>serial</dt><dd class="num">${v(inv.serial)}</dd></div>
        <div><dt>node id</dt><dd class="num">${n.id}</dd></div>
        <div><dt>OS</dt><dd>${v(inv.os)}</dd></div>
        <div><dt>kernel</dt><dd class="num">${v(inv.kernel)}</dd></div>
        <div><dt>driver / CUDA</dt><dd class="num">${v(inv.driver)} / ${v(inv.cuda)}</dd></div>
        <div><dt>TandemLLM</dt><dd class="num">${n.version || NOT_READ}</dd></div>
        <div><dt>disks</dt><dd>${v(inv.disk)}</dd></div>
        <div><dt>NICs</dt><dd>${v(inv.nics)}</dd></div>
        <div><dt>CX7</dt><dd>${inv.cx7 ?? html`<span class="muted">no ConnectX port</span>`}</dd></div>
        <div><dt>trust</dt><dd>${trust}</dd></div>
        <div><dt>in the fleet</dt><dd>since ${new Date(n.since * 1000).toLocaleDateString('en-GB')} (${span(Date.now() / 1000 - n.since)})</dd></div>
      </dl>
    </section>`;
  }

  private page(): TemplateResult {
    const n = this.node;
    if (!this.fleetState) return html`<div class="skeleton" style="height:240px"></div>`;
    if (!n)
      return html`<nav class="xs muted sp-crumb"><a href="#/sparks">Sparks</a></nav><header class="page-head" data-page="spark-gone"><div class="eyebrow">Fleet · Spark</div><h1>Not in the fleet</h1><p class="page-sub">No Spark ${this.nodeId} is paired with this controller: it was forgotten or released.</p></header><a class="btn" href="#/sparks">All Sparks</a>`;
    return html`${this.header(n)}${this.edit ? this.editPanel(n) : nothing}${this.banners(n)}
      <div class="g-wide">
        <div class="mon-col">${this.chartsPanel(n)}${this.actionsPanel(n)}${this.linksPanel(n)}</div>
        <div class="mon-col">${this.servesPanel(n)}${this.inventoryPanel(n)}</div>
      </div>
      <p class="xs faint mon-foot">read ${clock(Date.now() / 1000)} · every 10 s while live</p>`;
  }

  render() {
    // the dialog stays when the Spark leaves the fleet (forget, release): its result is read there
    return html`${this.page()}<qse-node-action @qse-action-done=${() => this.reload()}></qse-node-action>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-spark-node': QseSparkNode;
  }
}
