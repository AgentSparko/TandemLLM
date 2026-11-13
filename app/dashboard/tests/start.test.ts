// SRV-750 (tests for SRV-749): Get started's rules (lib/start.ts), where the first open lands, the
// fleet's members with a backend among them (the scope bar's choices, the words, the Spark-only
// view), and the mock that plays the controller: a test's reason, a verified add that saves nothing
// until it is confirmed, the backend member in a node's shape (valid against fleet-v1) with its
// deployments, its empty sample and the node-only routes' 409.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';
import { describe, expect, it } from 'vitest';
import { MockBackends } from '../mock/backends.ts';
import { MockFleet } from '../mock/fleet.ts';
import type { TestResult } from '../src/api/config';
import { isSpark, memberHref, membersWord, sparksOf, type FleetNode } from '../src/api/fleet';
import { landing } from '../src/lib/controller-host';
import { readout, sparksOnly, type FleetState } from '../src/lib/fleet-state';
import { MENU, crumb, sectionOf } from '../src/lib/menu';
import { parseHash } from '../src/lib/router';
import { DEFAULT_SCOPE, resolveScope, scopeChoices } from '../src/lib/scope';
import { PATHS, advancedOpen, canSave, formKey, testWords, urlError } from '../src/lib/start';

const FDIR = join(__dirname, '..', '..', 'contracts', 'fleet-v1');
const ajv = new Ajv2020({ allErrors: true, strict: true });
const schema = (name: string) => ajv.compile(JSON.parse(readFileSync(join(FDIR, `${name}.schema.json`), 'utf8')));
const NODES = schema('nodes');
const NODE = schema('node');
const SAMPLE = schema('sample');
const ERROR = schema('error');
const DEPLOYMENTS = schema('deployments');
const valid = (v: ReturnType<typeof schema>, data: unknown) => {
  if (!v(data)) throw new Error(ajv.errorsText(v.errors, { separator: '\n' }));
  return data;
};
const q = (s = '') => new URLSearchParams(s);
const KEY = 'sk-fixture-START00001234';
const URL_OK = 'http://192.0.2.20:8000/v1';

const test = (over: Partial<TestResult>): TestResult => ({ ok: false, status: null, latency_ms: null, error: null, models: [], reason: null, ...over });

describe('Get started: the three ways in', () => {
  it('connect a server, add a Spark, install the engine, in that order', () => {
    expect(PATHS.map((p) => p.id)).toEqual(['server', 'spark', 'engine']);
    expect(PATHS.map((p) => p.title)).toEqual(['Connect a server', 'Add a Spark', 'Install the TandemLLM engine']);
    // the form is on the page; the other two lead to the wizard that pairs and installs
    expect(PATHS.map((p) => p.href)).toEqual([null, '#/add-spark', '#/add-spark']);
  });
  it('a fleet with no member opens on it; a member or a route given keeps the page asked for', () => {
    expect(landing('', [])).toBe('start');
    expect(landing('#/', [])).toBe('start');
    expect(landing('#/usage', [])).toBeNull();
    expect(landing('', [{ id: 'backend.bench', class: 'backend' }])).toBeNull();
    expect(landing('', [{ id: 'desk' }])).toBeNull();
  });
  it('is a page of its own under Settings', () => {
    expect(parseHash('#/start').view).toBe('start');
    expect(sectionOf('start')).toBe('start');
    expect(crumb('start')).toEqual({ group: 'Settings', label: 'Get started' });
    expect(MENU.find((g) => g.label === 'Settings')!.items.map((i) => i.id)).toEqual(['start', 'add-spark', 'app']);
  });
});

describe('the connect form', () => {
  it('asks for a URL it can test', () => {
    expect(urlError('')).toMatch(/Enter the server/);
    expect(urlError('   ')).toMatch(/Enter the server/);
    expect(urlError('192.0.2.20:8000')).toMatch(/http:\/\/ or https:\/\//);
    expect(urlError('ftp://x')).not.toBeNull();
    expect(urlError(' http://192.0.2.20:8000/v1 ')).toBeNull();
    expect(urlError('https://api.example.test')).toBeNull();
  });
  it('says what a test found, and when a save needs the user’s word', () => {
    const ok = testWords(test({ ok: true, status: 200, latency_ms: 11.6, models: ['a', 'b', 'c', 'd', 'e', 'f'] }));
    expect(ok).toEqual({ tone: 'ok', text: 'Connected in 12 ms. 6 models: a, b, c, d and 2 more.', confirm: null });
    expect(testWords(test({ ok: true, models: ['a'] })).text).toBe('Connected. 1 model: a.');
    expect(testWords(test({ ok: true })).text).toBe('Connected. It lists no model yet.');
    const key = testWords(test({ status: 401, error: 'HTTP 401: the key was refused', reason: 'key_rejected', key_rejected: true }));
    expect(key.tone).toBe('warn');
    expect(key.text).toMatch(/refused the API key\. Nothing is saved/);
    expect(key.confirm).toBe('Save it with this key anyway');
    const off = testWords(test({ error: 'Connection refused', reason: 'unreachable' }));
    expect(off.tone).toBe('err');
    expect(off.text).toMatch(/^No answer from this address \(Connection refused\)/);
    expect(off.confirm).toMatch(/Save it anyway/);
    const other = testWords(test({ status: 404, error: 'HTTP 404', reason: 'not_openai' }));
    expect(other.text).toMatch(/not like an OpenAI-compatible server \(HTTP 404\).*\/v1/);
    expect(other.confirm).toBe('Save it anyway');
    // an answer of a controller that names no reason reads as no answer, never as a pass
    expect(testWords(test({})).tone).toBe('err');
  });
  it('saves only what was tested: a pass, or a failure the user confirmed', () => {
    const k = formKey(URL_OK, KEY, 'generic');
    const pass = test({ ok: true });
    const fail = test({ reason: 'key_rejected' });
    expect(canSave(null, k, null, false)).toBe(false);
    expect(canSave(k, k, pass, false)).toBe(true);
    expect(canSave(k, k, fail, false)).toBe(false);
    expect(canSave(k, k, fail, true)).toBe(true);
    // the form changed since the test: the URL, the key or the kind
    expect(canSave(k, formKey(URL_OK + '/', KEY, 'generic'), pass, false)).toBe(false);
    expect(canSave(k, formKey(URL_OK, KEY + 'x', 'generic'), pass, true)).toBe(false);
    expect(canSave(k, formKey(URL_OK, KEY, 'tandemllm'), pass, false)).toBe(false);
    expect(formKey(` ${URL_OK} `, KEY, 'generic')).toBe(k);
  });
  it('opens Advanced for someone who works in advanced mode', () => {
    expect(advancedOpen(() => '0')).toBe(true);
    expect(advancedOpen(() => '1')).toBe(false);
    expect(advancedOpen(() => null)).toBe(false);
    expect(
      advancedOpen(() => {
        throw new Error('no storage');
      }),
    ).toBe(false);
  });
});

describe('the mock controller: a backend by URL', () => {
  it('a test says why it failed', () => {
    const b = new MockBackends();
    expect(b.handle('POST', '/v1/config/test', { url: URL_OK })[1]).toMatchObject({ ok: true, reason: null, key_rejected: false, models: ['tiny-a', 'tiny-b'] });
    expect(b.handle('POST', '/v1/config/test', { url: URL_OK, key: 'wrong-key' })[1]).toMatchObject({ ok: false, status: 401, reason: 'key_rejected', key_rejected: true, models: [] });
    expect(b.handle('POST', '/v1/config/test', { url: 'http://down.test/v1' })[1]).toMatchObject({ ok: false, status: null, reason: 'unreachable' });
    expect(b.handle('POST', '/v1/config/test', { url: 'http://plain.test/' })[1]).toMatchObject({ ok: false, status: 404, reason: 'not_openai' });
  });
  it('Gherkin "Wrong key": a verified add saves nothing until it is confirmed', () => {
    const b = new MockBackends();
    const n = b.backends.length;
    const [st, body] = b.handle('POST', '/v1/config/backends', { url: URL_OK, key: 'wrong-key', label: 'Keyed', verify: true });
    expect(st).toBe(409);
    expect(body).toMatchObject({ error: { type: 'key_rejected', test: { key_rejected: true } } });
    expect((body as { error: { message: string } }).error.message).toMatch(/nothing was saved/);
    expect(b.backends).toHaveLength(n);
    expect(b.members(0).nodes).toEqual([]);
    const [st2, saved] = b.handle('POST', '/v1/config/backends', { url: URL_OK, key: 'wrong-key', label: 'Keyed', verify: true, confirm: true });
    expect(st2).toBe(201);
    expect(saved).toMatchObject({ id: 'keyed', kind: 'generic', test: { state: 'down' } });
    const m = b.members(0).nodes[0] as unknown as FleetNode;
    expect(m.status).toBe('alert');
    expect(m.status_text).toMatch(/^key rejected/);
    expect(m.backend).toMatchObject({ state: 'key_rejected', key_set: true, models: [] });
    expect(JSON.stringify([saved, b.overview(), b.members(0)])).not.toContain('wrong-key');
  });
  it('Gherkin "Connect a server": by its URL alone it is a member with its models as deployments', () => {
    const b = new MockBackends();
    const f = new MockFleet({ seed: 42, scenario: 'empty', now: 1_800_000_000 });
    f.extern = (now, taken) => b.members(now, taken);
    const at = 1_800_000_010_000;
    expect(valid(NODES, f.handle('GET', '/v1/fleet/nodes', q(), null, at).body)).toMatchObject({ nodes: [], backends: [] });
    const [st, saved] = b.handle('POST', '/v1/config/backends', { url: URL_OK, key: KEY, verify: true });
    expect(st).toBe(201);
    expect(saved).toMatchObject({ id: '192-0-2-20', kind: 'generic', key: { key_set: true, last4: '1234' } });
    const ans = valid(NODES, f.handle('GET', '/v1/fleet/nodes', q(), null, at).body) as { nodes: FleetNode[]; backends: FleetNode[] };
    expect(ans.nodes).toEqual([]);
    expect(ans.backends).toHaveLength(1);
    const m = ans.backends[0];
    expect(m).toMatchObject({ id: 'backend.192-0-2-20', class: 'backend', status: 'up', hw: 'OpenAI-compatible server', address: '192.0.2.20:8000', pairing: { state: 'unpaired' } });
    expect(m.backend).toMatchObject({ kind: 'generic', models: ['tiny-a', 'tiny-b'], figures: 'gateway', key_set: true });
    expect(valid(NODE, f.handle('GET', `/v1/fleet/nodes/${m.id}`, q(), null, at).body)).toMatchObject({ id: m.id });
    const deps = (valid(DEPLOYMENTS, f.handle('GET', '/v1/fleet/deployments', q(), null, at).body) as { deployments: { id: string; members: string[]; record: boolean }[] }).deployments;
    expect(deps.map((d) => [d.id, d.members, d.record])).toEqual([['tiny-a', [m.id], false], ['tiny-b', [m.id], false]]);
    // the key is in no answer
    expect(JSON.stringify([saved, ans, deps, b.overview()])).not.toContain(KEY);
    // a second server at the same host gets its own id; a label names one
    expect(b.handle('POST', '/v1/config/backends', { url: 'http://192.0.2.20:9000/v1', verify: true })[1]).toMatchObject({ id: '192-0-2-20-2' });
    expect(b.handle('POST', '/v1/config/backends', { url: 'http://192.0.2.21:9000/v1', label: 'Bench server', verify: true })[1]).toMatchObject({ id: 'bench-server' });
    // a backend added on the Backends screen (no verify) is as before: no member of the mock's fleet
    expect(b.handle('POST', '/v1/config/backends', { id: 'old-way', kind: 'generic', url: 'http://192.0.2.30:8000/v1' })[0]).toBe(201);
    expect(b.members(0).nodes.map((x) => x.id)).toEqual(['backend.192-0-2-20', 'backend.192-0-2-20-2', 'backend.bench-server']);
  });
  it('Gherkin "No host figures without TandemNode": the sample is empty and says why; node-only routes answer 409', () => {
    const b = new MockBackends();
    const f = new MockFleet({ seed: 42, scenario: 'fleet4', now: 1_800_000_000 });
    f.extern = (now, taken) => b.members(now, taken);
    const at = 1_800_000_010_000;
    b.handle('POST', '/v1/config/backends', { url: URL_OK, label: 'Bench', verify: true });
    const ans = valid(NODES, f.handle('GET', '/v1/fleet/nodes', q(), null, at).body) as { nodes: FleetNode[]; backends: FleetNode[] };
    expect(ans.nodes).toHaveLength(4);
    // its colour is one no Spark holds
    expect(ans.nodes.map((n) => n.colour)).not.toContain(ans.backends[0].colour);
    const id = 'backend.bench';
    const s = valid(SAMPLE, f.handle('GET', `/v1/fleet/nodes/${id}/sample`, q(), null, at).body) as { via: string; sample: unknown; note: string };
    expect(s).toMatchObject({ via: 'none', sample: null });
    expect(s.note).toMatch(/no host figures.*TandemNode/);
    for (const [method, path] of [['GET', `/v1/fleet/nodes/${id}/actions/drain`], ['POST', `/v1/fleet/nodes/${id}/actions/reboot`], ['PATCH', `/v1/fleet/nodes/${id}`], ['DELETE', `/v1/fleet/nodes/${id}`]] as const) {
      const r = f.handle(method, path, q(), {}, at);
      expect(r.status, path).toBe(409);
      expect(valid(ERROR, r.body)).toMatchObject({ error: { type: 'not_a_node' } });
    }
    const gpu = f.handle('GET', '/v1/fleet/metrics', q(`source=node:${id}&metric=gpu&range=5m`), null, at).body as { series: { node: string; values: (number | null)[] }[] };
    expect(gpu.series.map((x) => x.node)).toEqual([id]);
    expect(new Set(gpu.series[0].values)).toEqual(new Set([null]));
    // the Sparks' own routes are as before
    expect(f.handle('GET', '/v1/fleet/nodes/desk/sample', q(), null, at).status).toBe(200);
  });
});

describe('a backend among the fleet’s members', () => {
  const node = (id: string, colour: number, cls: 'spark' | 'backend' = 'spark'): FleetNode => ({ id, alias: id, colour, class: cls, status: 'up', status_text: 'up', drain: null, lease_end: null } as unknown as FleetNode);
  const nodes = [node('desk', 1), node('r3', 2), node('backend.bench', 3, 'backend')];
  const fleet = { nodes, deployments: [{ id: 'tiny-a' }] };
  it('the scope bar offers it in a group of its own, and a link to it resolves', () => {
    const cs = scopeChoices(fleet, DEFAULT_SCOPE);
    expect(cs.map((c) => [c.group, c.id ?? 'all'])).toEqual([[null, 'all'], ['Sparks', 'desk'], ['Sparks', 'r3'], ['Backends', 'backend.bench'], ['Deployments', 'tiny-a']]);
    const s = { kind: 'node' as const, id: 'backend.bench', range: '6h' as const, live: true };
    expect(resolveScope(s, fleet)).toEqual(s);
    expect(scopeChoices(fleet, s).find((c) => c.on)!.id).toBe('backend.bench');
    // one Spark and one backend are two members: there is a choice
    expect(scopeChoices({ nodes: [nodes[0], nodes[2]], deployments: [] }, DEFAULT_SCOPE)).toHaveLength(3);
    // a fleet without a backend is as before
    expect(scopeChoices({ nodes: nodes.slice(0, 2), deployments: [] }, DEFAULT_SCOPE).map((c) => c.group)).toEqual([null, 'Sparks', 'Sparks']);
  });
  it('is no Spark: the pages about machines leave it out, and the words say members', () => {
    expect(nodes.map(isSpark)).toEqual([true, true, false]);
    expect(sparksOf(nodes).map((n) => n.id)).toEqual(['desk', 'r3']);
    expect(sparksOf(null)).toEqual([]);
    expect(membersWord(nodes)).toBe('members');
    expect(membersWord(nodes, 1)).toBe('member');
    expect(membersWord(nodes.slice(0, 2))).toBe('Sparks');
    expect(membersWord(nodes.slice(0, 1))).toBe('Spark');
    expect(memberHref(nodes[0])).toBe('#/sparks/desk');
    expect(memberHref(nodes[2])).toBe('#/api/backends');
    const state = { nodes, deployments: [], alerts: [], tps: {}, gateway: true } as unknown as FleetState;
    expect(sparksOnly(state)!.nodes.map((n) => n.id)).toEqual(['desk', 'r3']);
    expect(sparksOnly(null)).toBeNull();
    expect(readout(state).label).toMatch(/^Fleet: 2 Sparks, 1 backend,/);
    expect(readout(state).swatches).toHaveLength(3);
    expect(readout({ ...state, nodes: nodes.slice(0, 2) }).label).toMatch(/^Fleet: 2 Sparks, no rate yet/);
  });
});
