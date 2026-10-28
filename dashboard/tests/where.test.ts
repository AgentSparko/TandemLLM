// VIS-255 (tests for VIS-254): where a row, a lane or an answer ran in the fleet, the ledger a page
// reads, and the mock's two ledgers and its gateway: a request the gateway relays is a row in both
// ledgers, so no figure is ever added up from both.
import { EventEmitter } from 'node:events';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { MockFleet, type FleetScenario } from '../mock/fleet.ts';
import { createMockMiddleware } from '../mock/server.ts';
import type { FleetDeployment, FleetNode } from '../src/api/fleet.ts';
import type { RequestRow, Usage } from '../src/api/types.ts';
import { groupByPlace, hereNode, inScope, isFleet, ledgerFor, rowWhere, scopeNodes, whereOf, type FleetView } from '../src/lib/where.ts';

const T0 = Date.parse('2026-10-27T16:40:00Z') / 1000;
const fleetOf = (scenario: FleetScenario): FleetView => {
  const m = new MockFleet({ scenario, now: T0 });
  const get = <T>(path: string) => m.handle('GET', path, new URLSearchParams(), null, T0 * 1000).body as T;
  return { nodes: get<{ nodes: FleetNode[] }>('/v1/fleet/nodes').nodes, deployments: get<{ deployments: FleetDeployment[] }>('/v1/fleet/deployments').deployments };
};

describe('where a request ran', () => {
  const f4 = fleetOf('fleet4');
  it('one Spark is today’s box; two or more are a fleet', () => {
    expect(isFleet(fleetOf('fleet1'))).toBe(false);
    expect(isFleet(null)).toBe(false);
    expect(isFleet(f4)).toBe(true);
    expect(isFleet(fleetOf('gold3'))).toBe(true);
  });
  it("attributes this server's own rows and lanes to the member reached through its own backend", () => {
    expect(hereNode(f4)?.alias).toBe('Desk');
    expect(hereNode(fleetOf('gold3'))?.alias).toBe('Gold');
    // the engine's own lane: two deployments on Desk's engine, told apart by the model
    expect(whereOf(f4, 'engine', 'qwen3.8-27b')).toEqual({ node: 'desk', deployment: 'qwen27' });
    expect(whereOf(f4, 'engine', 'wren')).toEqual({ node: 'desk', deployment: 'wren' });
    expect(whereOf(f4, null, 'Qwen3.8-27B')).toEqual({ node: 'desk', deployment: 'qwen27' });
    // the server's own name for its main model (the box's engine serves it as TandemLLM)
    expect(whereOf(f4, 'engine', 'TandemLLM')).toEqual({ node: 'desk', deployment: 'qwen27' });
    // a model the members do not name: the node is known, the deployment not
    expect(whereOf(f4, 'engine', 'gpt-oss-120b')).toEqual({ node: 'desk', deployment: null });
    // another Spark's engine through the gateway; a cloud API is on no Spark
    expect(whereOf(f4, 'r3-engine', 'qwen3.8-27b')).toEqual({ node: 'r3', deployment: 'qwen27' });
    expect(whereOf(f4, 'ds', 'deepseek-flash')).toEqual({ node: null, deployment: null });
    expect(whereOf(null, 'engine')).toEqual({ node: null, deployment: null });
  });
  it("a controller's row names its own place; an engine's row is attributed", () => {
    const row = (o: Partial<RequestRow>) => ({ model: 'qwen3.8-27b', backend: null, ...o });
    expect(rowWhere(f4, row({ node: 'r3', deployment: 'qwen27' }))).toEqual({ node: 'r3', deployment: 'qwen27' });
    expect(rowWhere(f4, row({}))).toEqual({ node: 'desk', deployment: 'qwen27' });
    expect(rowWhere(f4, row({ backend: { id: 'deepseek', kind: 'deepseek', label: null }, model: 'deepseek-chat' }))).toEqual({ node: null, deployment: null });
  });
  it('a scope covers its Sparks; a cloud request only All', () => {
    expect(scopeNodes(f4, { kind: 'all' }).map((n) => n.id)).toEqual(['desk', 'r1', 'r2', 'r3']);
    expect(scopeNodes(f4, { kind: 'node', id: 'r3' }).map((n) => n.id)).toEqual(['r3']);
    expect(scopeNodes(f4, { kind: 'deployment', id: 'qwen27' }).map((n) => n.id)).toEqual(['desk', 'r3']);
    expect(inScope({ node: 'r3', deployment: 'qwen27' }, { kind: 'deployment', id: 'qwen27' })).toBe(true);
    expect(inScope({ node: 'r3', deployment: 'qwen27' }, { kind: 'node', id: 'desk' })).toBe(false);
    expect(inScope({ node: null, deployment: null }, { kind: 'all' })).toBe(true);
    expect(inScope({ node: null, deployment: null }, { kind: 'node', id: 'desk' })).toBe(false);
  });
  it('groups by deployment, then node; what ran on no Spark last', () => {
    const items = [
      { b: 'ds', m: 'deepseek-flash' },
      { b: 'r3-engine', m: 'qwen3.8-27b' },
      { b: 'engine', m: 'wren' },
      { b: 'engine', m: 'qwen3.8-27b' },
      { b: 'r3-engine', m: 'qwen3.8-27b' },
    ];
    const g = groupByPlace(items, (x) => whereOf(f4, x.b, x.m), f4);
    expect(g.map((x) => [x.deployment?.name ?? null, x.node?.alias ?? null, x.items.length])).toEqual([
      ['Qwen3.8-27B', 'Desk', 1],
      ['Qwen3.8-27B', 'R-3', 2],
      ['Wren', 'Desk', 1],
      [null, null, 1],
    ]);
  });
  it("reads this server's ledger by default, the controller's for a Spark or a deployment in scope", () => {
    expect(ledgerFor(null, { kind: 'all' }, null)).toBe('here');
    expect(ledgerFor(fleetOf('fleet1'), { kind: 'all' }, null)).toBe('here');
    expect(ledgerFor(fleetOf('fleet1'), { kind: 'all' }, 'fleet')).toBe('fleet');
    expect(ledgerFor(f4, { kind: 'all' }, null)).toBe('here');
    expect(ledgerFor(f4, { kind: 'all' }, 'fleet')).toBe('fleet');
    expect(ledgerFor(f4, { kind: 'node' }, 'here')).toBe('fleet');
    expect(ledgerFor(f4, { kind: 'deployment' }, null)).toBe('fleet');
    expect(ledgerFor(f4, { kind: 'all' }, 'here', true)).toBe('fleet');
  });
});

describe('the API has no single engine of its own (VIS-254 acceptance)', () => {
  const dir = join(__dirname, '..', 'src', 'api');
  const src = readdirSync(dir).map((f) => readFileSync(join(dir, f), 'utf8')).join('\n');
  it('no ENGINE constant, no SystemInfo or Live: the answers are named for the server that gives them', () => {
    expect(src).not.toMatch(/export const ENGINE\b/);
    expect(src).not.toMatch(/\binterface (SystemInfo|Live)\b/);
    expect(src).toMatch(/export interface ServerSystem\b/);
    expect(src).toMatch(/export interface ServerLive\b/);
    expect(src).toMatch(/export interface OwnEngine\b/);
    expect(readFileSync(join(__dirname, '..', 'src', 'lib', 'relayed.ts'), 'utf8')).not.toMatch(/\bENGINE\b/);
  });
  it('rows, usage and filters carry the node and the deployment; a read names its ledger', () => {
    expect(src).toMatch(/export interface Where \{\s+node: string \| null;\s+deployment: string \| null;/);
    expect(src).toMatch(/export type LedgerSource = 'here' \| 'fleet'/);
    for (const k of ['node?: string | null;', 'deployment?: string | null;']) expect(src).toContain(k);
    expect(src).toMatch(/nodes\?: string\[\]; deployments\?: string\[\]/);
    expect(src).toContain("const LEDGER: Record<LedgerSource, string> = { here: '/v1/dashboard', fleet: '/v1/fleet/ledger' }");
  });
});

describe("the mock's two ledgers and its gateway", () => {
  const mw = createMockMiddleware({ live: false });
  function call(method: string, url: string, cookie = 'qse_fleet=gold3', body?: unknown, headers: Record<string, string> = {}): Promise<{ status: number; headers: Record<string, string | string[]>; body: unknown }> {
    return new Promise((resolve) => {
      const req = Object.assign(new EventEmitter(), { method, url, headers: { cookie, ...headers } as Record<string, string>, socket: { destroy() {} } });
      const out: Record<string, string | string[]> = {};
      let status = 200;
      const res = {
        headersSent: false,
        setHeader: (k: string, v: string) => (out[k.toLowerCase()] = v),
        appendHeader: (k: string, v: string) => (out[k.toLowerCase()] = v),
        writeHead: (s: number, h: Record<string, string> = {}) => {
          status = s;
          for (const [k, v] of Object.entries(h)) out[k.toLowerCase()] = v;
        },
        write: () => true,
        end: (s?: string) => resolve({ status, headers: out, body: s ? JSON.parse(s) : null }),
      };
      mw(req as never, res as never, () => resolve({ status, headers: out, body: null }));
      queueMicrotask(() => {
        if (body !== undefined) req.emit('data', JSON.stringify(body));
        req.emit('end');
      });
    });
  }
  const usage = async (qs: string) => (await call('GET', `/v1/fleet/ledger/usage?bucket=day&tz=UTC&${qs}`)).body as Usage;

  it('Gherkin "Usage by node": a month of requests on 3 Sparks, each row on one of them or on none', async () => {
    const all = await usage('');
    expect(all.dimensions.nodes?.sort()).toEqual(['bronze', 'gold', 'r3']);
    expect(all.dimensions.deployments?.sort()).toEqual(['qwen27', 'wren']);
    const by = await Promise.all(['gold', 'r3', 'bronze'].map((n) => usage(`node=${n}`)));
    for (const [i, n] of ['gold', 'r3', 'bronze'].entries()) {
      expect(by[i].filters.node).toBe(n);
      expect(by[i].totals.requests).toBeGreaterThan(0);
    }
    // the nodes and the cloud backend add up to the whole, each row counted once
    const nodes = by.reduce((a, u) => a + u.totals.total_tokens, 0);
    expect(nodes).toBeLessThan(all.totals.total_tokens);
    expect(all.buckets.filter((b) => b.requests > 0).length).toBeGreaterThanOrEqual(28);
    expect((await usage('deployment=wren')).totals.requests).toBe(by[2].totals.requests);
  });
  it("the controller's ledger begins with its fleet; this server's keeps its year", async () => {
    const mine = (await call('GET', '/v1/dashboard/usage?bucket=day&tz=UTC')).body as Usage;
    const fleet = await usage('');
    expect(mine.buckets.filter((b) => b.requests > 0).length).toBeGreaterThan(fleet.buckets.filter((b) => b.requests > 0).length);
    expect(mine.dimensions.nodes).toBeUndefined();
    const rows = ((await call('GET', '/v1/fleet/ledger/requests?limit=50')).body as { requests: RequestRow[] }).requests;
    expect(rows.every((r) => r.node === null || ['gold', 'r3', 'bronze'].includes(r.node!))).toBe(true);
    expect(rows.some((r) => r.node === null && r.backend?.id === 'deepseek')).toBe(true);
    const sum = (await call('GET', '/v1/fleet/ledger/summary?tz=UTC')).body as { ledger: { since: string } };
    expect(Date.parse(sum.ledger.since)).toBeGreaterThan(Date.now() - 32 * 86400_000);
  });
  it('Gherkin "Compare in Playground": the gateway answers through the pinned member and names it', async () => {
    const page = { 'content-type': 'application/json' };
    const send = (pin?: string, model = 'qwen3.8-27b') => call('POST', '/v1/fleet/gateway/chat/completions', 'qse_fleet=gold3', { model, messages: [{ role: 'user', content: 'hi' }] }, { ...page, ...(pin ? { 'x-tandem-pin': pin } : {}) });
    for (const [pin, alias] of [['gold', 'Gold'], ['R-3', 'R-3']]) {
      const r = await send(pin);
      expect([r.status, r.headers['x-tandem-node'], r.headers['x-tandem-deployment']]).toEqual([200, alias, 'qwen27']);
    }
    const stranger = await send('bronze');
    expect([stranger.status, (stranger.body as { error: { code: string } }).error.code]).toEqual([409, 'not_a_member']);
    expect((await send(undefined, 'nope')).status).toBe(404);
    const split = await call('POST', '/v1/fleet/gateway/chat/completions', 'qse_fleet=fleet4', { model: 'deepseek-v4.1-flash', messages: [] }, page);
    expect([split.status, (split.body as { error: { code: string } }).error.code]).toEqual([501, 'not_built']);
  });
});
