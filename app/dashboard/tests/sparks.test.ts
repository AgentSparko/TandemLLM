// VIS-251 (tests for VIS-250): the Sparks pages' rules, without a page. The list's rows from the mock
// fleet, each column's sort with missing values last, the text, status and owned/rented filters; a
// node's link and lease in words; the colours a node may take and the edit's patch and refusals; the
// actions a node offers and the confirm a release asks for; a job and a drain in words; the drained
// status word; and the chart's event markers clustered with their count.
import { describe, expect, it } from 'vitest';
import type { FleetNode } from '../src/api/fleet.ts';
import { clusterEvents } from '../src/charts/fleet-chart.ts';
import { colourChoices, confirmButton, drainLine, editPatch, filterRows, jobWords, leaseWords, linkWords, nodeActions, parseTags, sortRows, sparkRows, type SparkRow } from '../src/lib/sparks.ts';
import { statusWords } from '../src/ui/node-chip.ts';
import { MockFleet } from '../mock/fleet.ts';

const T0 = Date.parse('2026-10-27T16:40:00Z');
const q = (s = '') => new URLSearchParams(s);

function fleet4(at = T0) {
  const m = new MockFleet({ seed: 42, scenario: 'fleet4', now: T0 / 1000 });
  const get = <T>(path: string, qs = '') => m.handle('GET', path, q(qs), null, at).body as T;
  const nodes = get<{ nodes: FleetNode[] }>('/v1/fleet/nodes').nodes;
  const deployments = get<{ deployments: import('../src/api/fleet.ts').FleetDeployment[] }>('/v1/fleet/deployments').deployments;
  const links = get<{ links: import('../src/api/fleet.ts').FleetLink[] }>('/v1/fleet/links').links;
  const now = (k: string) => Object.fromEntries(get<{ series: { node: string; values: (number | null)[] }[] }>('/v1/fleet/metrics', `source=all&metric=${k}&range=5m`).series.map((s) => [s.node, s.values.at(-1) ?? null]));
  const rows = sparkRows({ nodes, deployments, links, now: { mem: now('mem'), temp: now('temp'), power: now('power') }, cap: Object.fromEntries(nodes.map((n) => [n.id, 121.6])) }, at / 1000);
  return { m, nodes, deployments, links, rows };
}
const ids = (rows: SparkRow[]) => rows.map((r) => r.node.id);

describe('the Sparks list', () => {
  it('a row per Spark with what it serves, its figures, link and lease', () => {
    const { rows } = fleet4();
    expect(ids(rows)).toEqual(['desk', 'r1', 'r2', 'r3']);
    const desk = rows[0];
    expect(desk.deps.map((d) => d.id)).toEqual(['qwen27', 'wren']);
    expect(desk.link).toBe('LAN · 0.4 ms');
    expect(desk.lease).toBe('owned');
    expect(desk.mem).toBeGreaterThan(0);
    expect(rows[1].link).toBe('tunnel · 37 ms');
    expect(rows[1].lease).toBe('ends in 2 d 4 h');
  });

  it('an off Spark has no figures now, never its last sample', () => {
    const { rows } = fleet4(T0 + 120_000);
    const m = new MockFleet({ seed: 42, scenario: 'node-down', now: T0 / 1000 });
    const nodes = (m.handle('GET', '/v1/fleet/nodes', q(), null, T0 + 120_000).body as { nodes: FleetNode[] }).nodes;
    const off = sparkRows({ nodes, deployments: [], links: [], now: { temp: { r3: 80 }, power: { r3: 150 }, mem: { r3: 90 } }, cap: {} }).find((r) => r.node.id === 'r3')!;
    expect(off.node.status).toBe('off');
    expect([off.temp, off.power, off.mem]).toEqual([null, null, null]);
    expect(rows.length).toBe(4);
  });

  it('sorts every column both ways, a missing value last either way', () => {
    const { rows } = fleet4();
    expect(ids(sortRows(rows, 'alias', 'desc'))).toEqual(['r3', 'r2', 'r1', 'desk']);
    // R-3 throttles: an alert sorts first
    expect(ids(sortRows(rows, 'status', 'asc'))[0]).toBe('r3');
    const byTemp = sortRows(rows, 'temp', 'desc');
    expect(byTemp.map((r) => r.temp)).toEqual([...rows.map((r) => r.temp)].sort((a, b) => (b ?? 0) - (a ?? 0)));
    expect(ids(sortRows(rows, 'lease', 'asc')).at(-1)).toBe('desk'); // owned: no lease end, last
    expect(ids(sortRows(rows, 'lease', 'desc')).at(-1)).toBe('desk');
    const gap = rows.map((r) => (r.node.id === 'r2' ? { ...r, power: null } : r));
    expect(ids(sortRows(gap, 'power', 'asc')).at(-1)).toBe('r2');
    expect(ids(sortRows(gap, 'power', 'desc')).at(-1)).toBe('r2');
    expect(ids(sortRows(rows, 'link', 'asc'))[0]).toBe('desk');
    const v = rows.map((r) => (r.node.id === 'r1' ? { ...r, node: { ...r.node, version: '0.15.10' } } : r.node.id === 'r2' ? { ...r, node: { ...r.node, version: '0.9.2' } } : r));
    expect(ids(sortRows(v, 'version', 'asc'))[0]).toBe('r2');
    expect(ids(sortRows(v, 'version', 'desc'))[0]).toBe('r1');
  });

  it('filters by text, status and owned or rented', () => {
    const { rows } = fleet4();
    const f = (q: string, status: Parameters<typeof filterRows>[1]['status'] = 'all', kind: Parameters<typeof filterRows>[1]['kind'] = 'all') => ids(filterRows(rows, { q, status, kind }));
    expect(f('rented')).toEqual(['r1', 'r2', 'r3']);
    expect(f('deepseek')).toEqual(['r1', 'r2']);
    expect(f('GX10')).toEqual(['desk']);
    expect(f('wren desk')).toEqual(['desk']);
    expect(f('', 'alert')).toEqual(['r3']);
    expect(f('', 'all', 'owned')).toEqual(['desk']);
    expect(f('qwen', 'all', 'rented')).toEqual(['r3']);
    expect(f('nothing like it')).toEqual([]);
    const drained = rows.map((r) => (r.node.id === 'r1' ? { ...r, node: { ...r.node, status: 'draining' as const, drain: { state: 'drained' as const, since: 1, by: null, inflight: 0 } } } : r));
    expect(ids(filterRows(drained, { q: '', status: 'draining', kind: 'all' }))).toEqual(['r1']);
    expect(ids(filterRows(drained, { q: '', status: 'drained', kind: 'all' }))).toEqual(['r1']);
  });

  it('a link and a lease in words', () => {
    const one = new MockFleet({ seed: 42, scenario: 'fleet1', now: T0 / 1000 });
    const links = (one.handle('GET', '/v1/fleet/links', q(), null, T0).body as { links: import('../src/api/fleet.ts').FleetLink[] }).links;
    expect(linkWords({ id: 'desk', transport: 'lan' }, links)).toEqual({ main: 'LAN · the controller runs here', fabric: 'no fabric link', rtt: null });
    const { links: l4 } = fleet4();
    expect(linkWords({ id: 'r2', transport: 'tunnel' }, l4).fabric).toBe('fabric 200G ✓');
    expect(linkWords({ id: 'x', transport: 'lan' }, l4).main).toBe('LAN · no link reported');
    expect(leaseWords({ lease_end: null, owned: true })).toBe('owned');
    expect(leaseWords({ lease_end: 1000, owned: false }, 2000)).toBe('ended');
    expect(leaseWords({ lease_end: 2000 + 2 * 3600 + 120, owned: false }, 2000)).toBe('ends in 2 h 2 min');
    expect(leaseWords({ lease_end: 2030, owned: false }, 2000)).toBe('ends in 1 min');
  });
});

describe('a Spark’s edit', () => {
  const { nodes } = fleet4();
  const r1 = nodes.find((n) => n.id === 'r1')!;

  it('offers the colours no other Spark has', () => {
    const c = colourChoices(nodes, 'r1');
    expect(c.map((x) => x.usedBy)).toEqual(['Desk', null, 'R-2', 'R-3', null, null, null, null]);
  });

  it('sends only what changed, and refuses what the controller refuses', () => {
    const d = { alias: '  Rental East ', colour: 6, icon: 'cloud' as const, tags: 'rented, east, rented' };
    expect(editPatch(r1, d, nodes)).toEqual({ patch: { alias: 'Rental East', colour: 6, tags: ['rented', 'east'] } });
    expect(editPatch(r1, { alias: 'R-1', colour: 2, icon: 'cloud', tags: 'rented' }, nodes)).toEqual({ patch: null });
    expect(editPatch(r1, { ...d, alias: 'desk' }, nodes).error).toBe('Another Spark is called desk.');
    expect(editPatch(r1, { ...d, colour: 1 }, nodes).error).toBe('Colour 1 is Desk’s.');
    expect(editPatch(r1, { ...d, alias: ' ' }, nodes).error).toBe('An alias is 1 to 40 characters.');
    expect(editPatch(r1, { ...d, tags: 'x'.repeat(33) }, nodes).error).toBe('A tag is at most 32 characters.');
    expect(parseTags(' a, b ,, a,c ')).toEqual(['a', 'b', 'c']);
    expect(parseTags(Array.from({ length: 20 }, (_, i) => `t${i}`).join(','))).toHaveLength(16);
  });
});

describe('a Spark’s actions', () => {
  it('drain or undrain, forget for an owned Spark, release for a rented one', () => {
    expect(nodeActions({ drain: null, lease_end: null }).map((a) => a.action)).toEqual(['drain', 'restart-engine', 'reboot', 'update', 'forget']);
    const acts = nodeActions({ drain: { state: 'drained', since: 1, by: null, inflight: 0 }, lease_end: 9 });
    expect(acts.map((a) => a.action)).toEqual(['undrain', 'restart-engine', 'reboot', 'update', 'release-lease']);
    expect(acts.filter((a) => a.danger).map((a) => a.action)).toEqual(['release-lease']);
    expect(confirmButton({ action: 'release-lease', title: '' })).toEqual({ label: 'Release and wipe', typeAlias: true });
    expect(confirmButton({ action: 'forget', title: '' })).toEqual({ label: 'Forget', typeAlias: false });
  });

  it('a job and a drain in words; a drained Spark is said so', () => {
    const c = (t: number) => `t${t}`;
    expect(jobWords(null, c)).toBeNull();
    expect(jobWords({ action: 'reboot', state: 'running', started_at: 5, simulated: true }, c)).toBe('reboot running (simulated) since t5');
    expect(jobWords({ action: 'restart', state: 'done', message: 'up after 41 s' }, c)).toBe('restart done: up after 41 s');
    const n = { alias: 'Gold', status: 'draining' as const };
    expect(drainLine({ ...n, drain: null })).toBeNull();
    expect(drainLine({ ...n, drain: { state: 'draining', since: 1, by: 'admin', inflight: 3 } })!.title).toBe('Draining: 3 requests in flight');
    expect(drainLine({ ...n, drain: { state: 'drained', since: 1, by: 'admin', inflight: 0 } })!.title).toBe('Drained: nothing in flight');
    expect(drainLine({ ...n, status: 'off', drain: { state: 'drained', since: 1, by: null, inflight: 0 } })!.title).toBe('Gold is drained and off');
    expect(statusWords({ status: 'draining', status_text: 'draining · 3 in flight', drain: { state: 'draining', since: 1, by: null, inflight: 3 } })).toBe('draining');
    expect(statusWords({ status: 'draining', status_text: 'drained', drain: { state: 'drained', since: 1, by: null, inflight: 0 } })).toBe('drained');
  });
});

describe('event markers on a chart', () => {
  it('a burst of events is one marker with its count, the rest stay apart', () => {
    const evs = [0, 2, 3, 4, 5, 6, 7, 600, 900].map((dt, i) => ({ at: 1000 + dt, text: `e${i}`, alert: i === 3 }));
    // 3600 s over 600 px: 6 s a pixel, so the first seven fall within 10 px of the first
    const m = clusterEvents(evs, 1000, 3600, 0, 600);
    expect(m.map((x) => x.texts.length)).toEqual([7, 1, 1]);
    expect(m[0]).toMatchObject({ x: 0, alert: true });
    expect(m[1].x).toBeCloseTo(100);
    // a long, even run is cut every 10 px, never one marker for all
    const run = Array.from({ length: 20 }, (_, i) => ({ at: 1000 + i * 54, text: `r${i}` }));
    expect(clusterEvents(run, 1000, 3600, 0, 600).length).toBe(10);
    expect(clusterEvents([], 1000, 3600, 0, 600)).toEqual([]);
  });
});
