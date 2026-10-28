// Discovery in the mock (SRV-727, mock/discovery.ts) as the controller answers it (proxy/discovery.py):
// the scan asks first, each device says what it is, macOS's Local Network denial has its fallback,
// one row per box with the cursor, and the devices line up with MOCK_PAIR so the wizard can walk
// each one from Find to Connect. The ssh line parser holds the controller's cases.
import { EventEmitter } from 'node:events';
import { describe, expect, it } from 'vitest';
import { MOCK_LAN, parseSshLine } from '../mock/discovery.ts';
import { MOCK_PAIR, MockFleet } from '../mock/fleet.ts';
import { createMockMiddleware } from '../mock/server.ts';
import { mergeDiscovery, type Discovery, type DiscoveryRow, type FleetNode } from '../src/api/fleet.ts';

const T0 = Date.parse('2026-10-28T09:00:00Z');
const q = (s = '') => new URLSearchParams(s);
const at = (m: MockFleet, ms: number, since?: number) => m.handle('GET', '/v1/fleet/discovery', q(since == null ? '' : `since=${since}`), null, T0 + ms).body as Discovery;
const post = (m: MockFleet, path: string, body: unknown, ms: number) => m.handle('POST', path, q(), body, T0 + ms);
const byIp = (d: Discovery) => Object.fromEntries(d.rows.flatMap((r) => r.addresses.map((a) => [a, r]))) as Record<string, DiscoveryRow>;

describe('discovery in the mock', () => {
  it('Scan asks first: nothing scanned before the consent, the question is data, the /24 only', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 / 1000 });
    const run = post(m, '/v1/fleet/discovery', {}, 0).body as Discovery;
    expect(run.scan).toMatchObject({ state: 'ask', question: 'Scan 192.0.2.0/24 for SSH?', subnet: '192.0.2.0/24', interface: 'en0', total: 0, done: 0, timeout_ms: 300, rate_per_s: 50 });
    const before = at(m, 4000);
    expect(before.mdns.state).toBe('done');
    expect(before.rows.filter((r) => r.sources.includes('scan'))).toEqual([]);
    expect(before.rows.some((r) => r.addresses.includes(MOCK_LAN.nas))).toBe(false);
    for (const body of [{ subnet: '198.51.100.0/24' }, { subnet: '192.0.2.0/24', all: true }, null]) {
      const r = post(m, '/v1/fleet/discovery/scan', body, 4100);
      expect(r.status).toBe(400);
    }
    expect(at(m, 4200).scan.state).toBe('ask');
    const yes = post(m, '/v1/fleet/discovery/scan', { subnet: '192.0.2.0/24' }, 5000);
    expect((yes.body as Discovery).scan).toMatchObject({ state: 'running', total: 254, done: 0, by: 'dashboard session' });
    expect(post(m, '/v1/fleet/discovery/scan', { subnet: '192.0.2.0/24' }, 5100).status).toBe(409);
    // rows arrive with the scan's progress, inside the subnet
    const mid = at(m, 5000 + 1300);
    expect(mid.scan.done).toBe(65);
    expect(Object.keys(byIp(mid))).toContain('192.0.2.63');
    expect(Object.keys(byIp(mid))).not.toContain(MOCK_LAN.nas);
    const done = at(m, 5000 + 6000);
    expect(done.scan).toMatchObject({ state: 'done', done: 254, open: 5 });
    expect(done.rows.filter((r) => r.sources.includes('scan')).every((r) => r.addresses.every((a) => a.startsWith('192.0.2.')))).toBe(true);
    // a new run asks again
    expect((post(m, '/v1/fleet/discovery', {}, 20_000).body as Discovery).scan.state).toBe('ask');
  });

  it('Classified results: a NAS is "Linux host, not a Spark", a bare GX10 "GB10 box, nothing installed" once SSH read its DMI', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 / 1000 });
    post(m, '/v1/fleet/discovery', {}, 0);
    post(m, '/v1/fleet/discovery/scan', { subnet: '192.0.2.0/24' }, 3000);
    const d = byIp(at(m, 9000));
    expect([d[MOCK_LAN.nas].kind, d[MOCK_LAN.nas].title]).toEqual(['host', 'Linux host, not a Spark']);
    expect([d[MOCK_LAN.mac].kind, d[MOCK_LAN.mac].title]).toEqual(['host', 'Host, not a Spark']);
    expect(d[MOCK_LAN.silent]).toMatchObject({ kind: 'unknown', detail: 'port 22 is open but sent no SSH banner' });
    expect(d[MOCK_PAIR.address]).toMatchObject({ kind: 'spark', title: 'Spark with TandemLLM 0.15.0', connect: ['code', 'ssh'] });
    expect(d[MOCK_PAIR.address].detail).toContain('pairing code showing on the node');
    expect(d[MOCK_PAIR.owned]).toMatchObject({ kind: 'owned', title: 'Spark owned by another TandemApp', connect: ['takeover'] });
    const gx = d[MOCK_PAIR.bare];
    expect(gx).toMatchObject({ kind: 'unknown', name: MOCK_LAN.gx10Name, sources: ['mdns', 'scan'] });
    expect(gx.detail).toContain('its name suggests a Spark');
    // the SSH login: not installed, and the DMI it read
    const r = post(m, '/v1/fleet/pair/ssh', { host: MOCK_PAIR.bare, user: 'spark', password: MOCK_PAIR.password }, 10_000);
    expect(r.status).toBe(409);
    expect((r.body as { error: { type: string; message: string } }).error).toMatchObject({ type: 'not_installed' });
    expect((r.body as { error: { message: string } }).error.message).toContain('(ASUS Ascent GX10, a GB10)');
    const after = at(m, 10_000, at(m, 9000).cursor);
    expect(after.full).toBe(false);
    expect(after.rows.map((x) => x.addresses[0])).toEqual([MOCK_PAIR.bare]);
    expect(after.rows[0]).toMatchObject({ kind: 'gb10', title: 'GB10 box, nothing installed', detail: 'ASUS Ascent GX10 · TandemNode is not installed', hw: { label: 'ASUS Ascent GX10', spark: true, source: 'dmi' }, sources: ['mdns', 'scan', 'login'] });
  });

  it('each MOCK_PAIR device walks from Find to Connect', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 / 1000 });
    post(m, '/v1/fleet/discovery', {}, 0);
    post(m, '/v1/fleet/discovery/scan', { subnet: '192.0.2.0/24' }, 3000);
    // the fleet's own LAN node is there, in the fleet
    const fleetRow = at(m, 9000).rows.find((r) => r.fleet === 'desk');
    expect(fleetRow).toMatchObject({ kind: 'spark', connect: [] });
    // 192.0.2.60: by code
    const code = post(m, '/v1/fleet/pair', { address: MOCK_PAIR.address, code: MOCK_PAIR.code }, 10_000);
    expect(code.status).toBe(200);
    const joined = (code.body as FleetNode).id;
    expect(byIp(at(m, 11_000))[MOCK_PAIR.address]).toMatchObject({ fleet: joined, connect: [], detail: 'NVIDIA DGX Spark · TandemLLM 0.15.0 · in this fleet' });
    // 192.0.2.61: ~/.ssh/config, the scan, then SSH with the password
    const cfgRow = byIp(at(m, 9000))[MOCK_PAIR.ssh];
    expect(cfgRow).toMatchObject({ kind: 'unknown', name: MOCK_LAN.configHost, sources: ['scan', 'ssh_config'], connect: ['ssh'] });
    expect(cfgRow.ssh_config).toMatchObject({ alias: MOCK_LAN.configHost, user: 'spark' });
    const hk = post(m, '/v1/fleet/pair/ssh/hostkey', { host: MOCK_PAIR.ssh }, 12_000);
    const ssh = post(m, '/v1/fleet/pair/ssh', { host: MOCK_PAIR.ssh, user: 'spark', password: MOCK_PAIR.password, host_key: (hk.body as { fingerprint: string }).fingerprint }, 12_000);
    expect(ssh.status).toBe(200);
    expect(byIp(at(m, 13_000))[MOCK_PAIR.ssh]).toMatchObject({ kind: 'spark', fleet: (ssh.body as FleetNode).id, connect: [] });
    // 192.0.2.62: owned elsewhere, and pairing says so
    expect(byIp(at(m, 13_000))[MOCK_PAIR.owned].kind).toBe('owned');
    expect(post(m, '/v1/fleet/pair', { address: MOCK_PAIR.owned, code: '123456' }, 13_000).body).toMatchObject({ error: { type: 'owned' } });
    // the rented node of ~/.ssh/config: its ssh line
    const rented = at(m, 13_000).rows.find((r) => r.ssh_config?.alias === 'rented-east')!;
    expect(rented.ssh_config).toMatchObject({ hostname: MOCK_LAN.rented, port: 2201, proxy_jump: 'hop@198.51.100.1' });
    const t = post(m, '/v1/fleet/discovery/target', { line: `ssh -p ${rented.ssh_config!.port} -J ${rented.ssh_config!.proxy_jump} ${rented.ssh_config!.user}@${rented.ssh_config!.hostname}` }, 13_000);
    expect((t.body as { target: unknown }).target).toMatchObject({ host: MOCK_LAN.rented, port: 2201, user: 'spark', remote: true, route: [{ kind: 'jump', host: '198.51.100.1' }, { kind: 'node', host: MOCK_LAN.rented }] });
  });

  it('Local Network denied: the API says so, the scan is refused, add by address works', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 / 1000 });
    expect(at(m, 0).local_network.state).toBe('not_asked');
    m.discovery.lanMode = 'deny';
    const d = post(m, '/v1/fleet/discovery', {}, 0).body as Discovery;
    expect(d.local_network.state).toBe('denied');
    expect(d.local_network.settings).toContain('Local Network');
    expect(d.mdns.state).toBe('denied');
    expect(d.scan.state).toBe('denied');
    expect(at(m, 4000).rows.map((r) => r.ssh_config?.alias)).toEqual(['lab-spark', 'rented-east']);
    expect(post(m, '/v1/fleet/discovery/scan', { subnet: '192.0.2.0/24' }, 4000).body).toMatchObject({ error: { type: 'lan_denied' } });
    const addr = post(m, '/v1/fleet/discovery/address', { address: MOCK_PAIR.ssh }, 5000);
    expect(addr.status).toBe(200);
    expect((addr.body as { row: DiscoveryRow }).row).toMatchObject({ addresses: [MOCK_PAIR.ssh], sources: ['ssh_config', 'address'] });
    const unknown = post(m, '/v1/fleet/discovery/address', { address: '192.0.2.99' }, 5000);
    expect((unknown.body as { row: DiscoveryRow }).row).toMatchObject({ kind: 'unknown', detail: 'no SSH server answers there' });
    expect(post(m, '/v1/fleet/discovery/address', { address: 'spark.local' }, 5000).status).toBe(400);
    // allowed again: the next run says granted; a controller on a Spark needs no permission
    m.discovery.lanMode = 'allow';
    expect((post(m, '/v1/fleet/discovery', {}, 6000).body as Discovery).local_network.state).toBe('granted');
    const one = new MockFleet({ scenario: 'fleet1', now: T0 / 1000 });
    expect((post(one, '/v1/fleet/discovery', {}, 0).body as Discovery).local_network).toMatchObject({ state: 'not_needed', settings: null });
  });

  it('one row per box; the cursor and mergeDiscovery give the same list as a full read', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 / 1000 });
    let list = mergeDiscovery(null, post(m, '/v1/fleet/discovery', {}, 0).body as Discovery);
    for (let ms = 250; ms <= 9000; ms += 250) {
      if (ms === 3000) list = mergeDiscovery(list, post(m, '/v1/fleet/discovery/scan', { subnet: '192.0.2.0/24' }, ms).body as Discovery);
      list = mergeDiscovery(list, at(m, ms, list.cursor));
    }
    const full = at(m, 9000);
    expect(list.rows.map((r) => [r.id, r.seq])).toEqual(full.rows.map((r) => [r.id, r.seq]));
    const ips = full.rows.flatMap((r) => r.addresses);
    expect(new Set(ips).size).toBe(ips.length);
    expect(new Set(full.rows.map((r) => r.id)).size).toBe(full.rows.length);
    // a poll right after a full read brings nothing new
    expect(at(m, 9000, full.cursor + 1).rows).toEqual([]);
    // a cursor from an earlier run is a full answer
    post(m, '/v1/fleet/discovery', {}, 10_000);
    expect(at(m, 10_500, full.cursor).full).toBe(true);
  });

  it('the mock server: discovery wants a session with the fleet password, ?lan=deny sets the cookie', async () => {
    const mw = createMockMiddleware({ live: false });
    function call(method: string, url: string, cookie = '', body?: unknown): Promise<{ status: number; headers: Record<string, string | string[]>; body: unknown }> {
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
          end: (s?: string) => resolve({ status, headers, body: s ? JSON.parse(s) : null }),
        };
        mw(req as never, res as never, () => resolve({ status, headers, body: null }));
        queueMicrotask(() => {
          if (body !== undefined) req.emit('data', JSON.stringify(body));
          req.emit('end');
        });
      });
    }
    const page = await call('GET', '/dashboard/?lan=deny&mock=nologin');
    expect(String(page.headers['set-cookie'])).toContain('qse_lan=deny');
    const denied = await call('POST', '/v1/fleet/discovery', 'qse_lan=deny', {});
    expect((denied.body as Discovery).local_network.state).toBe('denied');
    await call('POST', '/__mock/mode', '', { fleetPassword: true });
    const locked = await call('GET', '/v1/fleet/discovery');
    expect(locked.status).toBe(401);
    expect((await call('GET', '/v1/fleet/nodes')).status).toBe(200);
    await call('POST', '/__mock/mode', '', {});
    expect((await call('POST', '/__mock/fleet', '', { lan: 'sideways' })).status).toBe(400);
  });
});

describe('the ssh line (the controller cases of tests/test_discovery.py)', () => {
  it('reads -p, -l, -i, -J, -o and ssh://, the first value winning, and the route', () => {
    let t = parseSshLine('ssh -p 2201 spark@198.51.100.7');
    expect([t.user, t.host, t.port, t.remote, t.jumps]).toEqual(['spark', '198.51.100.7', 2201, false, []]);
    expect(t.route).toEqual([{ kind: 'node', user: 'spark', host: '198.51.100.7', port: 2201 }]);
    t = parseSshLine('ssh -i ~/.ssh/rent_key -J hop@203.0.113.9:2200,[2001:db8::1]:22 -p2201 spark@node7.rent.example');
    expect([t.identity_file, t.port, t.remote]).toEqual(['~/.ssh/rent_key', 2201, true]);
    expect(t.jumps).toEqual([
      { user: 'hop', host: '203.0.113.9', port: 2200 },
      { user: null, host: '2001:db8::1', port: 22 },
    ]);
    expect(t.route.map((r) => r.kind)).toEqual(['jump', 'jump', 'node']);
    t = parseSshLine('ssh://spark@198.51.100.7:2201');
    expect([t.user, t.host, t.port]).toEqual(['spark', '198.51.100.7', 2201]);
    t = parseSshLine("-o Port=2202 -o 'User spark' -o ProxyJump=hop@198.51.100.1 -l other -L 8000:localhost:8000 -A 198.51.100.7 uptime");
    expect([t.user, t.port, t.jumps[0].host]).toEqual(['spark', 2202, '198.51.100.1']);
    expect(t.warnings).toContain('-L 8000:localhost:8000 is not used');
    expect(t.warnings).toContain('the command after the host is not used');
    expect(parseSshLine('ssh spark@lab-a.local').remote).toBe(false);
  });

  it('refuses what it cannot take', () => {
    for (const bad of ["ssh -o ProxyCommand='nc %h %p' h", 'ssh -o LocalCommand=x h', 'ssh -p', 'ssh', 'ssh -p 70000 h', "ssh 'a b'@h", 'ssh -Z h', 'ssh h;rm', "ssh -J 'x y' h", 'ssh -p two h', '', "ssh 'unclosed"]) {
      expect(() => parseSshLine(bad), bad).toThrow();
    }
  });
});
