// Discovery in the mock (`/v1/fleet/discovery*`, docs/contract/fleet-v1, SRV-727), as the controller
// answers it (proxy/discovery.py): a run browses mDNS for 3 s and reads ~/.ssh/config, the scan asks
// "Scan 192.0.2.0/24 for SSH?" and runs only after the consent, at 50 addresses a second, and each
// device says what it is. Rows appear on the clock: poll with the cursor (here tenths of a second,
// so a row that changed in the poll's own tenth comes again; the page merges by id).
//
// The devices line up with MOCK_PAIR (mock/fleet.ts), so the wizard can walk each one end to end:
//   192.0.2.60  mDNS TandemNode, not paired, a pairing code showing (482913)    → Connect by code
//   192.0.2.61  ~/.ssh/config `lab-spark`, then the scan: Ubuntu 24.04, unknown → SSH, "spark-pass"
//   192.0.2.62  mDNS TandemNode owned by another TandemApp                       → take-over
//   192.0.2.63  mDNS `_ssh._tcp` gx10-7f3a, then the scan: Ubuntu 24.04; after the SSH login (409
//               not_installed) its DMI says "GB10 box, nothing installed"       → Install (5.3)
//   192.0.2.70  the scan: a NAS (dropbear)          "Linux host, not a Spark"
//   192.0.2.71  the scan: a Mac (OpenSSH 9.8)       "Host, not a Spark"
//   192.0.2.80  the scan: port 22 open, no banner   unknown
//   rented-east ~/.ssh/config, 198.51.100.7 through a jump host                 → the ssh line
// plus every LAN node of the scenario, "in this fleet". The controller of fleet4 runs on a Mac:
// Local Network starts `not_asked` and the first run asks; `qse_lan=deny` (?lan=deny on the page)
// or POST /__mock/fleet {lan: 'deny'} makes macOS refuse it.

import type { Discovery, DiscoveryRow, FleetNode, LocalNetwork, SshTarget } from '../src/api/fleet.ts';
import { FLEET_CONTRACT } from '../src/api/fleet.ts';

type Reply = { status: number; body: unknown };
const bad = (status: number, type: string, message: string): Reply => ({ status, body: { error: { type, message } } });

export const MOCK_LAN = { subnet: '192.0.2.0/24', nas: '192.0.2.70', mac: '192.0.2.71', silent: '192.0.2.80', gx10Name: 'gx10-7f3a', configHost: 'lab-spark', rented: '198.51.100.7' } as const;
const BROWSE_MS = 3000;
const SCAN_GAP_MS = 20; // 50 a second
const UBUNTU = 'SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.5';
const KINDS = ['spark', 'gb10', 'host', 'owned', 'unknown'];
const SETTINGS = 'System Settings → Privacy & Security → Local Network';
const LAN_TEXT: Record<LocalNetwork, string> = {
  not_needed: 'this machine asks no permission for the local network',
  not_asked: 'macOS asks once, at the first search: allow it to find Sparks',
  granted: 'allowed: this app can search the local network',
  denied: `macOS keeps this app off the local network: Find lists nothing it would hear there; add by address still works. Turn it on in ${SETTINGS}`,
};
const hostKey = (seed: string) => `SHA256:${seed.padEnd(43, 'x').slice(0, 43)}`;

type Row = Omit<DiscoveryRow, 'seq' | 'seen_at'>;
const row = (id: string, p: Partial<Row> & Pick<Row, 'kind' | 'title' | 'detail' | 'addresses' | 'sources'>): Row => ({
  id,
  name: null,
  node: null,
  hw: null,
  ssh: null,
  ssh_config: null,
  fleet: null,
  connect: [],
  evidence: [],
  ...p,
});
const tn = (id: string, extra: Partial<NonNullable<Row['node']>> = {}): NonNullable<Row['node']> => ({ id, class: 'spark', version: '0.15.0', hw: 'NVIDIA DGX Spark', paired: false, owner: null, listen: 'loopback', window: null, port: 7700, virtual: false, ...extra });

export class MockDiscovery {
  /** what macOS answers this controller's first browse */
  lanMode: 'allow' | 'deny' = 'allow';
  private run = 0;
  private runAt = 0;
  private denied = false;
  private lan: LocalNetwork;
  private scanAt: number | null = null;
  private scanStop: number | null = null;
  private scanBy: string | null = null;
  private learned = new Map<string, { at: number; what: 'gx10' }>();
  private addressed = new Map<string, number>();

  constructor(private o: { onMac: boolean; host: string; nodes: () => Pick<FleetNode, 'id' | 'alias' | 'address' | 'transport' | 'version' | 'hw' | 'since'>[] }) {
    this.lan = o.onMac ? 'not_asked' : 'not_needed';
  }

  private get iface() {
    return { name: this.o.onMac ? 'en0' : 'enP7s7', address: this.o.host, subnet: MOCK_LAN.subnet };
  }

  /** what a pairing found out (mock/fleet.ts): the SSH login read a GX10's DMI */
  learn(address: string, what: 'gx10', nowMs: number): void {
    this.learned.set(address, { at: nowMs, what });
  }

  // ---- the rows on the clock ----------------------------------------------------------------
  private scanned(ip: string): number | null {
    if (this.scanAt == null) return null;
    const at = this.scanAt + Number(ip.split('.')[3]) * SCAN_GAP_MS;
    return this.scanStop != null && at > this.scanStop ? null : at;
  }

  /** each device's phases [(when, content)] in the order each merges the ones before it (mDNS, the
   *  scan, a login): the last one that has happened wins, whichever came first on the clock */
  private phases(): [number, Row][][] {
    const t = this.runAt;
    const mdns = !this.denied && this.run > 0;
    const fleetOf = (ip: string) => this.o.nodes().find((n) => n.address.startsWith(`${ip}:`));
    const out: [number, Row][][] = [];
    // the fleet's own LAN nodes
    for (const n of this.o.nodes()) {
      const ip = n.address.split(':')[0];
      if (n.transport !== 'lan' || !mdns) continue;
      out.push([[t + 400, row(`d-fleet-${n.id}`, { kind: 'spark', title: `Spark with TandemLLM ${n.version}`, detail: `${n.hw} · TandemLLM ${n.version} · in this fleet`, name: n.alias, addresses: [ip], sources: ['mdns'], node: tn(n.id, { hw: n.hw, paired: true, owner: 'this', listen: 'lan' }), hw: { label: n.hw, spark: true, source: 'mdns' }, fleet: n.id, evidence: [`mDNS: TandemNode ${n.id}, TandemLLM ${n.version}, ${n.hw}, paired`] })]]);
    }
    // 192.0.2.60: a TandemNode showing a pairing code
    {
      const ip = '192.0.2.60';
      const ph: [number, Row][] = [];
      const base = { name: 'spark-lab', addresses: [ip], sources: ['mdns'] as Row['sources'], node: tn('n-mock0000060', { window: 7701 }), hw: { label: 'NVIDIA DGX Spark', spark: true, source: 'mdns' as const }, evidence: ['mDNS: TandemNode n-mock0000060, TandemLLM 0.15.0, NVIDIA DGX Spark, not paired'] };
      if (mdns) ph.push([t + 800, row('d-mock60', { ...base, kind: 'spark', title: 'Spark with TandemLLM 0.15.0', detail: 'NVIDIA DGX Spark · TandemLLM 0.15.0 · pairing code showing on the node', connect: ['code', 'ssh'] })]);
      const f = fleetOf(ip);
      if (mdns && f) ph.push([Math.max(t + 800, f.since * 1000), row('d-mock60', { ...base, node: tn('n-mock0000060', { paired: true, owner: 'this', listen: 'lan' }), kind: 'spark', title: 'Spark with TandemLLM 0.15.0', detail: 'NVIDIA DGX Spark · TandemLLM 0.15.0 · in this fleet', fleet: f.id })]);
      out.push(ph);
    }
    // 192.0.2.61: ~/.ssh/config, then the scan, then SSH once
    {
      const ip = '192.0.2.61';
      const cfg = { alias: MOCK_LAN.configHost, hostname: ip, user: 'spark', port: 22, identity_file: null, proxy_jump: null };
      const ph: [number, Row][] = [];
      if (this.run > 0) ph.push([t + 100, row('d-mock61', { kind: 'unknown', title: 'Unknown device', detail: `from ~/.ssh/config (${cfg.alias}): not checked`, name: cfg.alias, addresses: [ip], sources: ['ssh_config'], ssh: { port: 22, banner: null, os: null, host_key: hostKey('mock61') }, ssh_config: cfg, connect: ['ssh'], evidence: [`~/.ssh/config: Host ${cfg.alias}`] })]);
      const s = this.scanned(ip);
      const scanRow = (extra: Partial<Row>) =>
        row('d-mock61', { kind: 'unknown', title: 'Unknown device', detail: 'Ubuntu 24.04, as DGX OS: maybe a Spark; sign in once over SSH to tell', name: cfg.alias, addresses: [ip], sources: ['scan', 'ssh_config'], ssh: { port: 22, banner: UBUNTU, os: 'Ubuntu 24.04', host_key: hostKey('mock61') }, ssh_config: cfg, connect: ['ssh'], evidence: [`SSH banner: ${UBUNTU} (Ubuntu 24.04)`, `~/.ssh/config: Host ${cfg.alias}`], ...extra });
      if (s != null) ph.push([s, scanRow({})]);
      const f = fleetOf(ip);
      if (f && this.run > 0) ph.push([Math.max(t + 100, f.since * 1000), scanRow({ kind: 'spark', title: 'Spark with TandemLLM', detail: 'NVIDIA DGX Spark · in this fleet', sources: s != null ? ['scan', 'ssh_config', 'login'] : ['ssh_config', 'login'], hw: { label: 'NVIDIA DGX Spark', spark: true, source: 'dmi' }, fleet: f.id, connect: [], evidence: ['DMI over SSH: NVIDIA · DGX Spark · DGX Spark'] })]);
      out.push(ph);
    }
    // 192.0.2.62: owned by another TandemApp
    if (mdns) {
      out.push([[t + 1200, row('d-mock62', { kind: 'owned', title: 'Spark owned by another TandemApp', detail: 'ASUS Ascent GX10 · TandemLLM 0.14.2 · take it over on the node, or over SSH', name: 'spark-annex', addresses: ['192.0.2.62'], sources: ['mdns'], node: tn('n-mock0000062', { version: '0.14.2', hw: 'ASUS Ascent GX10', paired: true, owner: 'other', listen: 'lan' }), hw: { label: 'ASUS Ascent GX10', spark: true, source: 'mdns' }, connect: ['takeover'], evidence: ['mDNS: TandemNode n-mock0000062, TandemLLM 0.14.2, ASUS Ascent GX10, paired', 'pairing: owned by another TandemApp'] })]]);
    }
    // 192.0.2.63: a bare GX10
    {
      const ip = '192.0.2.63';
      const ph: [number, Row][] = [];
      const name = MOCK_LAN.gx10Name;
      const ev = [`mDNS: _ssh ${name}`];
      if (mdns) ph.push([t + 1600, row('d-mock63', { kind: 'unknown', title: 'Unknown device', detail: 'announces itself over mDNS; sign in once over SSH to tell · its name suggests a Spark', name, addresses: [ip], sources: ['mdns'], ssh: { port: 22, banner: null, os: null, host_key: null }, connect: ['ssh'], evidence: ev })]);
      const s = this.scanned(ip);
      const src: Row['sources'] = mdns ? ['mdns', 'scan'] : ['scan'];
      const ssh = { port: 22, banner: UBUNTU, os: 'Ubuntu 24.04', host_key: null };
      if (s != null) ph.push([s, row('d-mock63', { kind: 'unknown', title: 'Unknown device', detail: 'Ubuntu 24.04, as DGX OS: maybe a Spark; sign in once over SSH to tell · its name suggests a Spark', name: mdns ? name : null, addresses: [ip], sources: src, ssh, connect: ['ssh'], evidence: [`SSH banner: ${UBUNTU} (Ubuntu 24.04)`, ...(mdns ? ev : [])] })]);
      const l = this.learned.get(ip);
      if (l && this.run > 0) {
        ph.push([l.at, row('d-mock63', { kind: 'gb10', title: 'GB10 box, nothing installed', detail: 'ASUS Ascent GX10 · TandemNode is not installed', name: mdns ? name : null, addresses: [ip], sources: [...(mdns ? (['mdns'] as const) : []), ...(s != null ? (['scan'] as const) : []), 'login'], ssh: { ...ssh, banner: s != null ? UBUNTU : null, os: s != null ? 'Ubuntu 24.04' : null, host_key: hostKey('mock63') }, hw: { label: 'ASUS Ascent GX10', spark: true, source: 'dmi' }, connect: ['ssh'], evidence: ['DMI over SSH: ASUSTeK COMPUTER INC. · GX10 · DGX Spark'] })]);
      }
      out.push(ph);
    }
    // the rest of the scan
    const scanOnly: [string, string | null, Partial<Row> & Pick<Row, 'kind' | 'title' | 'detail'>][] = [
      [MOCK_LAN.nas, 'SSH-2.0-dropbear_2022.83', { kind: 'host', title: 'Linux host, not a Spark', detail: 'Linux (dropbear) (its SSH banner): DGX OS is Ubuntu 24.04 or later' }],
      [MOCK_LAN.mac, 'SSH-2.0-OpenSSH_9.8', { kind: 'host', title: 'Host, not a Spark', detail: 'OpenSSH 9.8 (its SSH banner): DGX OS is Ubuntu 24.04 or later' }],
      [MOCK_LAN.silent, null, { kind: 'unknown', title: 'Unknown device', detail: 'port 22 is open but sent no SSH banner', connect: ['ssh'] }],
    ];
    for (const [ip, banner, p] of scanOnly) {
      const s = this.scanned(ip);
      if (s == null) continue;
      const os = banner == null ? null : banner.includes('dropbear') ? 'Linux (dropbear)' : 'OpenSSH 9.8';
      out.push([[s, row(`d-mock${ip.split('.')[3]}`, { addresses: [ip], sources: ['scan'], ssh: { port: 22, banner, os, host_key: null }, evidence: banner ? [`SSH banner: ${banner} (${os})`] : [], ...p })]]);
    }
    // a rented node in ~/.ssh/config
    if (this.run > 0) {
      const cfg = { alias: 'rented-east', hostname: MOCK_LAN.rented, user: 'spark', port: 2201, identity_file: '~/.ssh/rent_ed25519', proxy_jump: 'hop@198.51.100.1' };
      out.push([[t + 100, row('d-rented-east', { kind: 'unknown', title: 'Unknown device', detail: `from ~/.ssh/config (rented-east) via ${cfg.proxy_jump}: not checked`, name: cfg.alias, addresses: [cfg.hostname], sources: ['ssh_config'], ssh: { port: 2201, banner: null, os: null, host_key: null }, ssh_config: cfg, connect: ['ssh'], evidence: [`~/.ssh/config: Host rented-east (via ${cfg.proxy_jump})`] })]]);
    }
    // add by address: the row says the address was checked
    for (const [ip, at] of this.addressed) {
      const known = out.find((ph) => ph.some(([, r]) => r.addresses.includes(ip)));
      if (known) {
        const last = [...known].reverse().find(([w]) => w <= at) ?? known[0];
        known.push([at, { ...last[1], sources: [...new Set([...last[1].sources, 'address' as const])] }]);
      } else {
        out.push([[at, row(`d-addr-${ip}`, { kind: 'unknown', title: 'Unknown device', detail: 'no SSH server answers there', addresses: [ip], sources: ['address'] })]]);
      }
    }
    return out;
  }

  private rows(nowMs: number): DiscoveryRow[] {
    const out: DiscoveryRow[] = [];
    for (const ph of this.phases()) {
      const seen = ph.filter(([at]) => at <= nowMs);
      if (!seen.length) continue;
      const [at, r] = seen[seen.length - 1];
      out.push({ ...r, seen_at: Math.round(at) / 1000, seq: Math.floor(at / 100) });
    }
    const ip = (r: DiscoveryRow) => (r.addresses[0] ?? '999').split('.').map((x) => x.padStart(3, '0')).join('.');
    return out.sort((a, b) => KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind) || ip(a).localeCompare(ip(b)) || (a.name ?? '').localeCompare(b.name ?? '') || a.id.localeCompare(b.id));
  }

  snapshot(nowMs: number, since: number | null = null): Discovery {
    const rows = this.rows(nowMs);
    const full = since == null || since < Math.floor(this.runAt / 100);
    const scanEnd = this.scanAt == null ? null : this.scanStop ?? this.scanAt + 254 * SCAN_GAP_MS;
    const done = this.scanAt == null ? 0 : Math.max(0, Math.min(254, Math.floor((Math.min(nowMs, scanEnd as number) - this.scanAt) / SCAN_GAP_MS)));
    const open = ['61', '63', '70', '71', '80'].filter((x) => Number(x) <= done).length;
    const iface = this.iface;
    const scanState: Discovery['scan']['state'] = this.denied ? 'denied' : this.scanAt == null ? 'ask' : nowMs < (scanEnd as number) ? 'running' : this.scanStop != null ? 'stopped' : 'done';
    const browsing = this.run > 0 && !this.denied && nowMs < this.runAt + BROWSE_MS;
    return {
      contract_version: FLEET_CONTRACT,
      run: this.run,
      cursor: Math.floor(nowMs / 100),
      full,
      rows: full ? rows : rows.filter((r) => r.seq >= (since as number)),
      gone: [],
      mdns: {
        state: this.run === 0 ? 'idle' : this.denied ? 'denied' : browsing ? 'browsing' : 'done',
        types: ['_tandemnode._tcp.local', '_ssh._tcp.local', '_workstation._tcp.local'],
        started_at: this.run ? this.runAt / 1000 : null,
        ended_at: this.run && !browsing ? (this.denied ? this.runAt : this.runAt + BROWSE_MS) / 1000 : null,
        found: rows.filter((r) => r.sources.includes('mdns')).length,
        note: this.denied ? 'macOS keeps this app off the local network' : null,
      },
      scan: {
        state: scanState,
        subnet: iface.subnet,
        interface: iface.name,
        question: `Scan ${iface.subnet} for SSH?`,
        total: this.scanAt == null ? 0 : 254,
        done,
        open,
        started_at: this.scanAt == null ? null : this.scanAt / 1000,
        ended_at: scanState === 'done' || scanState === 'stopped' ? (scanEnd as number) / 1000 : null,
        rate_per_s: 1000 / SCAN_GAP_MS,
        timeout_ms: 300,
        by: this.scanBy,
      },
      ssh_config: this.run ? { state: 'read', hosts: 2, note: null } : { state: 'idle', hosts: 0, note: null },
      local_network: { state: this.lan, text: LAN_TEXT[this.lan], settings: this.o.onMac ? SETTINGS : null },
      interfaces: [iface],
    };
  }

  // ---- the routes ------------------------------------------------------------------------------
  handle(method: string, parts: string[], q: URLSearchParams, body: unknown, nowMs: number): Reply {
    const sub = parts.slice(1).join('/');
    const b = body as Record<string, unknown> | null;
    if (sub === '' && method === 'GET') {
      const s = q.get('since');
      if (s != null && !/^-?\d+$/.test(s)) return bad(400, 'bad_request', `since is a cursor (a whole number), not "${s}"`);
      return { status: 200, body: this.snapshot(nowMs, s == null ? null : Number(s)) };
    }
    if (sub === '' && method === 'POST') {
      if (b != null && (typeof b !== 'object' || Array.isArray(b) || Object.keys(b).some((k) => k !== 'interface'))) return bad(400, 'bad_request', 'the body is {interface?}');
      if (b?.interface != null && b.interface !== this.iface.name) return bad(400, 'bad_request', `no LAN interface '${String(b.interface)}' (one of ${this.iface.name})`);
      this.run += 1;
      this.runAt = nowMs;
      this.scanAt = this.scanStop = this.scanBy = null;
      this.addressed.clear();
      if (this.o.onMac) this.lan = this.lanMode === 'deny' ? 'denied' : 'granted';
      this.denied = this.lan === 'denied';
      return { status: 200, body: this.snapshot(nowMs) };
    }
    if (sub === 'scan' && method === 'POST') {
      if (!b || typeof b !== 'object' || Object.keys(b).length !== 1 || typeof b.subnet !== 'string') return bad(400, 'bad_request', 'the body is {subnet}: the subnet the question names');
      const st = this.snapshot(nowMs).scan.state;
      if (st === 'running') return bad(409, 'busy', `a scan of ${MOCK_LAN.subnet} is running`);
      if (this.denied || this.lan === 'denied') return bad(409, 'lan_denied', `macOS keeps this app off the local network: turn it on in ${SETTINGS}, then find again`);
      if (b.subnet !== MOCK_LAN.subnet) return bad(400, 'bad_request', `the consent names ${b.subnet}; the question is about ${MOCK_LAN.subnet}`);
      this.scanAt = nowMs;
      this.scanStop = null;
      this.scanBy = 'dashboard session';
      return { status: 200, body: this.snapshot(nowMs) };
    }
    if (sub === 'scan' && method === 'DELETE') {
      if (this.snapshot(nowMs).scan.state === 'running') this.scanStop = nowMs;
      return { status: 200, body: this.snapshot(nowMs) };
    }
    if (sub === 'address' && method === 'POST') {
      if (!b || typeof b !== 'object' || !('address' in b) || Object.keys(b).some((k) => k !== 'address' && k !== 'port')) return bad(400, 'bad_request', 'the body is {address, port?}');
      const a = String(b.address);
      if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(a) || a.split('.').some((x) => Number(x) > 255)) return bad(400, 'bad_request', `address is an IPv4 address, not "${a}"`);
      if ('port' in b && (typeof b.port !== 'number' || !Number.isInteger(b.port) || b.port < 1 || b.port > 65535)) return bad(400, 'bad_request', 'port is a whole number from 1 to 65535');
      if (a.startsWith('127.') || a === '0.0.0.0') return bad(400, 'bad_request', `${a} is no device's address`);
      this.addressed.set(a, nowMs);
      const r = this.rows(nowMs).find((x) => x.addresses.includes(a));
      return { status: 200, body: { contract_version: FLEET_CONTRACT, row: r } };
    }
    if (sub === 'target' && method === 'POST') {
      if (!b || typeof b !== 'object' || Object.keys(b).length !== 1 || typeof b.line !== 'string') return bad(400, 'bad_request', 'the body is {line}: an ssh command');
      try {
        return { status: 200, body: { contract_version: FLEET_CONTRACT, target: parseSshLine(b.line) } };
      } catch (e) {
        return bad(400, 'bad_request', (e as Error).message);
      }
    }
    return bad(404, 'not_found', `no route ${method} /v1/fleet/${parts.join('/')}`);
  }
}

// ---- the ssh line, as proxy/discovery.py parse_ssh_line reads it -----------------------------------

const NO_ARG = new Set('46AaCfGgKkMNnqsTtVvXxYy');
const WITH_ARG = new Set('BbcDEeFIiJLlmOoPpQRSWw');
const REFUSED_O = ['proxycommand', 'localcommand', 'permitlocalcommand', 'knownhostscommand', 'remotecommand', 'controlmaster', 'controlpath', 'sendenv', 'setenv'];
const USER_OK = /^[A-Za-z0-9._-]{1,64}$/;
const hostOk = (h: string) => !!h && h.length <= 253 && !h.startsWith('-') && !/[@/\\'"`$;|&<>\s]/.test(h);

/** shell-like words: single and double quotes, backslash escapes */
function words(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let has = false;
  let q: string | null = null;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) {
      if (c === q) q = null;
      else if (c === '\\' && q === '"' && i + 1 < line.length) cur += line[++i];
      else cur += c;
    } else if (c === "'" || c === '"') {
      q = c;
      has = true;
    } else if (c === '\\' && i + 1 < line.length) {
      cur += line[++i];
      has = true;
    } else if (/\s/.test(c)) {
      if (has || cur) out.push(cur);
      cur = '';
      has = false;
    } else cur += c;
  }
  if (q) throw new Error('the line does not parse: No closing quotation');
  if (has || cur) out.push(cur);
  return out;
}

function hop(spec: string): { user: string | null; host: string; port: number } {
  const at = spec.lastIndexOf('@');
  const user = at >= 0 ? spec.slice(0, at) : '';
  const rest = at >= 0 ? spec.slice(at + 1) : spec;
  const m = /^\[([0-9a-fA-F:.]+)\](?::(\d+))?$/.exec(rest) ?? /^([^:[\]]+)(?::(\d+))?$/.exec(rest);
  if (!m || !hostOk(m[1])) throw new Error(`jump host '${spec}' is not [user@]host[:port]`);
  if (user && !USER_OK.test(user)) throw new Error(`jump host: '${user}' is not a login name`);
  const port = m[2] ? Number(m[2]) : 22;
  if (port < 1 || port > 65535) throw new Error(`jump host: port ${port} is not 1 to 65535`);
  return { user: user || null, host: m[1], port };
}

export function parseSshLine(line: string): SshTarget {
  if (typeof line !== 'string' || !line.trim() || line.length > 2000) throw new Error('the line is an ssh command, up to 2000 characters');
  let argv = words(line.trim());
  if (argv.length && argv[0].split('/').pop() === 'ssh') argv = argv.slice(1);
  let port: string | null = null;
  let user: string | null = null;
  let key: string | null = null;
  let jump: string | null = null;
  const warnings: string[] = [];
  let dest: string | null = null;
  const rest: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (dest != null) {
      rest.push(a);
      continue;
    }
    if (a.startsWith('-') && a.length > 1) {
      for (let j = 1; j < a.length; j++) {
        const c = a[j];
        if (NO_ARG.has(c)) continue;
        if (!WITH_ARG.has(c)) throw new Error(`-${c} is not an ssh option`);
        let val = j + 1 < a.length ? a.slice(j + 1) : null;
        if (val == null) {
          if (++i >= argv.length) throw new Error(`-${c} needs a value`);
          val = argv[i];
        }
        if (c === 'p') port ??= val;
        else if (c === 'l') user ??= val;
        else if (c === 'i') key ??= val;
        else if (c === 'J') jump ??= val;
        else if (c === 'o') {
          let [k, v] = val.includes('=') ? [val.slice(0, val.indexOf('=')), val.slice(val.indexOf('=') + 1)] : [val.split(' ')[0], val.split(' ').slice(1).join(' ')];
          k = k.trim().toLowerCase();
          v = v.trim();
          if (REFUSED_O.includes(k)) throw new Error(`-o ${k}: an option that runs a command or changes how connections are shared is not accepted (use -J for a jump host)`);
          if (k === 'port') port ??= v;
          else if (k === 'user') user ??= v;
          else if (k === 'identityfile') key ??= v;
          else if (k === 'proxyjump') jump ??= v;
          else warnings.push(`-o ${k} is not used`);
        } else warnings.push(`-${c} ${val} is not used`);
        break;
      }
      continue;
    }
    dest = a;
  }
  if (dest == null) throw new Error('no host: the line is ssh [options] [user@]host');
  let host: string;
  const m = /^ssh:\/\/(?:([^@/]+)@)?(\[[0-9a-fA-F:.]+\]|[^:/]+)(?::(\d+))?\/?$/.exec(dest);
  if (m) {
    user ??= m[1] ?? null;
    host = m[2].replace(/^\[|\]$/g, '');
    port ??= m[3] ?? null;
  } else {
    const at = dest.lastIndexOf('@');
    host = at >= 0 ? dest.slice(at + 1) : dest;
    if (at > 0) user ??= dest.slice(0, at);
  }
  if (!hostOk(host)) throw new Error(`'${host}' is not a host name or address`);
  if (user != null && !USER_OK.test(user)) throw new Error(`'${user}' is not a login name`);
  const p = port == null ? 22 : Number(port);
  if (!Number.isInteger(p)) throw new Error(`port '${port}' is not a number`);
  if (p < 1 || p > 65535) throw new Error(`port ${p} is not 1 to 65535`);
  const jumps = jump == null || jump === 'none' ? [] : jump.split(',').map((s) => s.trim()).filter(Boolean).map(hop);
  if (rest.length) warnings.push('the command after the host is not used');
  const v4 = /^\d+\.\d+\.\d+\.\d+$/.test(host);
  const priv = v4 && /^(10\.|127\.|169\.254\.|192\.168\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|172\.(1[6-9]|2\d|3[01])\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)/.test(host);
  const remote = v4 ? !priv : host.includes(':') ? !/^(fe80|fc|fd|::1)/i.test(host) : !host.toLowerCase().endsWith('.local') && host.includes('.');
  return {
    user,
    host,
    port: p,
    identity_file: key,
    jumps,
    route: [...jumps.map((h) => ({ kind: 'jump' as const, ...h })), { kind: 'node' as const, user, host, port: p }],
    remote: remote || jumps.length > 0,
    warnings,
  };
}
