// The mock fleet (VIS-244): seeded and deterministic, moving with the clock, inside what a GB10 shows,
// and each scenario plays what it says; the mock server picks the scenario from ?fleet= / the cookie /
// POST /__mock/fleet.
import { EventEmitter } from 'node:events';
import { describe, expect, it } from 'vitest';
import { FLEET_SCENARIOS, MockFleet, USABLE_GIB, window } from '../mock/fleet.ts';
import { createMockMiddleware } from '../mock/server.ts';
import { METRIC_KEYS, RANGES, RANGE_KEYS, type FleetAlert, type FleetDeployment, type FleetNode, type MetricKey, type MetricSeries } from '../src/api/fleet.ts';

const T0 = Date.parse('2026-10-27T16:40:00Z') / 1000;
const get = <T>(m: MockFleet, path: string, qs = '', at = T0) => m.handle('GET', path, new URLSearchParams(qs), null, at * 1000).body as T;
const series = (m: MockFleet, metric: MetricKey, range = '1h', source = 'all', at = T0) => get<MetricSeries>(m, '/v1/fleet/metrics', `source=${source}&metric=${metric}&range=${range}`, at);

/** what a GB10 can show, per node */
const PLAUSIBLE: Record<MetricKey, [number, number]> = {
  tps: [0, 260],
  req: [0, 12],
  ttft: [100, 2000],
  gpu: [1, 99],
  power: [20, 180],
  temp: [40, 87],
  sm: [1400, 2418],
  memclk: [3200, 4266],
  mem: [0, USABLE_GIB],
  cpu: [1, 100],
  disk: [0, 1000],
  lan: [0, 8],
  fabric: [0, 12],
  rtt: [0, 45],
};

describe('mock fleet: seeded and live', () => {
  it('the same seed gives the same answer, another seed another one', () => {
    const a = new MockFleet({ seed: 42, scenario: 'fleet4', now: T0 });
    const b = new MockFleet({ seed: 42, scenario: 'fleet4', now: T0 });
    const c = new MockFleet({ seed: 7, scenario: 'fleet4', now: T0 });
    for (const metric of METRIC_KEYS) expect(JSON.stringify(series(a, metric))).toBe(JSON.stringify(series(b, metric)));
    expect(JSON.stringify(get(a, '/v1/fleet/topology'))).toBe(JSON.stringify(get(b, '/v1/fleet/topology')));
    expect(JSON.stringify(series(a, 'tps'))).not.toBe(JSON.stringify(series(c, 'tps')));
  });

  it('values move with the clock: the 5-minute window slides and the newest samples change', () => {
    const m = new MockFleet({ seed: 42, scenario: 'fleet4', now: T0 });
    const s0 = series(m, 'tps', '5m');
    const s1 = series(m, 'tps', '5m', 'all', T0 + 10);
    expect(s1.t0 - s0.t0).toBe(10);
    expect(s1.series[0].values.at(-1)).not.toBe(s0.series[0].values.at(-1));
    // the overlap is the same instants, so the same values
    expect(s1.series[0].values.slice(0, -5)).toEqual(s0.series[0].values.slice(5));
  });

  it('a window has the range count, its last sample at or before now, on a 30 s grid or the step', () => {
    for (const r of RANGE_KEYS) {
      const w = window(r, T0 + 17);
      const end = w.t0 + (w.count - 1) * w.step;
      expect(w.count).toBe(RANGES[r].count);
      expect(end).toBeLessThanOrEqual(T0 + 17);
      expect(T0 + 17 - end).toBeLessThan(Math.min(w.step, 30));
    }
  });

  for (const scenario of FLEET_SCENARIOS) {
    it(`${scenario}: every metric of every node stays in a GB10's range over 7 days and the last 5 minutes`, () => {
      const m = new MockFleet({ seed: 42, scenario, now: T0 });
      for (const at of [T0, T0 + 120]) {
        for (const metric of METRIC_KEYS) {
          for (const range of ['7d', '5m']) {
            for (const s of series(m, metric, range, 'all', at).series) {
              const vals = s.values.filter((v): v is number => v != null);
              // Desk has no fabric cable: no samples, never zeros
              if (metric !== 'fabric' || s.node !== 'desk') expect(vals.length, `${scenario} ${metric} ${range} ${s.node}`).toBeGreaterThan(0);
              for (const v of vals) {
                expect(v, `${scenario} ${metric} ${s.node}`).toBeGreaterThanOrEqual(PLAUSIBLE[metric][0]);
                expect(v, `${scenario} ${metric} ${s.node}`).toBeLessThanOrEqual(PLAUSIBLE[metric][1]);
              }
            }
          }
        }
      }
    });
  }
});

describe('mock fleet: the scenarios', () => {
  it('fleet1 is today: one Spark, the controller on it, the main model and Wren, no links, no alerts', () => {
    const m = new MockFleet({ scenario: 'fleet1', now: T0 });
    const nodes = get<{ nodes: FleetNode[] }>(m, '/v1/fleet/nodes').nodes;
    expect(nodes.map((n) => [n.alias, n.status, n.transport])).toEqual([['Desk', 'up', 'lan']]);
    expect(get<{ host: { name: string } }>(m, '/v1/fleet/topology').host.name).toBe('Desk');
    expect(get<{ links: unknown[] }>(m, '/v1/fleet/links').links).toEqual([]);
    expect(get<{ alerts: unknown[] }>(m, '/v1/fleet/alerts').alerts).toEqual([]);
    expect(get<{ deployments: FleetDeployment[] }>(m, '/v1/fleet/deployments').deployments.map((d) => d.type)).toEqual(['single', 'role']);
  });

  it('fleet4: Desk on the LAN, three rented over the tunnel, a Replicas, a Split over the switch, a Role, R-3 throttling', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const nodes = get<{ nodes: FleetNode[] }>(m, '/v1/fleet/nodes').nodes;
    expect(nodes.map((n) => `${n.alias}:${n.transport}:${n.status}:${n.colour}`)).toEqual(['Desk:lan:up:1', 'R-1:tunnel:up:2', 'R-2:tunnel:up:3', 'R-3:tunnel:alert:4']);
    // documentation-range addresses only
    for (const n of nodes) expect(n.address).toMatch(/^(192\.0\.2|198\.51\.100|203\.0\.113)\./);
    expect(nodes.filter((n) => !n.owned).every((n) => n.lease_end! > T0)).toBe(true);
    const deps = get<{ deployments: FleetDeployment[] }>(m, '/v1/fleet/deployments').deployments;
    expect(deps.map((d) => `${d.id}:${d.type}:${d.members.join('+')}:${d.status}`)).toEqual(['qwen27:replicas:desk+r3:serving', 'dsv41:split:r1+r2:serving', 'wren:role:desk:serving']);
    const links = get<{ links: { kind: string; a: string; b: string }[] }>(m, '/v1/fleet/links').links;
    expect(links.filter((l) => l.kind === 'fabric').map((l) => `${l.a}-${l.b}`)).toEqual(['r1-sw', 'r2-sw', 'r3-sw']);
    const alerts = get<{ alerts: FleetAlert[] }>(m, '/v1/fleet/alerts').alerts;
    expect(alerts.map((a) => a.id)).toEqual(['a-therm', 'a-lease', 'a-sleep']);
    expect(series(m, 'temp', '5m', 'node:r3').series[0].values.at(-1)).toBeGreaterThan(84);
  });

  it('a Split counts its tokens half per stage, so the members add up; a deployment source narrows tok/s', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const s = series(m, 'tps', '1h', 'deployment:dsv41').series;
    expect(s.map((x) => x.node)).toEqual(['r1', 'r2']);
    s[0].values.forEach((v, i) => expect(v).toBeCloseTo(s[1].values[i]!, 2));
    // Desk's Replicas member without Wren's tokens
    const desk = series(m, 'tps', '1h', 'node:desk').series[0].values;
    const deskQwen = series(m, 'tps', '1h', 'deployment:qwen27').series[0].values;
    deskQwen.forEach((v, i) => expect(v!).toBeLessThan(desk[i]!));
    // the hardware metrics of a deployment are its members' whole boxes
    expect(series(m, 'power', '1h', 'deployment:qwen27').series.map((x) => x.values)).toEqual([series(m, 'power', '1h', 'node:desk').series[0].values, series(m, 'power', '1h', 'node:r3').series[0].values]);
  });

  it('nulls where there is no sample: before a node was paired, and a node without a fabric port', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const week = series(m, 'tps', '7d').series;
    const r3 = week.find((s) => s.node === 'r3')!.values;
    expect(r3[0]).toBeNull();
    expect(r3.at(-1)).not.toBeNull();
    expect(series(m, 'fabric', '1h', 'node:desk').series[0].values.every((v) => v == null)).toBe(true);
    expect(series(m, 'mem').cap).toBe(USABLE_GIB);
    expect(series(m, 'tps').cap).toBeNull();
  });

  it('node-down: R-3 answers for 30 s, then goes off; the Replicas runs degraded and an alert fires 30 s later', () => {
    const m = new MockFleet({ scenario: 'node-down', now: T0 });
    const r3 = (at: number) => get<{ nodes: FleetNode[] }>(m, '/v1/fleet/nodes', '', at).nodes.find((n) => n.id === 'r3')!;
    expect(r3(T0 + 29).status).toBe('alert');
    expect(r3(T0 + 31)).toMatchObject({ status: 'off', status_text: 'not reachable' });
    expect(series(m, 'tps', '5m', 'node:r3', T0 + 40).series[0].values.at(-1)).toBeNull();
    expect(get<{ deployments: FleetDeployment[] }>(m, '/v1/fleet/deployments', '', T0 + 40).deployments[0]).toMatchObject({ status: 'degraded', status_text: 'degraded · 1 of 2 members' });
    expect(get<{ alerts: FleetAlert[] }>(m, '/v1/fleet/alerts', '', T0 + 45).alerts.map((a) => a.id)).toEqual(['a-lease', 'a-sleep']);
    expect(get<{ alerts: FleetAlert[] }>(m, '/v1/fleet/alerts', '', T0 + 61).alerts.map((a) => a.id)).toEqual(['a-down', 'a-lease', 'a-sleep']);
    expect(get<{ nodes: unknown[] }>(m, '/v1/fleet/memory', '', T0 + 40).nodes).toHaveLength(3);
  });

  it('alert-thermal: R-3 is fine at the start, heats past 84 °C, throttles, and the alert fires a minute in', () => {
    const m = new MockFleet({ scenario: 'alert-thermal', now: T0 });
    const ids = (at: number) => get<{ alerts: FleetAlert[] }>(m, '/v1/fleet/alerts', '', at).alerts.map((a) => a.id);
    expect(ids(T0 - 60)).not.toContain('a-therm');
    expect(ids(T0 + 59)).not.toContain('a-therm');
    expect(ids(T0 + 61)).toContain('a-therm');
    const temp = (at: number) => series(m, 'temp', '5m', 'node:r3', at).series[0].values.at(-1)!;
    expect(temp(T0 - 600)).toBeLessThan(84);
    expect(temp(T0 + 60)).toBeGreaterThan(83);
    expect(get<{ events: { id: string }[] }>(m, '/v1/fleet/events', `since=${T0}`, T0 + 61).events.map((e) => e.id)).toEqual(['e-therm']);
    const st = (at: number) => get<{ nodes: FleetNode[] }>(m, '/v1/fleet/nodes', '', at).nodes.find((n) => n.id === 'r3')!.status;
    expect([st(T0 - 1), st(T0 + 1)]).toEqual(['up', 'alert']);
  });

  it('fleet2-split: two Sparks on the LAN, one direct cable, the controller on Spark 1', () => {
    const m = new MockFleet({ scenario: 'fleet2-split', now: T0 });
    const t = get<{ host: { name: string }; nodes: FleetNode[]; links: { kind: string; a: string; b: string }[]; groups: { type: string; members: string[] }[] }>(m, '/v1/fleet/topology');
    expect(t.host.name).toBe('Spark 1');
    expect(t.nodes.map((n) => n.alias)).toEqual(['Spark 1', 'Spark 2']);
    expect(t.links.filter((l) => l.kind === 'fabric').map((l) => `${l.a}-${l.b}`)).toEqual(['s1-s2']);
    expect(t.groups).toEqual([{ deployment: 'dsv41', type: 'split', members: ['s1', 's2'] }]);
  });

  it('events come newest first; since keeps the later ones', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const all = get<{ events: { at: number }[] }>(m, '/v1/fleet/events').events;
    for (let i = 1; i < all.length; i++) expect(all[i].at).toBeLessThanOrEqual(all[i - 1].at);
    const recent = get<{ events: { at: number }[] }>(m, '/v1/fleet/events', `since=${T0 - 3600}`).events;
    expect(recent.length).toBeGreaterThan(0);
    expect(recent.every((e) => e.at > T0 - 3600)).toBe(true);
    expect(recent.length).toBeLessThan(all.length);
  });
});

describe('mock server: choosing the fleet', () => {
  const mw = createMockMiddleware({ live: false });
  function call(method: string, url: string, cookie = '', body?: unknown): Promise<{ status: number; headers: Record<string, string | string[]>; body: unknown; passed: boolean }> {
    return new Promise((resolve) => {
      const req = Object.assign(new EventEmitter(), { method, url, headers: { cookie } as Record<string, string>, socket: { destroy() {} } });
      const headers: Record<string, string | string[]> = {};
      let status = 200;
      const res = {
        headersSent: false,
        setHeader: (k: string, v: string) => (headers[k.toLowerCase()] = v),
        appendHeader: (k: string, v: string) => (headers[k.toLowerCase()] = [...((headers[k.toLowerCase()] as string[]) ?? []), v]),
        writeHead: (s: number, h: Record<string, string> = {}) => {
          status = s;
          Object.assign(headers, h);
        },
        end: (s?: string) => resolve({ status, headers, body: s ? JSON.parse(s) : null, passed: false }),
      };
      mw(req as never, res as never, () => resolve({ status, headers, body: null, passed: true }));
      queueMicrotask(() => {
        if (body !== undefined) req.emit('data', JSON.stringify(body));
        req.emit('end');
      });
    });
  }
  const aliases = (r: { body: unknown }) => (r.body as { nodes: FleetNode[] }).nodes.map((n) => n.alias);

  it('fleet4 by default; ?fleet= on the page sets the cookie the routes read; POST /__mock/fleet sets the default', async () => {
    expect(aliases(await call('GET', '/v1/fleet/nodes'))).toEqual(['Desk', 'R-1', 'R-2', 'R-3']);
    const page = await call('GET', '/dashboard/?fleet=fleet1&mock=nologin');
    expect(page.passed).toBe(true);
    expect(page.headers['set-cookie']).toEqual(['qse_fleet=fleet1; Path=/; SameSite=Strict', 'qse_mock=nologin; Path=/; SameSite=Strict']);
    expect(aliases(await call('GET', '/v1/fleet/nodes', 'qse_fleet=fleet1'))).toEqual(['Desk']);
    expect((await call('GET', '/dashboard/?fleet=nope')).headers['set-cookie']).toBeUndefined();
    expect((await call('POST', '/__mock/fleet', '', { scenario: 'fleet2-split', reset: true })).body).toEqual({ scenario: 'fleet2-split', started: [] });
    expect(aliases(await call('GET', '/v1/fleet/nodes'))).toEqual(['Spark 1', 'Spark 2']);
    expect((await call('POST', '/__mock/fleet', '', { scenario: 'fleet9' })).status).toBe(400);
    const put = await call('PUT', '/v1/fleet/layouts/overview?device=desk', '', { page: 'overview' });
    expect(put.status).toBe(400);
    expect((await call('POST', '/__mock/mode', '', { mode: '401' })).status).toBe(200);
    expect((await call('GET', '/v1/fleet/nodes')).status).toBe(401);
    await call('POST', '/__mock/mode', '', { mode: 'ok' });
  });
});
