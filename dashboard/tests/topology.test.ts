// VIS-249 (tests for VIS-248): the topology graph's layout matches /v1/fleet/topology for fleet1,
// fleet4, fleet2-split and node-down, and the real fleet of one (the controller on its own Spark, the
// LAN, no ConnectX): a box for every Spark and every piece of infrastructure, an edge for every link
// whose ends are drawn, a hull or an arc over every Split and Replicas deployment, nothing outside
// the drawing, and the controller on the right box.
import { describe, expect, it } from 'vitest';
import { MockFleet, type FleetScenario } from '../mock/fleet.ts';
import type { FleetTopology } from '../src/api/fleet.ts';
import { controllerNode, hwShort, linkLabel, topoLayout } from '../src/lib/topology.ts';

const T0 = Date.parse('2026-10-28T12:00:00Z') / 1000;
const topo = (s: FleetScenario, at = T0) => new MockFleet({ scenario: s, now: T0 }).topology(at);

function inside(l: ReturnType<typeof topoLayout>) {
  for (const b of l.boxes) {
    expect(b.x - b.w / 2, b.id).toBeGreaterThanOrEqual(0);
    expect(b.x + b.w / 2, b.id).toBeLessThanOrEqual(l.w);
    expect(b.y - b.h / 2, b.id).toBeGreaterThanOrEqual(0);
    expect(b.y + b.h / 2, b.id).toBeLessThanOrEqual(l.h);
  }
  for (const h of l.hulls) expect(h.ty, h.deployment).toBeGreaterThan(0);
}

describe('topology layout', () => {
  for (const s of ['fleet1', 'fleet4', 'fleet2-split', 'node-down'] as const) {
    it(`${s}: every Spark, infra item, link and group of /v1/fleet/topology is drawn, inside the drawing`, () => {
      const t = topo(s, s === 'node-down' ? T0 + 70 : T0);
      const l = topoLayout(t);
      const ids = new Set(l.boxes.map((b) => b.id));
      for (const n of t.nodes) expect(ids.has(n.id), n.id).toBe(true);
      for (const i of t.infra) expect(ids.has(i.id), i.id).toBe(true);
      const drawn = new Set(l.edges.filter((e) => !e.ghost).map((e) => e.id));
      for (const k of t.links) expect(drawn.has(k.id), k.id).toBe(true);
      const groups = t.groups.filter((g) => (g.type === 'split' || g.type === 'replicas') && g.members.length > 1);
      expect(l.hulls.map((h) => h.deployment).sort()).toEqual(groups.map((g) => g.deployment).sort());
      inside(l);
      // no two boxes overlap
      for (const a of l.boxes)
        for (const b of l.boxes) {
          if (a === b) continue;
          const apart = Math.abs(a.x - b.x) >= (a.w + b.w) / 2 || Math.abs(a.y - b.y) >= (a.h + b.h) / 2;
          expect(apart, `${a.id} / ${b.id}`).toBe(true);
        }
    });
  }

  it('fleet4: the Mac hosts the controller; Desk on the LAN, the rented right of the gateway, R-3 level with Desk; the Split boxed, the Replicas an arc', () => {
    const t = topo('fleet4');
    const l = topoLayout(t);
    const b = (id: string) => l.boxes.find((x) => x.id === id)!;
    expect(controllerNode(t)).toBeNull();
    expect(b('host').x).toBeLessThan(b('lan').x);
    expect(b('lan').x).toBeLessThan(b('desk').x);
    expect(b('gw').x).toBe(b('desk').x);
    for (const r of ['r1', 'r2', 'r3']) expect(b(r).x).toBeGreaterThan(b('gw').x);
    expect(b('sw').x).toBeGreaterThan(b('r1').x);
    expect(b('r3').y).toBe(b('desk').y);
    expect(l.hulls.find((h) => h.deployment === 'dsv41')!.rect).toBeDefined();
    expect(l.hulls.find((h) => h.deployment === 'qwen27')!.arc).toBeDefined();
    expect(l.edges.filter((e) => e.kind === 'fabric').map((e) => e.label)).toEqual(['200G · 196 Gb/s', '200G · 196 Gb/s', '200G · 194 Gb/s']);
  });

  it('a fleet of one: the controller on the box, beside the LAN, with the internet it would reach rented Sparks through', () => {
    const t = topo('fleet1');
    const l = topoLayout(t);
    expect(controllerNode(t)).toBe('desk');
    expect(l.boxes.find((x) => x.id === 'desk')!.controller).toBe(true);
    expect(l.boxes.map((x) => `${x.kind}${x.ghost ? ':ghost' : ''}`).sort()).toEqual(['internet:ghost', 'lan', 'node']);
    expect(l.edges.map((e) => `${e.id}${e.ghost ? ':ghost' : ''}`)).toEqual(['lan-desk', 'lan-inet:ghost']);
    expect(l.hulls).toEqual([]);
  });

  it('the real fleet of one (a GX10, its controller on it, the LAN at 1 GbE, no ConnectX)', () => {
    const t: FleetTopology = {
      contract_version: '1.0',
      host: { name: 'spark1', address: '192.168.1.131', api_url: 'http://192.168.1.131:8091', may_sleep: false },
      infra: [{ id: 'lan', kind: 'lan', label: 'LAN' }],
      nodes: [{ ...topo('fleet1').nodes[0], id: 'n-0a1b2c3d4e5f', alias: 'spark1', address: '192.168.1.131', hw: 'NVIDIA DGX Spark', dmi: 'ASUSTeK COMPUTER INC. GX10' }],
      links: [{ id: 'lan-n-0a1b2c3d4e5f', a: 'lan', b: 'n-0a1b2c3d4e5f', kind: 'lan', speed: '1 GbE', rtt_ms: null, mtu: 1500, state: 'up', note: 'enP7s7; the controller runs on this node' }],
      groups: [{ deployment: 'TandemLLM', type: 'single', members: ['n-0a1b2c3d4e5f'] }],
    };
    const l = topoLayout(t);
    expect(controllerNode(t)).toBe('n-0a1b2c3d4e5f');
    expect(l.edges.find((e) => e.id === 'lan-n-0a1b2c3d4e5f')!.label).toBe('1 GbE');
    expect(hwShort(t.nodes[0])).toBe('ASUS GX10');
    inside(l);
  });

  it('fleet2-split: a direct cable between two Sparks in one column is an arc to the right, the Split a hull', () => {
    const l = topoLayout(topo('fleet2-split'));
    const cable = l.edges.find((e) => e.id === 's1-s2')!;
    expect(cable.d).toMatch(/^M\d+(\.\d+)? \d+(\.\d+)? C /);
    expect(cable.label).toBe('200G · 197 Gb/s');
    expect(l.hulls[0].rect).toBeDefined();
  });

  it('node-down: R-3 off is still drawn, as off; its links stay', () => {
    const t = topo('node-down', T0 + 70);
    const r3 = topoLayout(t).boxes.find((b) => b.id === 'r3')!;
    expect(r3.node!.status).toBe('off');
  });

  it('link labels: speed, then the round trip when it was timed', () => {
    const base = { id: 'x', a: 'a', b: 'b', mtu: 1500, state: 'up' as const, note: null };
    expect(linkLabel({ ...base, kind: 'lan', speed: '10 GbE', rtt_ms: 0.4 })).toBe('10 GbE · 0.4 ms');
    expect(linkLabel({ ...base, kind: 'wan', speed: 'tunnel :47011', rtt_ms: 37.2 })).toBe('tunnel :47011 · 37 ms');
    expect(linkLabel({ ...base, kind: 'lan', speed: '1 GbE', rtt_ms: null })).toBe('1 GbE');
  });
});
