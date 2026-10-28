// The Add Spark wizard's session in the mock (`/v1/fleet/add*`, docs/contract/fleet-v1, VIS-246), as the
// controller answers it (proxy/addspark.py): one active session, targets from discovery's rows or an ssh
// line, Connect through the mock's own pairing routes (mock/fleet.ts), Check with the controller's
// checks (proxy/preflight.py, ported below) over canned facts per device, Install as jobs on the clock,
// Link, Name and Done. Never a password or a key in an answer: `secret.held` says whether one is held.
//
// The devices (mock/discovery.ts, MOCK_PAIR):
//   192.0.2.60  code 482913 → paired, TandemLLM 0.15.0: Check passes, Install is skipped
//   192.0.2.61  SSH spark / spark-pass → paired (TandemNode was there): the same
//   192.0.2.62  owned by another TandemApp → take over over SSH → it runs 0.14.2: Check's TandemLLM
//               row is red, Install refuses it
//   192.0.2.63  bare GX10, SSH spark / spark-pass → 409 not_installed → bare; the first Check finds its
//               clock off (red, its fix named), Check again finds it fixed; Install about 20 s
//   rented-east 198.51.100.7:2201 (ssh line + a key, a lease end) → bare rental → Install → joins
//               through the tunnel with its lease; the lease rule's reminder in the session

import type { AddSession, AddTarget, InstallStep, InstallView, PreflightRow } from '../src/api/fleet.ts';
import { FLEET_CONTRACT } from '../src/api/fleet.ts';
import { MOCK_LAN, parseSshLine } from './discovery.ts';
import type { MockFleet } from './fleet.ts';

type Reply = { status: number; body: unknown };
const bad = (status: number, type: string, message: string): Reply => ({ status, body: { error: { type, message } } });
const STEPS = ['find', 'connect', 'check', 'install', 'link', 'name', 'done'] as const;
const SECRET_TTL_S = 1800;
const VERSION = '0.15.0';

/** each install step and how long it takes here, ms */
const INSTALL: [InstallStep['id'], string, number][] = [
  ['check', 'Check the box', 1200],
  ['upload', `Upload TandemLLM ${VERSION}`, 2400],
  ['agent', `Install TandemNode ${VERSION}`, 4000],
  ['engine', `Install the engine's runtime ${VERSION}`, 9000],
  ['unit', 'Start the agent (tandemnode.service)', 2000],
  ['pair', 'Pair and pin its certificate', 1200],
];
const INSTALL_LOG: Record<InstallStep['id'], string[]> = {
  check: ['DGX OS 7.2.1 · TandemLLM not installed'],
  upload: [`uploaded tandemllm-${VERSION}-py3-none-any.whl (9.4 MB, sha256 3f9c1d2e7a40b815)`],
  agent: ['Collecting cryptography>=42', `Successfully installed cffi-1.17.1 cryptography-44.0.2 pycparser-2.22 tandemllm-${VERSION}`],
  engine: [`==> [1/3] Checking this machine`, 'ok NVIDIA GB10 (sm_121), driver 580.95.05, CUDA 13.0', `==> [2/3] Engine source: v${VERSION}`, `==> [3/3] Python environment`, 'installing 41 pinned packages (torch 2.13.0+cu130, ...)', 'ok torch 2.13.0+cu130 (CUDA 13.0)'],
  unit: ['Created symlink default.target.wants/tandemnode.service', 'tandemnode.service is up on 127.0.0.1:7700'],
  pair: ['TandemNode adopted this controller; certificate pinned'],
};

// ---- the controller's checks (proxy/preflight.py checks), over the facts its script prints -------

type Facts = Record<string, string>;
const FACTS: Facts = { system: 'Linux', arch: 'aarch64', kernel: '6.11.0-1015-nvidia', os: 'Ubuntu 24.04.3 LTS', os_id: 'ubuntu 24.04', dgx: 'DGX OS 7.2.1', driver_smi: '580.95.05', gpu: 'NVIDIA GB10', cuda: '13.0', mem_kb: String(128 * 1024 * 1024), disk_kb: String(3480 * 1024 * 1024), disk_mount: '/', tandemllm: VERSION, cx7: 'enp1s0f0np0:4: ACTIVE:200,enP2p1s0f1np1:1: DOWN:?', sudo: 'password', ntp: 'yes', ntp_on: 'yes', linger: 'yes', python: '3.12', venv: 'yes', systemd: 'yes', user: 'spark' };

const ver = (t: string | undefined): [number, number] | null => {
  const m = /^\s*(\d+)\.(\d+)/.exec(t ?? '');
  return m ? [Number(m[1]), Number(m[2])] : null;
};
const lt = (a: [number, number], b: [number, number]) => a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);
const gib = (kb: string | undefined) => (kb && /^\d+$/.test(kb) ? Number(kb) / (1024 * 1024) : null);
const row = (id: string, label: string, state: PreflightRow['state'], value: string, fix = ''): PreflightRow => ({ id, label, state, value, fix: fix || null });

export function checks(f: Facts, fleetVersion: string, now: number, install = true): PreflightRow[] {
  const out: PreflightRow[] = [];
  const os = f.dgx || f.os || f.system || 'unknown';
  const arch = f.arch || '?';
  const id = (f.os_id || '').split(' ');
  const ubuntu = id.length === 2 && id[0] === 'ubuntu' && !lt(ver(id[1]) ?? [0, 0], [24, 4]);
  if ((f.system || 'Linux') !== 'Linux') out.push(row('os', 'OS', 'fail', `${os} · ${arch}`, 'TandemNode runs on Linux: DGX OS on a DGX Spark or another GB10 box'));
  else if (f.dgx || (ubuntu && arch === 'aarch64')) out.push(row('os', 'OS', 'ok', `${os} · ${arch}`));
  else out.push(row('os', 'OS', 'warn', `${os} · ${arch}`, 'Not DGX OS: TandemLLM is built for DGX OS (Ubuntu 24.04, aarch64); this box is best effort'));
  out.push(row('kernel', 'Kernel', f.kernel ? 'ok' : 'warn', f.kernel || 'not read', f.kernel ? '' : 'uname -r said nothing'));
  const drv = f.driver_smi || f.driver;
  out.push(drv ? row('driver', 'NVIDIA driver', 'ok', `${drv}${f.gpu ? ` · ${f.gpu}` : ''}`) : row('driver', 'NVIDIA driver', 'fail', 'none loaded', 'Install the NVIDIA driver (DGX OS ships it; on another Ubuntu: sudo ubuntu-drivers install), then reboot'));
  const cuda = ver(f.cuda);
  if (!cuda) out.push(row('cuda', 'CUDA', drv ? 'warn' : 'fail', 'not reported', drv ? 'nvidia-smi did not say its CUDA version; the engine needs 13.0' : 'nvidia-smi names the CUDA version the driver offers; with no driver there is none'));
  else if (lt(cuda, [13, 0])) out.push(row('cuda', 'CUDA', 'fail', f.cuda, 'The engine’s torch is built for CUDA 13.0: update the NVIDIA driver to one that offers 13.0 or later'));
  else out.push(row('cuda', 'CUDA', 'ok', f.cuda));
  const disk = gib(f.disk_kb);
  const where = f.disk_mount || f.home || '~';
  if (disk == null) out.push(row('disk', 'Disk free', 'warn', 'not read', `df ${where} said nothing`));
  else if (disk < 20) out.push(row('disk', 'Disk free', 'fail', `${disk.toFixed(0)} GiB on ${where}`, `TandemNode and the engine’s environment need 20 GiB: free space on ${where}`));
  else if (disk < 100) out.push(row('disk', 'Disk free', 'warn', `${disk.toFixed(0)} GiB on ${where}`, 'Enough for TandemNode and the engine; a model’s weights need 100 GiB or more: free space before Serve a model'));
  else out.push(row('disk', 'Disk free', 'ok', `${disk >= 1024 ? `${(disk / 1024).toFixed(1)} TiB` : `${disk.toFixed(0)} GiB`} on ${where}`));
  const mem = gib(f.mem_kb);
  out.push(mem == null ? row('memory', 'Memory', 'warn', 'not read', '/proc/meminfo said nothing') : row('memory', 'Memory', mem >= 64 ? 'ok' : 'warn', `${mem.toFixed(1)} GiB`, mem >= 64 ? '' : 'Less than a GB10’s 128 GB: only small models fit here'));
  const have = (f.tandemllm || '').trim();
  if (!have) out.push(row('tandemllm', 'TandemLLM', 'ok', `not installed · Install puts ${fleetVersion}`));
  else if (have.split('+')[0] === fleetVersion.split('+')[0]) out.push(row('tandemllm', 'TandemLLM', 'ok', `${have} · the fleet’s version`));
  else out.push(row('tandemllm', 'TandemLLM', 'fail', `${have} · the fleet runs ${fleetVersion}`, `Every node runs the fleet’s version: update TandemLLM on this box to ${fleetVersion} (bash install.sh --ref v${fleetVersion}), then check again. Install refuses ${have}`));
  const cx = (f.cx7 || '').split(',').filter(Boolean).map((p) => p.split(':'));
  if (!cx.length) out.push(row('cx7', 'ConnectX-7', 'warn', 'no ConnectX port', 'LAN only: no fabric link, so no Split across this box; Single and Replicas work'));
  else {
    const words = cx.map(([name, , st, rate]) => {
      const up = (st ?? '').trim().toUpperCase().startsWith('ACTIVE');
      return `${name} ${up ? (rate && rate !== '?' ? `up ${rate}G` : 'up') : 'no link'}`;
    });
    out.push(row('cx7', 'ConnectX-7', 'ok', `${cx.length} port${cx.length > 1 ? 's' : ''} · ${words.join(', ')}`));
  }
  out.push(f.sudo === 'nopasswd' || f.sudo === 'password' ? row('sudo', 'sudo', 'ok', 'available · asked per action in the Link step, never stored') : row('sudo', 'sudo', 'warn', 'not available to this user', 'Only the Link step needs it (link addresses, MTU): add the user to the sudo group, or set the link up by hand'));
  const skew = f.now ? Number(f.now) - now : null;
  const fixClock = 'Run sudo timedatectl set-ntp true on the node. Certificates and metrics need a correct clock';
  if (skew != null && Math.abs(skew) > 5) out.push(row('clock', 'Clock', 'fail', `${Math.abs(skew).toFixed(0)} s ${skew > 0 ? 'ahead' : 'behind'}${f.ntp_on === 'no' ? ' · NTP off' : ''}`, fixClock));
  else if (f.ntp === 'no') out.push(row('clock', 'Clock', 'fail', `not synchronised${f.ntp_on === 'no' ? ' · NTP off' : ''}`, fixClock));
  else out.push(row('clock', 'Clock', 'ok', f.ntp === 'yes' ? 'synchronised' : 'within 5 s of the controller'));
  if (install) {
    out.push(f.linger === 'no' ? row('linger', 'Services after logout', 'warn', `off for ${f.user || 'this user'}`, `The agent is a user service and stops when the last session ends: run sudo loginctl enable-linger ${f.user || '$USER'} on the node`) : row('linger', 'Services after logout', 'ok', f.linger === 'yes' ? 'kept (linger on)' : 'not reported'));
    const py = ver(f.python);
    out.push(!py || lt(py, [3, 11]) || f.venv !== 'yes' ? row('python', 'Python', 'fail', `${f.python || 'none'}${f.venv === 'yes' ? '' : ' · no venv'}`, 'Install needs Python 3.11 or later with venv: sudo apt install python3-venv (DGX OS has Python 3.12)') : row('python', 'Python', 'ok', `${f.python} with venv`));
    if (f.systemd !== 'yes') out.push(row('systemd', 'systemd', 'fail', 'not found', 'The agent runs as a systemd user unit; this box has no systemctl'));
  }
  return out;
}

export function verdict(rows: PreflightRow[], installed: boolean): NonNullable<NonNullable<AddTarget['preflight']>['verdict']> {
  const tv = rows.find((r) => r.id === 'tandemllm');
  const v = tv?.state === 'fail' ? { install: 'refused' as const, why: tv.value } : installed ? { install: 'skip' as const, why: 'TandemNode answers there and runs the fleet’s version' } : { install: 'needed' as const, why: 'nothing installed' };
  return { fails: rows.filter((r) => r.state === 'fail').length, warns: rows.filter((r) => r.state === 'warn').length, ...v };
}

// ---- the session -----------------------------------------------------------------------------------

type Target = Omit<AddTarget, 'secret' | 'install'> & { install: InstallView | null };
interface Job {
  start: number;
  done: boolean;
  options: NonNullable<InstallView['options']>;
}

export class MockWizard {
  private sessions = new Map<string, Omit<AddSession, 'targets' | 'reminders'> & { targets: Target[] }>();
  private active: string | null = null;
  private n = 0;
  private vault = new Map<string, number>();
  private vaultKind = new Map<string, 'password' | 'key'>();
  private jobs = new Map<string, Job>();
  private checksOf = new Map<string, number>();

  constructor(private fleet: MockFleet) {}

  private hold(k: string, kind: 'password' | 'key', now: number): void {
    this.vault.set(k, now + SECRET_TTL_S);
    this.vaultKind.set(k, kind);
  }

  private now(ms: number): number {
    return Math.round(ms) / 1000;
  }

  /** the facts a device's preflight reads, by its address and how many checks it had */
  private facts(t: Target, n: number, nowS: number): Facts {
    const f: Facts = { ...FACTS, now: String(Math.floor(nowS)) };
    if (t.address === '192.0.2.62') return { ...f, tandemllm: '0.14.2', dgx: 'DGX OS 7.1.0' };
    if (t.state === 'bare') {
      f.tandemllm = '';
      if (t.address === '192.0.2.63') {
        Object.assign(f, { dgx: 'DGX OS 7.2.1', cx7: 'enp1s0f0np0:1: DOWN:?,enP2p1s0f1np1:1: DOWN:?', linger: 'no', disk_kb: String(1830 * 1024 * 1024) });
        if (n === 0) Object.assign(f, { ntp: 'no', ntp_on: 'no', now: String(Math.floor(nowS) - 94) });
      }
      if (t.address === MOCK_LAN.rented) Object.assign(f, { dgx: 'DGX OS 7.2.1', cx7: 'enp1s0f0np0:4: ACTIVE:200,enP2p1s0f1np1:4: ACTIVE:200' });
    }
    return f;
  }

  private target(sid: string, tid: string): Target | undefined {
    return this.sessions.get(sid)?.targets.find((t) => t.id === tid);
  }

  /** the install's view at `ms`: its steps on the clock; done joins the node */
  private installAt(sid: string, t: Target, ms: number): void {
    const j = this.jobs.get(`${sid}/${t.id}`);
    if (!j) return;
    const steps: InstallStep[] = [];
    const log: NonNullable<InstallView['log']> = [];
    let at = j.start;
    for (const [id, label, dur] of INSTALL) {
      const skip = id === 'engine' && !j.options.engine;
      const end = at + (skip ? 0 : dur);
      const state: InstallStep['state'] = skip ? 'skip' : ms >= end ? 'done' : ms >= at ? 'running' : 'todo';
      steps.push({ id, label, state, detail: state === 'done' ? INSTALL_LOG[id].at(-1)! : null, started_at: state === 'todo' || skip ? null : at / 1000, ended_at: state === 'done' ? end / 1000 : null });
      if (state !== 'todo' && !skip) {
        const lines = INSTALL_LOG[id];
        const shown = state === 'done' ? lines.length : Math.max(1, Math.floor((lines.length * (ms - at)) / dur));
        lines.slice(0, shown).forEach((text, i) => log.push({ t: (at + (i * dur) / lines.length) / 1000, step: id, level: 'info', text }));
      }
      at = end;
    }
    const finished = ms >= at;
    if (finished && !j.done) {
      j.done = true;
      const node = this.fleet.admit(t.address, 'ssh', Math.floor(at / 1000), { hw: t.hw ?? undefined, ...(t.transport === 'tunnel' ? { transport: 'tunnel' as const, lease_end: t.rented?.lease_end, cost_per_hour: t.rented?.cost_per_hour ?? null } : {}) });
      Object.assign(t, { state: 'paired', node: node.id, via: 'ssh' });
      this.vault.delete(`${sid}/${t.id}`);
    }
    t.install = { state: finished ? 'done' : 'running', version: VERSION, node: finished ? t.node : null, error: null, started_at: j.start / 1000, ended_at: finished ? at / 1000 : null, options: j.options, steps, log };
  }

  view(sid: string, ms: number): AddSession {
    const s = this.sessions.get(sid)!;
    for (const t of s.targets) this.installAt(sid, t, ms);
    const now = this.now(ms);
    const hours = this.fleet.leaseHours();
    return {
      ...s,
      contract_version: FLEET_CONTRACT,
      targets: s.targets.map((t) => {
        const until = this.vault.get(`${s.id}/${t.id}`);
        return { ...t, secret: { held: until != null && until > now, until: until != null && until > now ? until : null } };
      }),
      reminders: s.targets.filter((t) => t.rented && t.node).map((t) => ({ node: t.node!, target: t.id, lease_end: t.rented!.lease_end, at: t.rented!.lease_end - hours * 3600, hours, rule: 'lease' as const, enabled: true })),
    };
  }

  private fromRow(tid: string, rid: unknown, ms: number): Target | Reply {
    const r = this.fleet.discovery.snapshot(ms).rows.find((x) => x.id === rid);
    if (!r) return bad(404, 'not_found', `no discovery row ${String(rid)}: find again`);
    if (r.fleet) return bad(409, 'conflict', `${r.name ?? r.addresses[0]} is in this fleet already`);
    if (!r.connect.length) return bad(409, 'conflict', `${r.name ?? r.addresses[0]}: ${r.title}; nothing to add`);
    const cfg = r.ssh_config;
    const jumps = cfg?.proxy_jump ? parseSshLine(`ssh -J ${cfg.proxy_jump} x`).jumps : [];
    const addr = cfg?.hostname ?? r.addresses[0];
    const remote = jumps.length > 0 || !/^(192\.0\.2\.|10\.|192\.168\.)/.test(addr);
    return {
      id: tid, row: r.id, label: r.name ?? addr, kind: r.kind, title: r.title, detail: r.detail, address: addr, connect: [...r.connect], hw: r.hw?.label ?? null,
      version: r.node?.version ?? null, window: r.node?.window ?? null,
      ssh: { host: addr, port: cfg?.port ?? r.ssh?.port ?? 22, user: cfg?.user ?? null, jumps, key_file: cfg?.identity_file ?? null, auth: null },
      rented: null, transport: remote ? 'tunnel' : 'lan', state: 'todo', via: null, host_key: null, error: null, node: null, preflight: null, install: null, name: null,
    };
  }

  private fromSsh(tid: string, ssh: unknown, rented: unknown, nowS: number): Target | Reply {
    let got;
    try {
      got = typeof ssh === 'string' ? parseSshLine(ssh) : parseSshLine(`ssh -p ${(ssh as { port?: number }).port ?? 22} ${(ssh as { jumps?: unknown[] }).jumps?.length ? `-J ${((ssh as { jumps: { user: string | null; host: string; port: number }[] }).jumps).map((j) => `${j.user ? `${j.user}@` : ''}${j.host}:${j.port}`).join(',')} ` : ''}${(ssh as { user?: string }).user ? `${(ssh as { user: string }).user}@` : ''}${(ssh as { host: string }).host}`);
    } catch (e) {
      return bad(400, 'bad_request', (e as Error).message);
    }
    const kf = typeof ssh === 'object' && ssh ? ((ssh as { identity_file?: string | null }).identity_file ?? null) : got.identity_file;
    let lease: Target['rented'] = null;
    if (rented != null) {
      const r = rented as { lease_end?: unknown; cost_per_hour?: unknown };
      if (typeof r !== 'object' || Object.keys(r).some((k) => k !== 'lease_end' && k !== 'cost_per_hour')) return bad(400, 'bad_request', 'rented is {lease_end, cost_per_hour?}');
      if (typeof r.lease_end !== 'number' || r.lease_end <= nowS) return bad(400, 'bad_request', 'lease_end is a Unix time in the future');
      if (r.cost_per_hour != null && (typeof r.cost_per_hour !== 'number' || r.cost_per_hour < 0)) return bad(400, 'bad_request', 'cost_per_hour is a number from 0');
      lease = { lease_end: r.lease_end, cost_per_hour: (r.cost_per_hour as number | null | undefined) ?? null };
    }
    const via = got.jumps.length ? ` via ${got.jumps.map((j) => `${j.user ? `${j.user}@` : ''}${j.host}${j.port !== 22 ? `:${j.port}` : ''}`).join(', ')}` : '';
    return {
      id: tid, row: null, label: got.host, kind: lease ? 'rented' : 'remote', title: lease ? 'Rented Spark' : 'Remote host', detail: `${got.user ? `${got.user}@` : ''}${got.host}:${got.port}${via}`, address: got.host, connect: ['ssh'], hw: null, version: null, window: null,
      ssh: { host: got.host, port: got.port, user: got.user, jumps: got.jumps, key_file: kf, auth: kf ? 'key_file' : null },
      rented: lease, transport: lease || got.remote ? 'tunnel' : 'lan', state: 'todo', via: null, host_key: null, error: null, node: null, preflight: null, install: null, name: null,
    };
  }

  // ---- the routes ------------------------------------------------------------------------------
  handle(method: string, parts: string[], body: unknown, ms: number): Reply {
    const p = parts.slice(1);
    const b = (body ?? {}) as Record<string, unknown>;
    const now = this.now(ms);
    const v = (sid: string): Reply => ({ status: 200, body: this.view(sid, ms) });
    if (!p.length) {
      if (method === 'GET') return { status: 200, body: { contract_version: FLEET_CONTRACT, fleet_version: VERSION, session: this.active ? this.view(this.active, ms) : null } };
      if (method === 'POST') {
        if (Object.keys(b).some((k) => k !== 'replace')) return bad(400, 'bad_request', 'the body is {replace?}');
        if (this.active) {
          const cur = this.sessions.get(this.active)!;
          if (!b.replace) return bad(409, 'busy', `an Add Spark session is open (${cur.id}, at ${cur.step}): resume it, or start over with replace: true`);
          cur.state = 'abandoned';
          this.forget(cur.id);
        }
        const id = `a-mock${String(++this.n).padStart(4, '0')}`;
        this.sessions.set(id, { contract_version: FLEET_CONTRACT, id, state: 'active', step: 'find', created_at: now, updated_at: now, by: 'dashboard session', fleet_version: VERSION, targets: [], link: { state: 'todo', why: null, results: [] }, done_at: null, seq: 0 });
        this.active = id;
        return v(id);
      }
    }
    const s = this.sessions.get(p[0] ?? '');
    if (!s) return bad(404, 'not_found', `no Add Spark session ${p[0]}`);
    if (p.length === 1 && method === 'GET') return v(s.id);
    if (s.state !== 'active') return bad(409, 'conflict', `the session ${s.id} is ${s.state}: start a new one`);
    s.updated_at = now;
    for (const t of s.targets) this.installAt(s.id, t, ms);
    if (p.length === 1 && method === 'DELETE') {
      if (s.targets.some((t) => t.install?.state === 'running')) return bad(409, 'busy', 'an install is running: wait for it');
      s.state = 'abandoned';
      this.forget(s.id);
      return v(s.id);
    }
    if (p.length === 1 && method === 'PATCH') return this.patch(s, b, ms);
    if (p[1] === 'targets' && p.length === 2 && method === 'POST') {
      if (!('row' in b) && !('ssh' in b)) return bad(400, 'bad_request', 'the body is {row} (a discovery row) or {ssh, rented?, key?}');
      if (s.targets.length >= 8) return bad(409, 'conflict', '8 devices per add at most');
      const tid = `t${++s.seq}`;
      const t = 'row' in b ? this.fromRow(tid, b.row, ms) : this.fromSsh(tid, b.ssh, b.rented, now);
      if ('status' in t) {
        s.seq--;
        return t;
      }
      if (s.targets.some((x) => (t.row && x.row === t.row) || (!t.row && !x.row && x.address === t.address && x.ssh.port === t.ssh.port))) {
        s.seq--;
        return bad(409, 'conflict', `${t.label} is picked already`);
      }
      if (typeof b.key === 'string' && b.key.trim()) {
        this.hold(`${s.id}/${tid}`, 'key', now);
        t.ssh.auth = 'key';
      }
      s.targets.push(t);
      return v(s.id);
    }
    if (p[1] === 'targets' && p.length === 3 && method === 'DELETE') {
      const t = s.targets.find((x) => x.id === p[2]);
      if (!t) return bad(404, 'not_found', `no target ${p[2]} in this session`);
      if (t.install?.state === 'running') return bad(409, 'busy', 'its install is running');
      s.targets = s.targets.filter((x) => x !== t);
      this.vault.delete(`${s.id}/${t.id}`);
      return v(s.id);
    }
    if (p[1] === 'targets' && p.length === 4 && method === 'POST') {
      const t = s.targets.find((x) => x.id === p[2]);
      if (!t) return bad(404, 'not_found', `no target ${p[2]} in this session`);
      if (p[3] === 'hostkey') {
        const r = this.fleet.handle('POST', '/v1/fleet/pair/ssh/hostkey', new URLSearchParams(), { host: t.ssh.host, port: t.ssh.port }, ms);
        if (r.status !== 200) return r;
        const hk = r.body as { type: string; fingerprint: string; known: 'new' | 'same' | 'changed'; stored: string | null };
        t.host_key = { type: hk.type, fingerprint: hk.fingerprint, known: hk.known, stored: hk.stored, confirmed: false };
        return v(s.id);
      }
      if (p[3] === 'connect') return this.connect(s, t, b, ms);
    }
    if (p[1] === 'check' && method === 'POST') return this.check(s, b, ms);
    if (p[1] === 'install' && method === 'POST') return this.install(s, b, ms);
    if (p[1] === 'link' && method === 'POST') return this.link(s, b, ms);
    return bad(404, 'not_found', `no route ${method} /v1/fleet/${parts.join('/')}`);
  }

  private forget(sid: string): void {
    for (const k of [...this.vault.keys()]) if (k.startsWith(`${sid}/`)) this.vault.delete(k);
    if (this.active === sid) this.active = null;
  }

  private connect(s: NonNullable<ReturnType<MockWizard['sessions']['get']>>, t: Target, b: Record<string, unknown>, ms: number): Reply {
    const m = b.method;
    if (m !== 'code' && m !== 'ssh' && m !== 'takeover') return bad(400, 'bad_request', 'the body is {method: code|ssh|takeover, ...}');
    if (!t.connect.includes(m) && !(m === 'ssh' && t.connect.includes('takeover'))) return bad(409, 'conflict', `${t.label} cannot be connected by ${m}`);
    t.error = null;
    const fail = (r: Reply): Reply => {
      const e = (r.body as { error: { type: string; message: string } }).error;
      t.error = e;
      if (e.type === 'owned' && !t.connect.includes('takeover')) t.connect.push('takeover');
      return r;
    };
    if (m === 'code') {
      const r = this.fleet.handle('POST', '/v1/fleet/pair', new URLSearchParams(), { address: t.address, code: String(b.code ?? ''), ...(t.window ? { port: t.window } : {}) }, ms);
      if (r.status !== 200) return fail(r);
      Object.assign(t, { state: 'paired', via: 'code', node: (r.body as { id: string }).id });
      return { status: 200, body: this.view(s.id, ms) };
    }
    if (!b.host_key || !t.host_key || b.host_key !== t.host_key.fingerprint) return fail(bad(409, 'host_key_unconfirmed', 'confirm the host key first (read it with .../hostkey and compare it on the node)'));
    const user = (b.user as string | undefined) ?? t.ssh.user ?? undefined;
    const held = (this.vault.get(`${s.id}/${t.id}`) ?? 0) > this.now(ms) ? this.vaultKind.get(`${s.id}/${t.id}`) : undefined;
    const given = b.password ? { password: b.password } : b.key ? { key: b.key } : {};
    // no secret in the body and none asked for: the one held for this target (a key given in Find)
    const secret = Object.keys(given).length || b.key_file || b.agent ? given : held ? { [held]: 'held' } : {};
    const kf = (b.key_file as string | undefined) ?? (!Object.keys(secret).length && !b.agent ? (t.ssh.key_file ?? undefined) : undefined);
    const login = { user: user ?? null, auth: ('password' in secret ? 'password' : 'key' in secret ? 'key' : kf ? 'key_file' : 'agent') as AddTarget['ssh']['auth'], key_file: kf ?? null };
    const r = this.fleet.handle('POST', '/v1/fleet/pair/ssh', new URLSearchParams(), { host: t.ssh.host, port: t.ssh.port, user, host_key: b.host_key, ...secret, ...(kf ? { key_file: kf } : {}), ...(t.transport === 'tunnel' ? { tunnel: true } : {}), ...(m === 'takeover' ? { takeover: true } : {}) }, ms);
    if (r.status === 200 || r.status === 409) {
      Object.assign(t.ssh, login);
      t.host_key.confirmed = true;
    }
    if (r.status === 409 && (r.body as { error: { type: string } }).error.type === 'not_installed') {
      if (Object.keys(given).length) this.hold(`${s.id}/${t.id}`, 'password' in given ? 'password' : 'key', this.now(ms));
      t.state = 'bare';
      t.via = m;
      const row = this.fleet.discovery.snapshot(ms).rows.find((x) => x.addresses.includes(t.address));
      if (row && t.row) Object.assign(t, { row: row.id, kind: row.kind, title: row.title, detail: row.detail, hw: row.hw?.label ?? t.hw });
      if (t.address === MOCK_LAN.rented) t.hw = 'NVIDIA DGX Spark';
      return { status: 200, body: this.view(s.id, ms) };
    }
    if (r.status !== 200) return fail(r);
    Object.assign(t, { state: 'paired', via: m, node: (r.body as { id: string }).id });
    this.vault.delete(`${s.id}/${t.id}`);
    return { status: 200, body: this.view(s.id, ms) };
  }

  private check(s: NonNullable<ReturnType<MockWizard['sessions']['get']>>, b: Record<string, unknown>, ms: number): Reply {
    const now = this.now(ms);
    const only = Array.isArray(b.targets) ? (b.targets as string[]) : null;
    const given = (b.secrets ?? {}) as Record<string, { password?: string; key?: string }>;
    for (const t of s.targets) {
      if (only && !only.includes(t.id)) continue;
      if (t.state === 'todo') continue;
      const k = `${s.id}/${t.id}`;
      if (t.state === 'bare' && (t.ssh.auth === 'password' || t.ssh.auth === 'key')) {
        const held = (this.vault.get(k) ?? 0) > now;
        if (!held && !given[t.id]) {
          t.preflight = { at: now, via: null, rows: [], verdict: null, error: { type: 'secret_needed', message: `enter the ${t.ssh.auth} for ${t.ssh.user}@${t.address} again: it is kept in memory only, for 30 minutes, and never stored` } };
          return bad(409, 'secret_needed', t.preflight.error!.message);
        }
        if (given[t.id]) this.hold(k, given[t.id].password ? 'password' : 'key', now);
      }
      const n = this.checksOf.get(k) ?? 0;
      this.checksOf.set(k, n + 1);
      const rows = checks(this.facts(t, n, now), s.fleet_version, now, t.state === 'bare');
      t.preflight = { at: now, via: t.state === 'paired' ? 'agent' : 'ssh', rows, verdict: verdict(rows, t.state === 'paired'), error: null };
      if (t.state === 'paired' && t.preflight.verdict!.install === 'skip') t.install = { state: 'skip', why: `TandemLLM ${s.fleet_version} runs there already` };
    }
    return { status: 200, body: this.view(s.id, ms) };
  }

  private install(s: NonNullable<ReturnType<MockWizard['sessions']['get']>>, b: Record<string, unknown>, ms: number): Reply {
    const now = this.now(ms);
    const start: Target[] = [];
    for (const t of s.targets) {
      const vd = t.preflight?.verdict;
      if (t.state === 'paired') {
        if (vd?.install === 'refused') return bad(409, 'version_mismatch', `${t.label}: ${vd.why}; Install refuses another version`);
        t.install = { state: 'skip', why: `TandemLLM ${s.fleet_version} runs there already` };
        continue;
      }
      if (t.state !== 'bare') return bad(409, 'conflict', `connect ${t.label} first`);
      if (!vd) return bad(409, 'conflict', `check ${t.label} first`);
      if (vd.install === 'refused') return bad(409, 'version_mismatch', `${t.label}: ${vd.why}; Install refuses another version`);
      if (vd.fails) return bad(409, 'preflight_failed', `${t.label}: fix the red checks, then check again`);
      const k = `${s.id}/${t.id}`;
      if (this.jobs.has(k)) continue;
      if ((t.ssh.auth === 'password' || t.ssh.auth === 'key') && (this.vault.get(k) ?? 0) <= now && !((b.secrets ?? {}) as Record<string, unknown>)[t.id]) {
        return bad(409, 'secret_needed', `enter the ${t.ssh.auth} for ${t.ssh.user}@${t.address} again: it is kept in memory only, for 30 minutes, and never stored`);
      }
      start.push(t);
    }
    for (const t of start) {
      const k = `${s.id}/${t.id}`;
      const o = (((b.options ?? {}) as Record<string, { engine?: boolean; port?: number; unit?: string; prefix?: string }>)[t.id] ?? {}) as { engine?: boolean; port?: number; unit?: string; prefix?: string };
      this.jobs.set(k, { start: ms, done: false, options: { prefix: o.prefix ?? '~/.local/share/tandemllm', port: o.port ?? 7700, unit: o.unit ?? 'tandemnode', engine: o.engine ?? true } });
      this.vault.delete(k);
      this.installAt(s.id, t, ms);
    }
    return { status: 200, body: this.view(s.id, ms) };
  }

  private link(s: NonNullable<ReturnType<MockWizard['sessions']['get']>>, b: Record<string, unknown>, ms: number): Reply {
    if (!['test', 'skip', 'configure'].includes(b.action as string)) return bad(400, 'bad_request', 'the body is {action: test|skip|configure}');
    const lan = s.targets.filter((t) => t.node && t.transport === 'lan');
    if (lan.length < 2) {
      s.link = { state: 'not_needed', why: s.targets.length === 1 ? `${s.targets[0].label} is the only new Spark: a link needs two or more on one fabric. Test it later from its page` : 'a link needs two or more new Sparks on the LAN', results: [] };
      return { status: 200, body: this.view(s.id, ms) };
    }
    if (b.action === 'skip') {
      s.link = { ...s.link, state: 'skipped', why: 'skipped: run it later from a Spark’s page' };
      return { status: 200, body: this.view(s.id, ms) };
    }
    if (b.action === 'configure') return bad(501, 'not_built', 'setting link addresses with sudo comes with the rented pair (step 7): set the fabric up by hand, then test it here');
    s.link = {
      state: 'done',
      why: null,
      results: lan.map((t) => {
        const r = this.fleet.handle('POST', `/v1/fleet/links/lan-${t.node}/test`, new URLSearchParams(), null, ms);
        const ok = r.status === 200 ? (r.body as { link: { test: AddSession['link']['results'][number]['test'] } }).link.test : null;
        return { node: t.node!, link: `lan-${t.node}`, test: ok ?? null, error: ok ? null : (r.body as { error: { type: string; message: string } }).error };
      }),
    };
    return { status: 200, body: this.view(s.id, ms) };
  }

  private patch(s: NonNullable<ReturnType<MockWizard['sessions']['get']>>, b: Record<string, unknown>, ms: number): Reply {
    if (!Object.keys(b).length || Object.keys(b).some((k) => k !== 'step' && k !== 'names')) return bad(400, 'bad_request', 'the body is {step?, names?}');
    if ('names' in b) {
      if (typeof b.names !== 'object' || b.names == null || Array.isArray(b.names)) return bad(400, 'bad_request', 'names is {target id: {alias, colour, icon, tags}}');
      for (const [tid, n] of Object.entries(b.names as Record<string, AddTarget['name']>)) {
        const t = s.targets.find((x) => x.id === tid);
        if (!t) return bad(404, 'not_found', `no target ${tid} in this session`);
        t.name = { ...t.name, ...n };
      }
    }
    if ('step' in b) {
      const step = b.step as (typeof STEPS)[number];
      if (!STEPS.includes(step)) return bad(400, 'bad_request', `step is one of ${STEPS.join(', ')}`);
      if (step === 'done') {
        const todo = s.targets.filter((t) => !t.node);
        if (!s.targets.length || todo.length) return bad(409, 'conflict', todo.length ? `every device must be paired first: ${todo.map((t) => t.label).join(', ')}` : 'no device picked');
        for (const t of s.targets) {
          if (!t.name || !Object.keys(t.name).length) continue;
          const r = this.fleet.handle('PATCH', `/v1/fleet/nodes/${t.node}`, new URLSearchParams(), t.name, ms);
          if (r.status !== 200) return bad(r.status, 'bad_request', `${t.label}: ${(r.body as { error: { message: string } }).error.message}`);
        }
        Object.assign(s, { step: 'done', state: 'done', done_at: this.now(ms) });
        this.forget(s.id);
        return { status: 200, body: this.view(s.id, ms) };
      }
      s.step = step;
    }
    return { status: 200, body: this.view(s.id, ms) };
  }
}
