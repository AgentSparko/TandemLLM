// VIS-247 (tests for VIS-246): the Add Spark wizard's logic (lib/addspark.ts) and its session in the mock
// (mock/addspark.ts) as the controller answers it (proxy/addspark.py): every answer holds to the contract;
// the four Gherkin scenarios walked through the routes; the steps' gates; resume; Link only with two or
// more new LAN Sparks; a version that is not the fleet's; each red check naming its fix; the rented form,
// the names, the install's progress and the permissions in words.
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';
import { describe, expect, it } from 'vitest';
import { checks, verdict } from '../mock/addspark.ts';
import { MOCK_LAN } from '../mock/discovery.ts';
import { MOCK_PAIR, MockFleet } from '../mock/fleet.ts';
import type { AddSession, FleetNode } from '../src/api/fleet.ts';
import { gate, installPercent, installWords, nameDraft, nameErrors, namesBody, nextStep, permissionRows, prevStep, rentedLine, rentedTerms, resumeStep, skipped, skippedBetween, versionMismatch } from '../src/lib/addspark.ts';

const FDIR = join(__dirname, '..', '..', 'contracts', 'fleet-v1');
const ajv = new Ajv2020({ allErrors: true, strict: true });
const schema: Record<string, ReturnType<typeof ajv.compile>> = {};
for (const f of readdirSync(FDIR)) if (f.endsWith('.schema.json')) schema[f.replace('.schema.json', '')] = ajv.compile(JSON.parse(readFileSync(join(FDIR, f), 'utf8')));
function valid(name: string, data: unknown): void {
  if (!schema[name](data)) throw new Error(`${name}: ${ajv.errorsText(schema[name].errors, { separator: '\n' })}`);
}

const T0 = Date.parse('2026-10-28T09:00:00Z');
const q = () => new URLSearchParams();

/** the mock fleet's routes at T0 + ms, every answer checked against its schema */
function routes(scenario: 'fleet4' | 'fleet1' = 'fleet4') {
  const m = new MockFleet({ scenario, now: T0 / 1000 });
  const call = (method: string, path: string, body: unknown, ms: number) => {
    const r = m.handle(method, path, q(), body, T0 + ms);
    const b = r.body as { error?: unknown; session?: unknown; fleet_version?: unknown };
    if (r.status !== 200) valid('error', b);
    else if (path === '/v1/fleet/add' && method === 'GET') {
      valid('add', b);
      if (b.session) valid('add-session', b.session);
    } else if (path.startsWith('/v1/fleet/add')) valid('add-session', b);
    return r;
  };
  const s = (method: string, path: string, body: unknown, ms: number): AddSession => {
    const r = call(method, path, body, ms);
    expect(r.status, JSON.stringify(r.body)).toBe(200);
    return r.body as AddSession;
  };
  return { m, call, s };
}

/** a session with discovery run (and the scan said yes to) */
function begin(scenario: 'fleet4' | 'fleet1' = 'fleet4') {
  const r = routes(scenario);
  r.call('POST', '/v1/fleet/discovery', {}, 0);
  r.call('POST', '/v1/fleet/discovery/scan', { subnet: '192.0.2.0/24' }, 3500);
  const sid = r.s('POST', '/v1/fleet/add', {}, 9000).id;
  const rowAt = (ip: string, ms: number) => (r.m.handle('GET', '/v1/fleet/discovery', q(), null, T0 + ms).body as { rows: { id: string; addresses: string[] }[] }).rows.find((x) => x.addresses.includes(ip))!.id;
  return { ...r, sid, rowAt };
}

const nodesOf = (m: MockFleet, ms: number) => (m.handle('GET', '/v1/fleet/nodes', q(), null, T0 + ms).body as { nodes: FleetNode[] }).nodes;

describe('the Add Spark session in the mock', () => {
  it('one active session: a new one, busy, Start over, resume (GET), abandoned; the schemas are strict', () => {
    const { s, call } = routes();
    expect((call('GET', '/v1/fleet/add', null, 0).body as { session: unknown }).session).toBeNull();
    const a = s('POST', '/v1/fleet/add', {}, 0);
    expect(a).toMatchObject({ state: 'active', step: 'find', targets: [], fleet_version: '0.15.0' });
    expect(call('POST', '/v1/fleet/add', {}, 10).status).toBe(409);
    s('PATCH', `/v1/fleet/add/${a.id}`, { step: 'connect' }, 20);
    expect((call('GET', '/v1/fleet/add', null, 30).body as { session: AddSession }).session.step).toBe('connect');
    const b = s('POST', '/v1/fleet/add', { replace: true }, 40);
    expect(b.id).not.toBe(a.id);
    expect(s('GET', `/v1/fleet/add/${a.id}`, null, 50).state).toBe('abandoned');
    expect(call('PATCH', `/v1/fleet/add/${a.id}`, { step: 'check' }, 60).status).toBe(409);
    for (const bad of [{ step: 'later' }, {}, { names: [] }]) expect(call('PATCH', `/v1/fleet/add/${b.id}`, bad, 70).status).toBe(400);
    expect(call('PATCH', `/v1/fleet/add/${b.id}`, { step: 'done' }, 80).status).toBe(409);
    expect(s('DELETE', `/v1/fleet/add/${b.id}`, null, 90).state).toBe('abandoned');
    expect(schema['add-session']({ id: 'x' })).toBe(false);
    expect(schema['add-connect']({ method: 'code', code: '48291' })).toBe(false);
    expect(schema['add-connect']({ method: 'ssh', password: 'x', shell: 'y' })).toBe(false);
    expect(schema['add-link']({ action: 'fix' })).toBe(false);
    expect(schema['add-patch']({})).toBe(false);
    expect(schema['add-target']({ row: 'd', extra: 1 })).toBe(false);
    expect(schema['add-run']({ secrets: { t1: { password: '' } } })).toBe(false);
    expect(schema['pair-ssh']({ host: 'h', user: 'u', jumps: [{ host: 'j' }] })).toBe(false);
    expect(schema['pair-ssh']({ host: 'h', user: 'u', key_file: '~/.ssh/k', jumps: [{ user: null, host: 'j', port: 22 }], tunnel: true })).toBe(true);
  });

  it('Gherkin "LAN Spark with TandemLLM 0.15.0 + pairing code": Check passes, Install is skipped, Name is shown, the node joins', () => {
    const { m, s, call, sid, rowAt } = begin();
    let a = s('POST', `/v1/fleet/add/${sid}/targets`, { row: rowAt(MOCK_PAIR.address, 9100) }, 9100);
    const tid = a.targets[0].id;
    expect(a.targets[0]).toMatchObject({ kind: 'spark', connect: ['code', 'ssh'], window: 7701, transport: 'lan' });
    expect(call('POST', `/v1/fleet/add/${sid}/targets/${tid}/connect`, { method: 'code', code: '000000' }, 9200).status).toBe(403);
    a = s('POST', `/v1/fleet/add/${sid}/targets/${tid}/connect`, { method: 'code', code: MOCK_PAIR.code }, 9300);
    expect(a.targets[0]).toMatchObject({ state: 'paired', via: 'code', error: null });
    a = s('POST', `/v1/fleet/add/${sid}/check`, {}, 9400);
    const t = a.targets[0];
    expect(t.preflight).toMatchObject({ via: 'agent', verdict: { fails: 0, install: 'skip' } });
    expect(t.install?.state).toBe('skip');
    expect(gate(a, 'check')).toBe(true);
    // Install and Link are skipped, each saying why; Next from Check is Name
    expect(skipped(a, 'install')).toContain('runs TandemLLM 0.15.0 already');
    expect(skipped(a, 'link')).toContain('only new Spark');
    expect(nextStep(a, 'check')).toBe('name');
    expect(prevStep(a, 'name')).toBe('check');
    expect(skippedBetween(a, 'check', 'name').map((x) => x.step)).toEqual(['install', 'link']);
    a = s('PATCH', `/v1/fleet/add/${sid}`, { step: 'name' }, 9500);
    const node = nodesOf(m, 9500).find((n) => n.id === t.node)!;
    const d = nameDraft(a.targets[0], node);
    expect(d).toMatchObject({ alias: node.alias, colour: node.colour });
    a = s('PATCH', `/v1/fleet/add/${sid}`, { names: namesBody(a, { [tid]: { ...d, alias: 'Spark 5', tags: 'desk, owned' } }), step: 'done' }, 9600);
    expect(a).toMatchObject({ state: 'done', step: 'done' });
    expect(nodesOf(m, 9700).find((n) => n.id === t.node)).toMatchObject({ alias: 'Spark 5', tags: ['desk', 'owned'], transport: 'lan' });
    expect((call('GET', '/v1/fleet/add', null, 9800).body as { session: unknown }).session).toBeNull();
    // the topology draws it on the LAN
    const topo = m.handle('GET', '/v1/fleet/topology', q(), null, T0 + 9900).body as { links: { b: string }[] };
    expect(topo.links.some((l) => l.b === t.node)).toBe(true);
  });

  it('Gherkin "Bare box over SSH": not installed, held password, the red clock with its fix, Install’s steps on the clock, paired; the password in no answer', () => {
    const { m, s, call, sid, rowAt } = begin();
    let a = s('POST', `/v1/fleet/add/${sid}/targets`, { row: rowAt(MOCK_PAIR.bare, 9100) }, 9100);
    const tid = a.targets[0].id;
    const url = `/v1/fleet/add/${sid}/targets/${tid}`;
    expect(call('POST', `${url}/connect`, { method: 'ssh', user: 'spark', password: MOCK_PAIR.password }, 9150).status).toBe(409); // host key first
    a = s('POST', `${url}/hostkey`, {}, 9200);
    const fp = a.targets[0].host_key!.fingerprint;
    expect(a.targets[0].host_key).toMatchObject({ known: 'new', confirmed: false });
    expect(call('POST', `${url}/connect`, { method: 'ssh', user: 'spark', password: 'wrong', host_key: fp }, 9250).status).toBe(502);
    a = s('POST', `${url}/connect`, { method: 'ssh', user: 'spark', password: MOCK_PAIR.password, host_key: fp }, 9300);
    expect(a.targets[0]).toMatchObject({ state: 'bare', kind: 'gb10', title: 'GB10 box, nothing installed', secret: { held: true } });
    expect(gate(a, 'connect')).toBe(true);
    a = s('POST', `/v1/fleet/add/${sid}/check`, {}, 9400);
    const red = a.targets[0].preflight!.rows.filter((r) => r.state === 'fail');
    expect(red.map((r) => r.id)).toEqual(['clock']);
    expect(red[0].fix).toContain('timedatectl set-ntp true');
    expect(gate(a, 'check')).toBe('Fix the red rows, then check again');
    expect(call('POST', `/v1/fleet/add/${sid}/install`, {}, 9450).status).toBe(409);
    a = s('POST', `/v1/fleet/add/${sid}/check`, { targets: [tid] }, 9500);
    expect(a.targets[0].preflight!.verdict).toMatchObject({ fails: 0, install: 'needed' });
    expect(a.targets[0].preflight!.rows.find((r) => r.id === 'linger')).toMatchObject({ state: 'warn' });
    expect(skipped(a, 'install')).toBeNull();
    expect(nextStep(a, 'check')).toBe('install');
    a = s('POST', `/v1/fleet/add/${sid}/install`, {}, 10_000);
    expect(a.targets[0].secret.held).toBe(false); // the job has it; the vault forgot it
    expect(gate(a, 'install')).toBe('Installing…');
    a = s('GET', `/v1/fleet/add/${sid}`, null, 19_000);
    const run = a.targets[0].install!;
    expect(run.state).toBe('running');
    expect(run.steps!.map((x) => x.state)).toEqual(['done', 'done', 'done', 'running', 'todo', 'todo']);
    expect(installPercent(run)).toBeGreaterThan(40);
    expect(installWords(run)).toBe("Install the engine's runtime 0.15.0…");
    a = s('GET', `/v1/fleet/add/${sid}`, null, 31_000);
    expect(a.targets[0]).toMatchObject({ state: 'paired', install: { state: 'done' } });
    expect(installPercent(a.targets[0].install)).toBe(100);
    expect(gate(a, 'install')).toBe(true);
    expect(nodesOf(m, 31_000).some((n) => n.id === a.targets[0].node && n.hw === 'ASUS Ascent GX10')).toBe(true);
    expect(JSON.stringify(a)).not.toContain(MOCK_PAIR.password);
  });

  it('Gherkin "Rented": the ssh line and a key, a lease end: reached through a tunnel, the reminder before the lease ends', () => {
    for (const [scenario, hours] of [['fleet4', 72], ['fleet1', 24]] as const) {
      const { m, s, call, sid } = begin(scenario);
      const end = T0 / 1000 + 6 * 86400;
      expect(call('POST', `/v1/fleet/add/${sid}/targets`, { ssh: 'ssh -p 2201 spark@198.51.100.7', rented: { lease_end: T0 / 1000 - 10 } }, 9050).status).toBe(400);
      let a = s('POST', `/v1/fleet/add/${sid}/targets`, { ssh: 'ssh -p 2201 spark@198.51.100.7', rented: { lease_end: end, cost_per_hour: 2.4 }, key: 'fixture key text' }, 9100);
      const t0 = a.targets[0];
      expect(t0).toMatchObject({ kind: 'rented', transport: 'tunnel', ssh: { host: MOCK_LAN.rented, port: 2201, user: 'spark', auth: 'key' }, secret: { held: true }, rented: { lease_end: end, cost_per_hour: 2.4 } });
      expect(JSON.stringify(a)).not.toContain('fixture key text');
      const url = `/v1/fleet/add/${sid}/targets/${t0.id}`;
      const fp = s('POST', `${url}/hostkey`, {}, 9200).targets[0].host_key!.fingerprint;
      expect(call('POST', `${url}/connect`, { method: 'ssh', user: 'spark', password: 'x', host_key: fp }, 9250).status).toBe(400); // a tunnel takes a key
      a = s('POST', `${url}/connect`, { method: 'ssh', user: 'spark', host_key: fp }, 9300);
      expect(a.targets[0].state).toBe('bare');
      s('POST', `/v1/fleet/add/${sid}/check`, {}, 9400);
      s('POST', `/v1/fleet/add/${sid}/install`, {}, 9500);
      a = s('GET', `/v1/fleet/add/${sid}`, null, 40_000);
      const t = a.targets[0];
      expect(t.state).toBe('paired');
      const n = nodesOf(m, 40_000).find((x) => x.id === t.node)!;
      expect(n).toMatchObject({ transport: 'tunnel', owned: false, lease_end: end, cost_per_hour: 2.4, icon: 'cloud', tags: ['rented'] });
      expect(a.reminders).toEqual([{ node: t.node, target: t.id, lease_end: end, at: end - hours * 3600, hours, rule: 'lease', enabled: true }]);
      // tunnelled: no fabric to link
      expect(skipped(a, 'link')).toContain('through a tunnel');
      const rules = (m.handle('GET', '/v1/fleet/rules', q(), null, T0 + 40_000).body as { rules: { id: string; metric: string; condition: string }[] }).rules;
      expect(rules.find((r) => r.id === 'lease')).toMatchObject({ metric: 'lease_left', condition: `lease ends in < ${hours} h` });
    }
  });

  it('Gherkin "Resume": closed at Install, reopened: the Install step with its progress', () => {
    const { s, sid, rowAt, call } = begin();
    let a = s('POST', `/v1/fleet/add/${sid}/targets`, { row: rowAt(MOCK_PAIR.bare, 9100) }, 9100);
    const url = `/v1/fleet/add/${sid}/targets/${a.targets[0].id}`;
    const fp = s('POST', `${url}/hostkey`, {}, 9200).targets[0].host_key!.fingerprint;
    s('POST', `${url}/connect`, { method: 'ssh', user: 'spark', password: MOCK_PAIR.password, host_key: fp }, 9300);
    s('POST', `/v1/fleet/add/${sid}/check`, {}, 9400);
    s('POST', `/v1/fleet/add/${sid}/check`, {}, 9450);
    s('PATCH', `/v1/fleet/add/${sid}`, { step: 'install' }, 9500);
    s('POST', `/v1/fleet/add/${sid}/install`, {}, 9600);
    // the page closes; another opens it later
    const got = call('GET', '/v1/fleet/add', null, 20_000).body as { session: AddSession };
    a = got.session;
    expect(a.id).toBe(sid);
    expect(resumeStep(a)).toBe('install');
    expect(a.targets[0].install!.steps!.find((x) => x.state === 'running')!.id).toBe('engine');
    expect(installPercent(a.targets[0].install)).toBeGreaterThan(0);
    expect(call('DELETE', `/v1/fleet/add/${sid}`, null, 20_100).status).toBe(409); // its install runs
  });

  it('a version that is not the fleet’s: a red TandemLLM row with its fix, Check’s gate says so, Install refuses', () => {
    const { s, call, sid, rowAt } = begin();
    let a = s('POST', `/v1/fleet/add/${sid}/targets`, { row: rowAt(MOCK_PAIR.owned, 9100) }, 9100);
    const t0 = a.targets[0];
    expect(t0.connect).toEqual(['takeover']);
    const url = `/v1/fleet/add/${sid}/targets/${t0.id}`;
    const fp = s('POST', `${url}/hostkey`, {}, 9200).targets[0].host_key!.fingerprint;
    a = s('POST', `${url}/connect`, { method: 'takeover', user: 'spark', password: MOCK_PAIR.password, host_key: fp }, 9300);
    expect(a.targets[0].state).toBe('paired');
    a = s('POST', `/v1/fleet/add/${sid}/check`, {}, 9400);
    const t = a.targets[0];
    expect(versionMismatch(t)).toBe('0.14.2 · the fleet runs 0.15.0');
    expect(t.preflight!.rows.find((r) => r.id === 'tandemllm')!.fix).toContain('Install refuses 0.14.2');
    expect(t.preflight!.verdict!.install).toBe('refused');
    expect(gate(a, 'check')).toBe('spark-annex runs TandemLLM 0.14.2; the fleet runs 0.15.0: update it there, then check again');
    expect(skipped(a, 'install')).toBeNull();
    const r = call('POST', `/v1/fleet/add/${sid}/install`, {}, 9500);
    expect(r.status).toBe(409);
    expect((r.body as { error: { type: string } }).error.type).toBe('version_mismatch');
  });

  it('Link only with two or more new LAN Sparks: one is skipped with why; two are tested; the sudo setup is not built', () => {
    const { s, call, sid, rowAt } = begin();
    let a = s('POST', `/v1/fleet/add/${sid}/targets`, { row: rowAt(MOCK_PAIR.address, 9100) }, 9100);
    a = s('POST', `/v1/fleet/add/${sid}/targets`, { row: rowAt(MOCK_PAIR.ssh, 9110) }, 9110);
    const [c, h] = a.targets;
    expect(call('POST', `/v1/fleet/add/${sid}/targets`, { row: c.row }, 9120).status).toBe(409); // picked already
    s('POST', `/v1/fleet/add/${sid}/targets/${c.id}/connect`, { method: 'code', code: MOCK_PAIR.code }, 9200);
    a = s('POST', `/v1/fleet/add/${sid}/link`, { action: 'test' }, 9250);
    expect(a.link.state).toBe('not_needed');
    const fp = s('POST', `/v1/fleet/add/${sid}/targets/${h.id}/hostkey`, {}, 9300).targets[1].host_key!.fingerprint;
    a = s('POST', `/v1/fleet/add/${sid}/targets/${h.id}/connect`, { method: 'ssh', user: 'spark', password: MOCK_PAIR.password, host_key: fp }, 9400);
    expect(a.targets.every((t) => t.state === 'paired')).toBe(true);
    a = s('POST', `/v1/fleet/add/${sid}/check`, {}, 9500);
    expect(skipped(a, 'install')).toContain('every Spark runs');
    expect(skipped(a, 'link')).toBeNull();
    expect(nextStep(a, 'check')).toBe('link');
    expect(gate(a, 'link')).toBe('Test the link, or skip it');
    expect(call('POST', `/v1/fleet/add/${sid}/link`, { action: 'configure' }, 9600).status).toBe(501);
    a = s('POST', `/v1/fleet/add/${sid}/link`, { action: 'test' }, 9700);
    expect(a.link.state).toBe('done');
    expect(a.link.results).toHaveLength(2);
    expect(a.link.results.every((r) => (r.test?.gbps ?? 0) > 1)).toBe(true);
    expect(gate(a, 'link')).toBe(true);
    expect(s('POST', `/v1/fleet/add/${sid}/link`, { action: 'skip' }, 9800).link.state).toBe('skipped');
  });

  it('a login within the mDNS browse’s first seconds still says "GB10 box, nothing installed" (each phase merges the earlier)', () => {
    const { m, s, call } = routes();
    call('POST', '/v1/fleet/discovery', {}, 0);
    call('POST', '/v1/fleet/discovery/scan', { subnet: '192.0.2.0/24' }, 50);
    const sid = s('POST', '/v1/fleet/add', {}, 1400).id;
    // the scan reached .63 at 1.31 s, its mDNS answer comes at 1.6 s: the login at 1.43 s is newest
    const rid = (m.handle('GET', '/v1/fleet/discovery', q(), null, T0 + 1400).body as { rows: { id: string; addresses: string[] }[] }).rows.find((x) => x.addresses.includes(MOCK_PAIR.bare))!.id;
    const tid = s('POST', `/v1/fleet/add/${sid}/targets`, { row: rid }, 1410).targets[0].id;
    const fp = s('POST', `/v1/fleet/add/${sid}/targets/${tid}/hostkey`, {}, 1420).targets[0].host_key!.fingerprint;
    const t = s('POST', `/v1/fleet/add/${sid}/targets/${tid}/connect`, { method: 'ssh', user: 'spark', password: MOCK_PAIR.password, host_key: fp }, 1430).targets[0];
    expect(t).toMatchObject({ state: 'bare', kind: 'gb10', title: 'GB10 box, nothing installed' });
    for (const at of [1430, 1700, 5000]) {
      const r = (m.handle('GET', '/v1/fleet/discovery', q(), null, T0 + at).body as { rows: { kind: string; addresses: string[] }[] }).rows.find((x) => x.addresses.includes(MOCK_PAIR.bare))!;
      expect(r.kind).toBe('gb10');
    }
  });

  it('a held password expires: Check asks for it again and says why', () => {
    const { s, call, sid, rowAt } = begin();
    const a = s('POST', `/v1/fleet/add/${sid}/targets`, { row: rowAt(MOCK_PAIR.bare, 9100) }, 9100);
    const url = `/v1/fleet/add/${sid}/targets/${a.targets[0].id}`;
    const fp = s('POST', `${url}/hostkey`, {}, 9200).targets[0].host_key!.fingerprint;
    s('POST', `${url}/connect`, { method: 'ssh', user: 'spark', password: MOCK_PAIR.password, host_key: fp }, 9300);
    const late = 9300 + 31 * 60_000;
    const r = call('POST', `/v1/fleet/add/${sid}/check`, {}, late);
    expect(r.status).toBe(409);
    expect((r.body as { error: { type: string; message: string } }).error).toMatchObject({ type: 'secret_needed' });
    expect((r.body as { error: { message: string } }).error.message).toContain('never stored');
    expect(s('POST', `/v1/fleet/add/${sid}/check`, { secrets: { [a.targets[0].id]: { password: MOCK_PAIR.password } } }, late + 10).targets[0].secret.held).toBe(true);
  });
});

describe('the wizard’s logic', () => {
  it('every red check row names its fix; warnings too; the TandemLLM row decides Install', () => {
    const now = T0 / 1000;
    const base = { system: 'Linux', arch: 'aarch64', kernel: '6.11', dgx: 'DGX OS 7.2.1', driver_smi: '580', cuda: '13.0', mem_kb: String(128 * 1048576), disk_kb: String(3000 * 1048576), tandemllm: '', cx7: 'p0:4: ACTIVE:200', sudo: 'password', ntp: 'yes', linger: 'yes', python: '3.12', venv: 'yes', systemd: 'yes', now: String(now) };
    const bad = { ...base, driver_smi: '', cuda: '12.4', disk_kb: String(10 * 1048576), ntp: 'no', python: '3.10', tandemllm: '0.14.2', systemd: 'no' };
    const red = checks(bad, '0.15.0', now).filter((r) => r.state === 'fail');
    expect(red.map((r) => r.id).sort()).toEqual(['clock', 'cuda', 'disk', 'driver', 'python', 'systemd', 'tandemllm']);
    expect(red.every((r) => r.fix && r.fix.length > 20)).toBe(true);
    const warn = checks({ ...base, cx7: '', sudo: 'none', linger: 'no', disk_kb: String(50 * 1048576) }, '0.15.0', now).filter((r) => r.state === 'warn');
    expect(warn.map((r) => r.id).sort()).toEqual(['cx7', 'disk', 'linger', 'sudo']);
    expect(warn.every((r) => r.fix)).toBe(true);
    expect(verdict(checks(base, '0.15.0', now), false).install).toBe('needed');
    expect(verdict(checks({ ...base, tandemllm: '0.15.0' }, '0.15.0', now, false), true)).toMatchObject({ fails: 0, install: 'skip' });
    expect(verdict(checks({ ...base, tandemllm: '0.14.2' }, '0.15.0', now), true).install).toBe('refused');
    // a paired node is not asked for Python or linger: nothing is installed there over SSH
    expect(checks(base, '0.15.0', now, false).map((r) => r.id)).not.toContain('python');
  });

  it('the gates: Find needs a device, Connect each paired or reached, Name refuses a taken alias or colour', () => {
    const s = (over: Partial<AddSession>): AddSession => ({ contract_version: '1.0', id: 'a', state: 'active', step: 'find', created_at: 0, updated_at: 0, by: 'x', fleet_version: '0.15.0', targets: [], link: { state: 'todo', why: null, results: [] }, done_at: null, seq: 0, reminders: [], ...over });
    const t = (id: string, state: 'todo' | 'paired' | 'bare', node: string | null = null) =>
      ({ id, row: id, label: id, kind: 'spark', title: '', detail: '', address: '192.0.2.1', connect: ['code'], hw: null, version: null, window: null, ssh: { host: '192.0.2.1', port: 22, user: null, jumps: [], key_file: null, auth: null }, rented: null, transport: 'lan', state, via: null, host_key: null, error: null, node, preflight: null, install: null, name: null, secret: { held: false, until: null } }) as AddSession['targets'][number];
    expect(gate(s({}), 'find')).toBe('Pick at least one device');
    expect(gate(s({ targets: [t('a', 'todo'), t('b', 'bare')] }), 'connect')).toBe('Connect a');
    expect(gate(s({ targets: [t('a', 'paired', 'n1'), t('b', 'bare')] }), 'connect')).toBe(true);
    expect(gate(s({ targets: [t('a', 'paired', 'n1')] }), 'check')).toBe('Check a');
    // resume never lands past a gate that does not hold
    expect(resumeStep(s({ step: 'install', targets: [t('a', 'todo')] }))).toBe('connect');
    const names = { a: { alias: 'Desk', colour: 3, icon: 'desk' as const, tags: '' }, b: { alias: 'New', colour: 3, icon: 'desk' as const, tags: '' } };
    const sess = s({ targets: [t('a', 'paired', 'n1'), t('b', 'paired', 'n2')] });
    const fleetNodes = [{ id: 'desk', alias: 'Desk', colour: 1 }, { id: 'n1', alias: 'Spark 5', colour: 5 }, { id: 'n2', alias: 'Spark 6', colour: 6 }];
    const err = nameErrors(sess, names, fleetNodes);
    expect(err.a).toBe('Another Spark is called Desk.');
    expect(err.b).toContain('Colour 3');
    expect(gate(sess, 'name', names, fleetNodes)).toBe('Another Spark is called Desk.');
    expect(gate(sess, 'name', { a: { ...names.a, alias: 'East' }, b: { ...names.b, colour: 4 } }, fleetNodes)).toBe(true);
    // Link: two LAN nodes need it; a tunnelled one does not count
    expect(skipped(sess, 'link')).toBeNull();
    const far = s({ targets: [t('a', 'paired', 'n1'), { ...t('b', 'paired', 'n2'), transport: 'tunnel' }] });
    expect(skipped(far, 'link')).toBe('b is reached through a tunnel: no fabric between it and the LAN; a is the only one on the LAN');
  });

  it('the rented form: the line with its own jump and key path, the lease at the end of its day, the cost', () => {
    expect(rentedLine({ line: 'spark@198.51.100.7 -p 2201', key: '', keyPath: '~/.ssh/rent', jump: 'hop@198.51.100.1', leaseEnd: '', cost: '' })).toBe('ssh -J hop@198.51.100.1 -i ~/.ssh/rent spark@198.51.100.7 -p 2201');
    expect(rentedLine({ line: 'ssh -p 2201 spark@198.51.100.7', key: '', keyPath: '', jump: '', leaseEnd: '', cost: '' })).toBe('ssh -p 2201 spark@198.51.100.7');
    const now = new Date(2026, 9, 28, 12).getTime() / 1000;
    const got = rentedTerms({ leaseEnd: '2026-11-02', cost: '€2,40' }, now);
    expect(got).toEqual({ lease_end: new Date(2026, 10, 2, 23, 59).getTime() / 1000, cost_per_hour: 2.4 });
    expect(rentedTerms({ leaseEnd: '2026-10-01', cost: '' }, now)).toBe('The lease ends in the future');
    expect(rentedTerms({ leaseEnd: '', cost: '' }, now)).toBe('Give the day the lease ends');
    expect(rentedTerms({ leaseEnd: '2026-11-02', cost: 'cheap' }, now)).toBe('Cost per hour is a number, like 2.40');
  });

  it('the permissions: each with why and what works without it; Keychain said as not available, never faked', () => {
    const rows = permissionRows('denied', 'default');
    expect(rows.map((r) => r.id)).toEqual(['lan', 'notify', 'keychain']);
    expect(rows[0]).toMatchObject({ state: 'denied', ok: false });
    expect(rows[0].without).toContain('by address');
    expect(rows[1].state).toBe('not asked yet');
    expect(rows[2]).toMatchObject({ state: 'not available in a browser app', ok: null });
    expect(permissionRows('not_needed', 'granted').map((r) => r.ok)).toEqual([true, true, null]);
  });
});
