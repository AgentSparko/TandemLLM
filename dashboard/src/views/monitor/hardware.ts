// <qse-hardware>: every metric of every Spark (VIS-248, concept §5 and §8.4, the v3 prototype's
// Hardware). Scope All (or a deployment's members): a matrix, one panel per metric group with a cell
// per Spark (its value now; a click scopes the page to that Spark and opens its sheet at the group)
// and the chart with Total · Stacked · Lines · Split · Table, each aggregated with its own operation;
// below a group what the newest samples add (memory split, cores, mounts and NVMe, interfaces, fabric
// ports with RDMA, thermal zones and fans). Scope one Spark: its full sheet (the memory split with
// the ceiling and the peak, its engines, its limits, every chart). Last the Host: the machine the
// controller runs on, the controller's own process and the RTT to each Spark. A field the board does
// not expose says so; history before the store began is a gap, never a line to zero. Today's System
// page stays below Hardware (#/hardware/system). The right sidebar: the node inspector.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { fleet, METRICS, type FleetHost, type FleetNode, type FleetSample, type MetricKey, type MetricSeries, type NodeMemory, type NodeSample } from '../../api/fleet';
import type { ChartEvent } from '../../charts/fleet-chart';
import '../../charts/fleet-chart';
import type { ChartMode, NodeSeries } from '../../charts/modes';
import { refreshContext, type ContextContent } from '../../lib/context';
import { absentWhy, aggregateNow, HW_GROUPS, holdWords, lastValue, NOT_EXPOSED, noSamples, type HwGroup } from '../../lib/monitor';
import { parseHash, setParams, type View } from '../../lib/router';
import { scopeParams, scopeSource } from '../../lib/scope';
import { nodeChip } from '../../ui/node-chip';
import { MonitorPage } from './base';
import { fmt, inspector, memBar, memLegend, span, typeBadge } from './bits';
import { chartEvents, fromFleet } from './data';

@customElement('qse-hardware')
export class QseHardware extends MonitorPage {
  protected readonly view: View = 'hardware';
  @state() private series: Partial<Record<MetricKey, MetricSeries>> = {};
  @state() private samples: Record<string, FleetSample | 'absent'> = {};
  @state() private memory: NodeMemory[] = [];
  @state() private host: FleetHost | 'absent' | null = null;
  @state() private rtt: MetricSeries | null = null;
  @state() private marks: ChartEvent[] = [];
  @state() private depNow: Record<string, { tps: number | null; req: number | null }> = {};
  @state() private inspected: string | null = null;
  private focused = '';

  protected scopeChanged(): void {
    this.series = {};
  }

  /** the Spark whose sheet shows: the scope's, or a fleet of one's only Spark (it has no scope to pick) */
  private get sheetNode(): FleetNode | null {
    const f = this.fleetState;
    if (!f) return null;
    const sc = this.resolved;
    if (sc.kind === 'node') return f.nodes.find((n) => n.id === sc.id) ?? null;
    return f.nodes.length === 1 && sc.kind === 'all' ? f.nodes[0] : null;
  }

  protected async load(isCurrent: () => boolean): Promise<void> {
    const s = this.resolved;
    const sheet = this.sheetNode;
    const src = sheet ? `node:${sheet.id}` : scopeSource(s);
    const nodes = this.inScope;
    const one = !!sheet;
    const metrics = one ? (Object.keys(METRICS) as MetricKey[]) : HW_GROUPS.map((g) => g.metric);
    const [answers, samples, mem, host, rtt] = await Promise.all([
      Promise.all(metrics.map((m) => fleet.metrics({ source: src, metric: m, range: s.range }).catch(() => null))),
      Promise.all(nodes.map((n) => fromFleet(() => fleet.sample(n.id)).catch(() => 'absent' as const))),
      fleet.memory().catch(() => null),
      fromFleet(() => fleet.host()).catch(() => null),
      fleet.metrics({ source: 'all', metric: 'rtt', range: s.range }).catch(() => null),
    ]);
    const deps = sheet ? (this.fleetState?.deployments ?? []).filter((d) => d.members.includes(sheet.id)) : [];
    const depNow = Object.fromEntries(
      await Promise.all(
        deps.map(async (d) => {
          const [t, r] = await Promise.all(['tps', 'req'].map((m) => fleet.metrics({ source: `deployment:${d.id}`, metric: m as MetricKey, range: '5m' }).catch(() => null)));
          const v = (x: MetricSeries | null) => (x ? aggregateNow(x.series.filter((y) => y.node === sheet!.id).map((y) => ({ v: lastValue(y.values) })), 'sum') : null);
          return [d.id, { tps: v(t), req: v(r) }] as const;
        }),
      ),
    );
    const first = answers.find((a): a is MetricSeries => !!a);
    const marks = first ? await chartEvents(first.t0).catch(() => []) : [];
    if (!isCurrent()) return;
    this.series = Object.fromEntries(metrics.map((m, i) => [m, answers[i]]).filter(([, a]) => a));
    this.samples = Object.fromEntries(nodes.map((n, i) => [n.id, samples[i]]));
    if (mem) this.memory = mem.nodes;
    this.host = host;
    this.rtt = rtt;
    this.marks = marks;
    this.depNow = depNow;
    refreshContext();
  }

  updated(): void {
    // a cell's click or a link (?focus=temp, ?focus=host) scrolls to its group once
    const focus = parseHash().params.get('focus');
    // once the figures are in: the groups above have their height then
    if (!focus || focus === this.focused || !Object.keys(this.series).length || !this.host) return;
    const el = this.querySelector<HTMLElement>(`#hw-${CSS.escape(focus)}`);
    if (!el) return;
    this.focused = focus;
    setParams({ focus: null });
    // after the charts drew at their height
    setTimeout(() => {
      el.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      el.classList.add('is-focus');
      setTimeout(() => el.classList.remove('is-focus'), 1600);
    }, 250);
  }

  private sample(id: string): NodeSample | null {
    const x = this.samples[id];
    return x && x !== 'absent' ? x.sample : null;
  }
  private hostNode(): string | null {
    return this.host && this.host !== 'absent' ? this.host.node : null;
  }

  /** a cell of the matrix: the Spark scopes the page and opens its sheet at this group */
  private toSheet(id: string, group: string): void {
    setParams({ ...scopeParams({ ...this.scope, kind: 'node', id }), focus: group });
  }

  private nodeSeries(m: MetricSeries): NodeSeries[] {
    const nodes = this.fleetState?.nodes ?? [];
    return m.series.map((s) => {
      const n = nodes.find((x) => x.id === s.node);
      return { id: s.node, alias: n?.alias ?? s.node, colour: n?.colour ?? 1, values: s.values };
    });
  }

  private chart(id: string, m: MetricSeries | undefined, mode: ChartMode, height: number): TemplateResult {
    if (!m) return html`<div class="skeleton" style=${`height:${height}px`}></div>`;
    const info = METRICS[m.metric];
    return html`<qse-fleet-chart chart-id=${`hw-${id}`} .series=${this.nodeSeries(m)} agg=${m.agg} unit=${m.unit} .t0=${m.t0} .step=${m.step_s} .decimals=${info.decimals} .cap=${m.cap} .max=${info.max ?? null} .events=${this.marks} mode=${mode} .height=${height} aria-label=${`${info.label} over ${m.range}`}></qse-fleet-chart>`;
  }

  // ---- what a group's samples add ----------------------------------------------------------------
  private extra(kind: NonNullable<HwGroup['extra']>, nodes: FleetNode[], one: boolean): TemplateResult {
    const row = (n: FleetNode, body: TemplateResult) => html`<div class="hw-x-row">${one ? nothing : nodeChip(n, { status: false })}<div class="hw-x-body">${body}</div></div>`;
    const rows = nodes.map((n) => {
      const s = this.sample(n.id);
      const x = this.samples[n.id];
      if (!s) return row(n, html`<span class="muted small">${x === 'absent' ? 'this controller does not give samples yet' : x?.note ?? 'no sample'}</span>`);
      switch (kind) {
        case 'gpu': {
          const g = s.gpus ?? [];
          if (!g.length) return row(n, html`<span class="muted small">no GPU in the sample</span>`);
          return row(
            n,
            html`${g.map(
              (x) => html`<span class="small">${x.name ?? 'GPU'} · encoder <span class="num">${x.enc == null ? NOT_EXPOSED : `${fmt(x.enc)} %`}</span> · decoder <span class="num">${x.dec == null ? NOT_EXPOSED : `${fmt(x.dec)} %`}</span> · clock events
                ${x.throttled == null ? html`<span class="muted">${NOT_EXPOSED}</span>` : x.throttle?.filter((r) => r !== 'gpu_idle').length ? html`<span class="warn-ink">${x.throttle.filter((r) => r !== 'gpu_idle').map((r) => r.replace(/_/g, ' ')).join(', ')}</span>` : html`<span class="ok-ink">none</span>`}</span>`,
            )}`,
          );
        }
        case 'mem':
          return row(n, html`${memBar(this.memory.find((m) => m.node === n.id), true)}<span class="xs muted num">${s.memory?.available != null ? `MemAvailable ${fmt(s.memory.available, 1)} GiB` : ''}${s.memory?.swap_total != null ? ` · swap ${fmt(s.memory.swap_used ?? 0, 1)} of ${fmt(s.memory.swap_total, 0)} GiB` : ''}</span>`);
        case 'cores': {
          const cores = s.cpu?.cores ?? [];
          if (!cores.length) return row(n, html`<span class="muted small">per core: ${NOT_EXPOSED}</span>`);
          const perf = cores.filter((c) => c.kind === 'performance').length;
          return row(
            n,
            html`<div class="cores" role="img" aria-label=${`${n.alias}: ${cores.length} cores`} style=${`--n:${cores.length}`}>${cores.map((c) => html`<i class=${c.kind === 'efficiency' ? 'is-e' : 'is-p'} style=${`height:${Math.max(3, c.util ?? 0)}%;--c:var(--n${((n.colour - 1) % 4) + 1})`} title=${`core ${c.cpu}${c.kind ? ` (${c.kind})` : ''}: ${fmt(c.util)} %${c.mhz ? ` at ${c.mhz} MHz` : ''}`}></i>`)}</div>
              <div class="cores-lbl xs muted"><span>${perf ? `${perf} performance · ${cores.length - perf} efficiency cores` : `${cores.length} cores`}</span><span class="num">load ${s.cpu?.load?.map((l) => fmt(l, 2)).join(' / ') ?? '—'} · ${s.cpu?.temp_c != null ? `${fmt(s.cpu.temp_c)} °C` : `temperature ${NOT_EXPOSED}`}</span></div>`,
          );
        }
        case 'disk': {
          const d = s.disks ?? {};
          return row(
            n,
            html`<span class="small">${(d.fs ?? []).map((f) => html`<span class="hw-pill num">${f.path} ${fmt(f.free_gb, 0)} of ${fmt(f.total_gb, 0)} GB free</span>`)}
              ${(d.nvme ?? []).length ? (d.nvme ?? []).map((x) => html`<span class="hw-pill num">${x.name} ${fmt(x.temp_c)} °C</span>`) : html`<span class="hw-pill muted">NVMe temperature ${NOT_EXPOSED}</span>`}
              ${(d.devices ?? []).map((x) => html`<span class="hw-pill num">${x.name} r ${fmt(x.read_mbps, 1)} · w ${fmt(x.write_mbps, 1)} MB/s${x.busy != null ? ` · ${fmt(x.busy * 100)} % busy` : ''}</span>`)}</span>`,
          );
        }
        case 'lan':
          return row(n, html`<span class="small">${(s.net?.lan ?? []).map((l) => html`<span class="hw-pill num">${l.iface} ${l.state ?? ''} · ${l.speed_mbps ? `${l.speed_mbps >= 1000 ? `${l.speed_mbps / 1000} Gb/s` : `${l.speed_mbps} Mb/s`}` : 'speed —'} · rx ${fmt(l.rx_mbps, 2)} tx ${fmt(l.tx_mbps, 2)} MB/s</span>`)}</span>`);
        case 'fabric': {
          const ports = s.net?.fabric ?? [];
          if (!ports.length) return row(n, html`<span class="muted small">no ConnectX port: ${NOT_EXPOSED}</span>`);
          return row(
            n,
            html`<span class="small">${ports.map(
              (p) =>
                html`<span class="hw-pill num ${p.state === 'up' ? '' : 'muted'}">${p.iface} ${p.state ?? ''}${p.speed_mbps ? ` · ${p.speed_mbps / 1000}G` : ''}${p.mtu ? ` · MTU ${p.mtu}` : ''}${p.rx_gbps != null ? ` · rx ${fmt(p.rx_gbps, 2)} tx ${fmt(p.tx_gbps, 2)} Gb/s` : ''}${p.rdma ? ` · RDMA ${p.rdma.device ?? ''} ${p.rdma.state ?? ''}${p.rdma.rx_gbps != null ? ` ${fmt(p.rdma.rx_gbps, 2)} / ${fmt(p.rdma.tx_gbps, 2)} Gb/s` : ''}` : ''}</span>`,
            )}</span>`,
          );
        }
      }
    });
    return html`<div class="hw-x">${rows}</div>`;
  }

  private board(nodes: FleetNode[], one: boolean): TemplateResult {
    return html`<section class="panel" id="hw-board" data-group="board">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Board</h2><p class="panel-sub">thermal zones, fans and uptime from the newest sample</p></div></header>
      <div class="hw-x">
        ${nodes.map((n) => {
          const s = this.sample(n.id);
          const z = s?.board?.zones ?? [];
          const fans = s?.board?.fans ?? [];
          return html`<div class="hw-x-row">${one ? nothing : nodeChip(n, { status: false })}<div class="hw-x-body small">
            ${!s
              ? html`<span class="muted">${(this.samples[n.id] as FleetSample | undefined)?.note ?? 'no sample'}</span>`
              : html`<span class="hw-pill num">${z.length ? `${z.length} zones · hottest ${fmt(Math.max(...z.map((x) => x.temp_c)), 1)} °C` : `thermal zones ${NOT_EXPOSED}`}</span>
                  <span class="hw-pill num">${fans.length ? fans.map((f) => `${f.name.split('/').pop()} ${fmt(f.rpm)} rpm`).join(' · ') : html`<span class="muted">fan ${NOT_EXPOSED}</span>`}</span>
                  <span class="hw-pill num">up ${s.board?.uptime_s != null ? span(s.board.uptime_s) : '—'}</span>
                  ${one && z.length ? html`<div class="hw-zones">${z.map((x) => html`<span class="hw-pill num">${x.type ?? x.zone} ${fmt(x.temp_c, 1)} °C</span>`)}</div>` : nothing}`}
          </div></div>`;
        })}
      </div>
    </section>`;
  }

  // ---- scope All: the matrix ----------------------------------------------------------------------
  private matrix(nodes: FleetNode[]): TemplateResult {
    const sc = this.resolved;
    const dep = sc.kind === 'deployment' ? this.fleetState?.deployments.find((d) => d.id === sc.id) : null;
    return html`${dep ? html`<p class="panel inset small muted">Hardware is per Spark: the ${nodes.length} member${nodes.length === 1 ? '' : 's'} of ${dep.name}, their whole boxes.</p>` : nothing}
      <div class="hw-grid">
        ${HW_GROUPS.map((g) => {
          const m = this.series[g.metric];
          const info = METRICS[g.metric];
          const absent = m && noSamples(m.series.flatMap((x) => x.values));
          return html`<section class="panel hw-group" id=${`hw-${g.id}`} data-group=${g.id}>
            <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">${g.title}</h2><p class="panel-sub">${g.metric === 'power' && nodes.every((n) => this.sample(n.id) && !this.sample(n.id)!.gpus?.some((x) => x.power_cap_w != null)) ? `${g.sub} · power cap ${NOT_EXPOSED}` : g.sub}</p></div></header>
            <div class="hw-cells" role="list">
              ${nodes.map((n) => {
                const v = m ? lastValue(m.series.find((x) => x.node === n.id)?.values ?? []) : null;
                return html`<button type="button" role="listitem" class="hw-cell" data-node=${n.id} title=${`${n.alias}: open its sheet at ${g.title}`} @click=${() => this.toSheet(n.id, g.id)}>${nodeChip(n, { status: false })}<b class="num">${v == null ? '—' : fmt(v, info.decimals)}</b><span class="muted xs">${v == null ? absentWhy(g.metric, n, this.sample(n.id), this.hostNode()) : info.unit}</span></button>`;
              })}
            </div>
            ${absent ? html`<p class="hw-absent small muted">${nodes.length === 1 ? absentWhy(g.metric, nodes[0], this.sample(nodes[0].id), this.hostNode()) : `No Spark in scope gives this: ${absentWhy(g.metric, nodes[0], this.sample(nodes[0].id), this.hostNode())}`}.</p>` : this.chart(g.id, m, g.mode, 180)}
            ${g.extra ? this.extra(g.extra, nodes, false) : nothing}
          </section>`;
        })}
        ${this.board(nodes, false)}
      </div>`;
  }

  // ---- scope one Spark: its sheet ------------------------------------------------------------------
  private sheet(n: FleetNode): TemplateResult {
    const f = this.fleetState!;
    const s = this.sample(n.id);
    const mem = this.memory.find((m) => m.node === n.id);
    const deps = f.deployments.filter((d) => d.members.includes(n.id));
    const g0 = s?.gpus?.[0];
    const ttft = lastValue(this.series.ttft?.series[0]?.values ?? []);
    const throttle = g0?.throttle?.filter((r) => r !== 'gpu_idle') ?? [];
    return html`<section class="panel hw-head" data-sheet=${n.id}>
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">${nodeChip(n, { lg: true })} <span class="muted small">${n.dmi || n.hw} · ${n.transport === 'tunnel' ? 'SSH tunnel' : 'LAN'} ${n.address}</span></h2><p class="panel-sub">${n.inventory.os ?? ''}${n.inventory.kernel ? ` · kernel ${n.inventory.kernel}` : ''}${n.inventory.driver ? ` · driver ${n.inventory.driver}` : ''}${n.inventory.cuda ? ` · CUDA ${n.inventory.cuda}` : ''}</p></div><div class="panel-tools"><a class="btn btn-sm" href=${`#/sparks/${encodeURIComponent(n.id)}`}>Node page</a></div></header>
        <div class="eyebrow hw-mem-t">Engine memory split · ${mem ? `${mem.usable} GiB usable` : 'unified memory'}</div>
        ${memBar(mem)}${mem ? memLegend(mem) : nothing}
      </section>
      <div class="hw-grid">
        ${deps.map(
          (d) => html`<section class="panel" data-engine=${d.id}>
            <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">${typeBadge(d.type)} ${d.name}</h2><p class="panel-sub">engine on ${n.alias} · ${d.pack || d.model} · ctx ${fmt(d.ctx / 1024)}k${d.type === 'split' ? ` · stage ${d.members.indexOf(n.id) + 1} of ${d.members.length}` : ''}</p></div></header>
            <dl class="kv">
              <div><dt>status</dt><dd class=${d.status === 'serving' ? 'ok-ink' : 'warn-ink'}>${d.status_text}</dd></div>
              <div><dt>decode</dt><dd class="num">${fmt(this.depNow[d.id]?.tps)} tok/s</dd></div>
              <div><dt>active requests</dt><dd class="num">${fmt(this.depNow[d.id]?.req)}</dd></div>
              <div><dt>TTFT p50</dt><dd class="num">${ttft == null ? 'no request ended in the window' : `${fmt(ttft)} ms`}</dd></div>
              <div><dt>API id</dt><dd class="num">${d.api_id}</dd></div>
            </dl>
            ${n.pairing.state === 'local' ? html`<p class="xs muted">KV use, prefix cache and MTP acceptance of this box’s engine: <a href="#/hardware/system">System</a>.</p>` : nothing}
          </section>`,
        )}
        <section class="panel" data-panel="limits">
          <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Limits</h2><p class="panel-sub">read from the Spark, not assumed</p></div></header>
          <dl class="kv">
            <div><dt>power cap</dt><dd>${g0?.power_cap_w != null ? `${fmt(g0.power_cap_w)} W` : html`<span class="muted">${NOT_EXPOSED}</span>`}</dd></div>
            <div><dt>GPU clock events</dt><dd>${g0?.throttled == null ? html`<span class="muted">${NOT_EXPOSED}</span>` : throttle.length ? html`<span class="warn-ink">${throttle.map((r) => r.replace(/_/g, ' ')).join(', ')}</span>` : html`<span class="ok-ink">none</span>`}</dd></div>
            <div><dt>GPU temperature rule</dt><dd>85 °C for ${holdWords(60)}</dd></div>
            <div><dt>engine ceiling</dt><dd class="num">${mem ? `${mem.ceiling} GiB · peak ${fmt(mem.peak, 1)} in 24 h` : '—'}</dd></div>
            <div><dt>swap</dt><dd class="num">${s?.memory?.swap_total != null ? `${fmt(s.memory.swap_used ?? 0, 1)} of ${fmt(s.memory.swap_total, 0)} GiB used` : NOT_EXPOSED}</dd></div>
            <div><dt>fan</dt><dd>${s?.board?.fans?.length ? s.board.fans.map((x) => `${fmt(x.rpm)} rpm`).join(' · ') : html`<span class="muted">${s ? NOT_EXPOSED : '—'}</span>`}</dd></div>
            <div><dt>CPU temperature</dt><dd class="num">${s?.cpu?.temp_c != null ? `${fmt(s.cpu.temp_c, 1)} °C` : NOT_EXPOSED}</dd></div>
            <div><dt>GPU memory</dt><dd>${s?.memory?.unified ? 'unified with the system' : NOT_EXPOSED}</dd></div>
            <div><dt>uptime</dt><dd class="num">${s?.board?.uptime_s != null ? span(s.board.uptime_s) : '—'}</dd></div>
          </dl>
          ${this.samples[n.id] && this.samples[n.id] !== 'absent' && (this.samples[n.id] as FleetSample).note ? html`<p class="xs muted">${(this.samples[n.id] as FleetSample).note}</p>` : nothing}
        </section>
      </div>
      <div class="hw-grid">
        ${[...HW_GROUPS.map((x) => x.metric), 'rtt' as const].map((k) => {
          const m = this.series[k];
          const g = HW_GROUPS.find((x) => x.metric === k);
          const info = METRICS[k];
          const absent = m && noSamples(m.series[0]?.values ?? []);
          const extra = g?.extra;
          return html`<section class="panel hw-group" id=${`hw-${g?.id ?? k}`} data-group=${g?.id ?? k}>
            <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">${g?.title ?? info.label}</h2><p class="panel-sub">${info.unit}${m ? ` · now ${fmt(lastValue(m.series[0]?.values ?? []), info.decimals)}` : ''}</p></div></header>
            ${absent ? html`<p class="hw-absent small muted">${absentWhy(k, n, s, this.hostNode())}.</p>` : this.chart(`n-${k}`, m, 'lines', 150)}
            ${extra ? this.extra(extra, [n], true) : nothing}
          </section>`;
        })}
        ${this.board([n], true)}
      </div>`;
  }

  // ---- the host ------------------------------------------------------------------------------------
  private hostSection(): TemplateResult {
    const h = this.host;
    const f = this.fleetState!;
    if (h === 'absent' || h == null) {
      return html`<section class="hw-host" id="hw-host" data-host><div class="eyebrow">Host</div><p class="panel inset small muted">${h === 'absent' ? 'This controller does not report its machine yet; a newer TandemApp does (GET /v1/fleet/host).' : 'Reading the host…'}</p></section>`;
    }
    const node = h.node ? f.nodes.find((n) => n.id === h.node) : null;
    const rtt = this.rtt;
    const rttAbsent = !rtt || noSamples(rtt.series.flatMap((x) => x.values));
    const c = h.controller;
    return html`<section class="hw-host" id="hw-host" data-host>
      <div class="hw-host-h"><div><div class="eyebrow">Host</div><h2>${h.name} <span class="muted small">runs TandemApp and the controller${h.os ? ` · ${h.os}` : ''}${node ? html` · it is ${nodeChip(node, { status: false })}` : ''}</span></h2></div><a class="btn btn-ghost btn-sm" href="#/app">Controller settings</a></div>
      ${h.may_sleep ? html`<div class="panel is-warn small"><span class="warn-ink">⚠</span> This machine may sleep. While it sleeps the API is down and no metrics are stored. Keep it awake, or move the controller to a Spark.</div>` : nothing}
      <div class="mtiles is-4">
        <div class="mtile"><div class="mtile-label">CPU</div><div class="mtile-val num">${fmt(h.cpu.util)}<span class="u">%</span></div><div class="mtile-sub">${h.cpu.cores ? `${h.cpu.cores} cores` : 'cores —'}</div></div>
        <div class="mtile"><div class="mtile-label">Memory</div><div class="mtile-val num">${fmt(h.memory.used_gib, 1)}<span class="u">/ ${fmt(h.memory.total_gib, 1)} GiB</span></div><div class="mtile-sub">the whole machine</div></div>
        <div class="mtile"><div class="mtile-label">Controller CPU</div><div class="mtile-val num">${fmt(c.cpu, 1)}<span class="u">%</span></div><div class="mtile-sub">of one core · ${c.threads} threads</div></div>
        <div class="mtile"><div class="mtile-label">Controller memory</div><div class="mtile-val num">${fmt(c.rss_mib)}<span class="u">MiB</span></div><div class="mtile-sub">up ${span(c.uptime_s)}</div></div>
      </div>
      <section class="panel" data-panel="rtt">
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">RTT to each Spark</h2><p class="panel-sub">ms · from the controller to each agent · max of the scope</p></div></header>
        ${rttAbsent ? html`<p class="hw-absent small muted">${node && f.nodes.length === 1 ? `The controller runs on ${node.alias} itself: no network between them to time.` : 'No round trip timed in this range yet.'}</p>` : this.chart('host-rtt', rtt!, 'lines', 160)}
      </section>
    </section>`;
  }

  // ---- the sidebar -------------------------------------------------------------------------------
  protected context(): ContextContent {
    const f = this.fleetState;
    if (!f || !f.nodes.length) return { title: 'Node inspector', body: html`<p class="muted small">The node inspector shows a Spark of the fleet once TandemApp’s controller answers on this server.</p>` };
    const sc = this.resolved;
    const id = this.inspected && f.nodes.some((n) => n.id === this.inspected) ? this.inspected : sc.kind === 'node' ? sc.id! : (this.inScope[0]?.id ?? f.nodes[0].id);
    const n = f.nodes.find((x) => x.id === id)!;
    const now = Object.fromEntries((['tps', 'temp', 'power', 'gpu'] as MetricKey[]).map((k) => [k, this.sample(n.id)?.node?.[k] ?? lastValue(this.series[k]?.series.find((x) => x.node === n.id)?.values ?? [])]));
    if (now.tps == null) now.tps = f.tps[n.id] ?? null;
    return {
      title: 'Node inspector',
      body: html`${f.nodes.length > 1 ? html`<div class="insp-pick" role="group" aria-label="Spark">${f.nodes.map((x) => html`<button type="button" class="btn btn-ghost btn-sm ${x.id === id ? 'is-on' : ''}" aria-pressed=${x.id === id} @click=${() => ((this.inspected = x.id), refreshContext())}>${nodeChip(x)}</button>`)}</div>` : nothing}
        ${inspector({ node: n, deps: f.deployments, now, memory: this.memory.find((m) => m.node === n.id) ?? null, sample: this.sample(n.id) })}`,
    };
  }

  render() {
    const f = this.fleetState;
    const one = this.sheetNode;
    const head = html`<header class="page-head" data-page="hardware">
      <div class="eyebrow">Monitor</div>
      <h1>Hardware</h1>
      <p class="page-sub">${one && f && f.nodes.length === 1 ? html`${one.alias}, the fleet’s one Spark: every figure it gives, and the machine the controller runs on.` : one ? html`${one.alias}: the full sheet. Pick All Sparks in the scope bar for the matrix.` : 'Every metric group for the scope. Each chart has its own Total · Stacked · Lines · Split · Table switch; a cell opens that Spark’s sheet.'} <a href="#/hardware/system">System</a> keeps this server’s engine, memory plan and limits.</p>
    </header>`;
    if (!f) return html`${head}<div class="skeleton" style="height:240px"></div>`;
    const nodes = this.inScope;
    return html`${head}${one ? this.sheet(one) : this.matrix(nodes)}${this.hostSection()}`;
  }
}
