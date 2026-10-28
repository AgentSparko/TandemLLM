// VIS-249 (tests for VIS-248): the Monitor pages' own rules, without a page. Each Spark's share of a
// total with the metric's operation and nulls left out; the energy since midnight integrated from
// power samples, gaps included; the timeline's paging; an alert rule's edit and what it refuses; who
// acknowledged an alert; the words for what a board does not expose, per metric; and the system
// notifications that a newly firing alert makes.
import { describe, expect, it } from 'vitest';
import type { FleetAlert, FleetEvent, FleetNode, FleetRule, NodeSample } from '../src/api/fleet.ts';
import { toNotify } from '../src/lib/alert-notify.ts';
import {
  absentWhy,
  ackInfo,
  aggregateNow,
  alertRule,
  byDay,
  draftOf,
  energyKWh,
  eventGlyph,
  eventTone,
  exposes,
  HW_GROUPS,
  holdWords,
  lastValue,
  mayHaveMore,
  mergeEvents,
  midnight,
  newerCursor,
  NOT_EXPOSED,
  nowByNode,
  olderCursor,
  rulePatch,
  scopeLabel,
  scopeOptions,
  shareParts,
  shareSegments,
  shareTip,
} from '../src/lib/monitor.ts';
import { METRIC_KEYS } from '../src/api/fleet.ts';
import { MockFleet } from '../mock/fleet.ts';

const nodes = [
  { id: 'desk', alias: 'Desk', colour: 1 },
  { id: 'r1', alias: 'R-1', colour: 2 },
  { id: 'r3', alias: 'R-3', colour: 4 },
];

describe('totals and each Spark’s share', () => {
  it('Share bar: 412 tok/s in total splits by node, and the tooltip lists each node’s value and share', () => {
    const parts = shareParts(nodes, { desk: 206, r1: 103, r3: 103 });
    expect(aggregateNow(parts, 'sum')).toBe(412);
    const seg = shareSegments(parts);
    expect(seg.map((s) => [s.id, s.f])).toEqual([
      ['desk', 0.5],
      ['r1', 0.25],
      ['r3', 0.25],
    ]);
    expect(shareTip(parts, 'tok/s', String, 412).split('\n')).toEqual(['Desk: 206 tok/s (50 %)', 'R-1: 103 tok/s (25 %)', 'R-3: 103 tok/s (25 %)', 'Σ 412 tok/s']);
  });

  it('each metric with its own operation: Σ, mean, max; a node without a sample is left out, never zero', () => {
    const parts = shareParts(nodes, { desk: 60, r1: null, r3: 86 });
    expect(aggregateNow(parts, 'sum')).toBe(146);
    expect(aggregateNow(parts, 'mean')).toBe(73);
    expect(aggregateNow(parts, 'max')).toBe(86);
    expect(aggregateNow(parts, 'maxmean')).toBe(86);
    expect(aggregateNow(shareParts(nodes, {}), 'sum')).toBeNull();
    expect(shareTip(parts, 'W', String, 146)).toContain('R-1: no sample');
    // the node without a value gets no segment
    expect(shareSegments(parts).map((s) => s.id)).toEqual(['desk', 'r3']);
  });

  it('memory keeps its free part as a segment of the capacity', () => {
    const seg = shareSegments(shareParts(nodes.slice(0, 2), { desk: 60, r1: 30 }), 243.2);
    expect(seg.map((s) => s.id)).toEqual(['desk', 'r1', null]);
    expect(seg.reduce((a, s) => a + s.f, 0)).toBeCloseTo(1, 6);
    expect(seg[2].f).toBeCloseTo((243.2 - 90) / 243.2, 6);
  });

  it('the newest value skips the nulls at the end; a series of nulls has none', () => {
    expect(lastValue([1, 2, null, null])).toBe(2);
    expect(lastValue([null, null])).toBeNull();
    expect(nowByNode({ series: [{ node: 'a', values: [3, null] }, { node: 'b', values: [null] }] })).toEqual({ a: 3, b: null });
  });
});

describe('energy today, computed from the power samples', () => {
  it('a constant 120 W for 2 h is 0.24 kWh; only the part after midnight counts', () => {
    const t0 = 1000;
    const vals = Array.from({ length: 13 }, () => 120);
    expect(energyKWh(vals, t0, 600, t0, t0 + 7200).kwh).toBeCloseTo(0.24, 6);
    const half = energyKWh(vals, t0, 600, t0 + 3600, t0 + 7200);
    expect(half.kwh).toBeCloseTo(0.12, 6);
    expect(half.covered).toBe(1);
  });

  it('a ramp is integrated as the trapezoid, cut exactly at the bounds', () => {
    // 0 W to 600 W over 600 s: 50 Wh, a half from 300 s on is 150 W..600 W × 300 s
    expect(energyKWh([0, 600], 0, 600, 0, 600).kwh).toBeCloseTo(0.05, 6);
    expect(energyKWh([0, 600], 0, 600, 300, 600).kwh).toBeCloseTo((((300 + 600) / 2) * 300) / 3.6e6, 9);
  });

  it('a gap (no sample: before the store began, the node off) adds nothing and lowers the coverage', () => {
    const r = energyKWh([null, null, 100, 100, 100], 0, 600, 0, 2400);
    expect(r.kwh).toBeCloseTo((100 * 1200) / 3.6e6, 9);
    expect(r.covered).toBeCloseTo(0.5, 6);
    expect(energyKWh([null, null], 0, 600, 0, 600)).toEqual({ kwh: null, covered: 0 });
    expect(energyKWh([1, 2], 0, 600, 600, 600).kwh).toBeNull();
  });

  it('midnight is the start of the local day', () => {
    const t = Date.parse('2026-10-28T15:30:00') / 1000;
    expect(midnight(t)).toBe(Date.parse('2026-10-28T00:00:00') / 1000);
  });

  it('the mock fleet’s power over today integrates to a GB10’s range: 60–180 W a box', () => {
    const now = Date.parse('2026-10-28T18:00:00') / 1000;
    const m = new MockFleet({ scenario: 'fleet4', now });
    const p = m.metrics('all', 'power', '24h', now);
    const from = midnight(now);
    for (const s of p.series) {
      const e = energyKWh(s.values, p.t0, p.step_s, from, now);
      const hours = ((now - from) * e.covered) / 3600;
      expect(e.kwh! / hours).toBeGreaterThan(0.05);
      expect(e.kwh! / hours).toBeLessThan(0.181);
    }
  });
});

const ev = (id: string, at: number, extra: Partial<FleetEvent> = {}): FleetEvent => ({ id, at, type: 'node', severity: null, node: 'desk', deployment: null, text: id, detail: '', ...extra });

describe('the timeline', () => {
  it('pages back with `before` = the oldest shown, polls with `since` = the newest; each event once, newest first', () => {
    const first = [ev('c', 30), ev('b', 20)];
    expect([olderCursor(first), newerCursor(first), newerCursor(first, 0)]).toEqual([20, 25, 30]);
    const older = [ev('b', 20), ev('a', 10)];
    const merged = mergeEvents(first, older);
    expect(merged.map((e) => e.id)).toEqual(['c', 'b', 'a']);
    expect(mergeEvents([ev('d', 40)], merged).map((e) => e.id)).toEqual(['d', 'c', 'b', 'a']);
    // a poll from before the newest brings it again, and it shows once
    expect(mergeEvents([ev('e', 30), ev('c', 30)], merged).map((e) => e.id)).toEqual(['e', 'c', 'b', 'a']);
    expect([olderCursor([]), newerCursor([])]).toEqual([null, null]);
    expect([mayHaveMore(Array(50).fill(ev('x', 1)), 50), mayHaveMore([ev('x', 1)], 50)]).toEqual([true, false]);
  });

  it('the mock pages exactly: the second page starts after the first, and nothing repeats', () => {
    const now = Date.parse('2026-10-28T18:00:00') / 1000;
    const m = new MockFleet({ scenario: 'fleet4', now });
    const p1 = m.events(now, { limit: 4 });
    const p2 = m.events(now, { limit: 4, before: olderCursor(p1)! });
    expect(p2.every((e) => e.at < olderCursor(p1)!)).toBe(true);
    const all = mergeEvents(p1, p2);
    expect(all).toHaveLength(p1.length + p2.length);
    expect(all.map((e) => e.id)).toEqual(m.events(now, { limit: 8 }).map((e) => e.id));
  });

  it('grouped by day: today, yesterday, then dates', () => {
    const now = Date.parse('2026-10-28T12:00:00') / 1000;
    const g = byDay([ev('a', now - 60), ev('b', now - 86400), ev('c', now - 3 * 86400)], now);
    expect(g.map(([d, e]) => [d, e.length])).toEqual([
      ['Today', 1],
      ['Yesterday', 1],
      [new Date((now - 3 * 86400) * 1000).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }), 1],
    ]);
  });

  it('glyphs and tones: an alert firing is a warning or an error, its resolution ok, an acknowledgement plain', () => {
    expect([eventGlyph({ type: 'alert', severity: 'err' }), eventTone({ type: 'alert', severity: 'err' })]).toEqual(['⚠', 'err']);
    expect([eventGlyph({ type: 'alert', severity: 'resolved' }), eventTone({ type: 'alert', severity: 'resolved' })]).toEqual(['✓', 'ok']);
    expect([eventGlyph({ type: 'alert', severity: null }), eventTone({ type: 'alert', severity: null })]).toEqual(['·', '']);
    expect(eventTone({ type: 'failover', severity: null })).toBe('warn');
    expect(eventGlyph({ type: 'controller', severity: null })).toBe('⌂');
  });
});

const alert = (o: Partial<FleetAlert> = {}): FleetAlert => ({ id: 'gpu-temp:r3', severity: 'err', node: 'r3', title: 'R-3 GPU 86 °C', since: 100, rule: 'GPU temperature ≥ 85 °C for 60 s', detail: '', action: null, acked: false, acked_at: null, ...o });

describe('alerts', () => {
  it('Acknowledge: acknowledged with the user and the time; the alert stays listed', () => {
    expect(ackInfo(alert(), [])).toBeNull();
    const a = alert({ acked: true, acked_at: 160 });
    expect(ackInfo(a, [ev('e1', 160, { type: 'alert', text: 'Acknowledged: R-3 GPU 86 °C', detail: 'by admin' })])).toEqual({ at: 160, who: 'admin' });
    expect(ackInfo(a, [])).toEqual({ at: 160, who: null });
    expect(alertRule(a)).toBe('gpu-temp');
  });

  it('the mock acknowledges as the controller: the alert keeps firing, acknowledged, and an event says who', () => {
    const now = Date.parse('2026-10-28T18:00:00') / 1000;
    const m = new MockFleet({ scenario: 'fleet4', now });
    m.handle('POST', '/v1/fleet/alerts/gpu-temp:r3/ack', new URLSearchParams(), null, (now + 3) * 1000);
    const a = m.alerts(now + 10).find((x) => x.id === 'gpu-temp:r3')!;
    expect(a).toMatchObject({ acked: true, acked_at: now + 3 });
    expect(ackInfo(a, m.events(now + 10, { types: ['alert'] }))).toEqual({ at: now + 3, who: 'admin' });
  });

  it('a system notification only for an alert that starts firing now, unacknowledged, of a rule with `system`', () => {
    const fire = alert();
    const sys = new Set(['gpu-temp']);
    expect(toNotify(null, [fire], sys)).toEqual([]);
    expect(toNotify(new Set(), [fire], sys)).toEqual([fire]);
    expect(toNotify(new Set(['gpu-temp:r3']), [fire], sys)).toEqual([]);
    expect(toNotify(new Set(), [alert({ acked: true })], sys)).toEqual([]);
    expect(toNotify(new Set(), [alert({ id: 'disk-free:r3' })], sys)).toEqual([]);
  });
});

const rule = (o: Partial<FleetRule> = {}): FleetRule => ({ id: 'gpu-temp', name: 'GPU temperature', metric: 'temp', condition: '≥ 85 °C for 60 s', scope: 'all', notify: ['app', 'system'], enabled: true, threshold: 85, unit: '°C', for_s: 60, severity: 'err', ...o });

describe('the rule editor', () => {
  it('an unchanged draft is no request; a change sends only what changed', () => {
    const r = rule();
    expect(rulePatch(r, draftOf(r))).toEqual({ patch: null });
    expect(rulePatch(r, { ...draftOf(r), threshold: '80', for_s: '30' })).toEqual({ patch: { threshold: 80, for_s: 30 } });
    expect(rulePatch(r, { ...draftOf(r), system: false })).toEqual({ patch: { notify: ['app'] } });
    expect(rulePatch(r, { ...draftOf(r), enabled: false, scope: 'node:r3' })).toEqual({ patch: { enabled: false, scope: 'node:r3' } });
    expect(rulePatch(r, { ...draftOf(r), threshold: '82,5' }).patch).toEqual({ threshold: 82.5 });
  });

  it('refuses what the controller refuses, before the request', () => {
    const r = rule();
    expect(rulePatch(r, { ...draftOf(r), threshold: 'hot' }).error).toMatch(/number/);
    expect(rulePatch(r, { ...draftOf(r), threshold: '' }).error).toMatch(/number/);
    expect(rulePatch(r, { ...draftOf(r), for_s: '-1' }).error).toMatch(/0 to 86400/);
    expect(rulePatch(r, { ...draftOf(r), for_s: '90000' }).error).toMatch(/0 to 86400/);
    expect(rulePatch(r, { ...draftOf(r), scope: 'rented' }).error).toMatch(/all Sparks or one/);
  });

  it('a rule without a threshold leaves it out; a host rule keeps its scope', () => {
    const down = rule({ id: 'node-down', metric: 'reachable', threshold: null, unit: null, for_s: 30 });
    expect(draftOf(down).threshold).toBe('');
    expect(rulePatch(down, { ...draftOf(down), threshold: '5', for_s: '45' })).toEqual({ patch: { for_s: 45 } });
    const host = rule({ id: 'host-sleep', metric: 'host_sleep', threshold: null, unit: null, for_s: 0, scope: 'host' });
    expect(rulePatch(host, { ...draftOf(host), scope: 'all' }).error).toMatch(/scope stays/);
  });

  it('the scope select: all and each Spark; a mock-only group stays listed as it reads', () => {
    expect(scopeOptions(rule(), nodes).map((o) => o.value)).toEqual(['all', 'node:desk', 'node:r1', 'node:r3']);
    expect(scopeOptions(rule({ scope: 'rented' }), nodes)[0]).toEqual({ value: 'rented', label: 'Rented' });
    expect([scopeLabel('host', nodes), scopeLabel('node:r3', nodes), scopeLabel('all', nodes)]).toEqual(['The controller’s machine', 'R-3', 'All Sparks']);
    expect([holdWords(300), holdWords(60), holdWords(45)]).toEqual(['5 min', '60 s', '45 s']);
  });
});

const spark = (o: Partial<FleetNode> = {}) => ({ id: 'n1', alias: 'dgx', colour: 1, inventory: { cx7: null }, ...o }) as unknown as FleetNode;

describe('Hardware: every metric, and what a board does not expose', () => {
  it('the matrix has a group for every metric the agent’s stream feeds, the RTT in the Host section', () => {
    const covered = new Set(HW_GROUPS.map((g) => g.metric));
    expect(METRIC_KEYS.filter((k) => !covered.has(k))).toEqual(['rtt']);
  });

  it('a GB10 without a memory clock, a power cap or a ConnectX cable: the words say why, never a zero', () => {
    // the live sample of a GX10 (2026-10-28): no mem_mhz, no power_cap_w, no fabric block, no fans
    const s: NodeSample = { gpus: [{ util: 0, sm_mhz: 2405, power_w: 9.01, temp_c: 38, throttled: false }], memory: { used: 90.3, unified: true }, cpu: { util: 2 }, disks: { devices: [{ name: 'nvme0n1' }] }, net: { lan: [{ iface: 'enP7s7' }] }, board: { zones: [{ zone: 'z0', temp_c: 39 }] } };
    expect([exposes('memclk', s), exposes('power', s), exposes('fabric', s), exposes('sm', s), exposes('tps', s)]).toEqual([false, true, false, true, null]);
    expect(absentWhy('memclk', spark(), s, null)).toBe(NOT_EXPOSED);
    expect(absentWhy('fabric', spark(), s, null)).toBe(`no ConnectX port: ${NOT_EXPOSED}`);
    expect(absentWhy('rtt', spark(), s, 'n1')).toMatch(/controller runs on this Spark/);
    // ports present but no cable, and a node without a sample yet
    expect(absentWhy('fabric', spark({ inventory: { cx7: 'not cabled' } as never }), { net: { fabric: [{ iface: 'enp1', state: 'down' }] } }, null)).toBe('ConnectX ports without a cable');
    expect(absentWhy('gpu', spark(), null, null)).toBe('no samples in this range yet');
    // an older controller without the sample route: a figure the Spark never gave while its others came
    expect(absentWhy('memclk', spark(), null, null, true)).toBe(NOT_EXPOSED);
    expect(absentWhy('ttft', spark(), null, null, true)).toBe('no samples in this range yet');
  });

  it('the mock’s samples: Desk has no fan and no cabled fabric, the rented have both, no board a power cap', () => {
    const now = Date.parse('2026-10-28T18:00:00') / 1000;
    const m = new MockFleet({ scenario: 'fleet4', now });
    const desk = m.sample('desk', now).sample!;
    const r1 = m.sample('r1', now).sample!;
    expect(desk.board?.fans).toBeUndefined();
    expect(r1.board?.fans?.[0].rpm).toBeGreaterThan(1000);
    expect([exposes('fabric', desk), exposes('fabric', r1)]).toEqual([false, true]);
    expect([desk, r1].every((x) => x.gpus!.every((g) => g.power_cap_w == null))).toBe(true);
    expect(desk.cpu?.cores).toHaveLength(20);
    expect(desk.cpu!.cores!.filter((c) => c.kind === 'performance')).toHaveLength(10);
  });
});
