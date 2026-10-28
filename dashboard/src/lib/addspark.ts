// What the Add Spark wizard computes (VIS-246, concept §6): which steps are skipped and why, whether
// Next may go on from a step and what it waits for, where Next and Back lead, the Name step's drafts
// and what the controller would refuse of them, the rented form's body, an install's progress and the
// permissions the wizard asks for. Pure: views/fleet/wizard.ts draws it. The session itself lives on
// the controller (proxy/addspark.py), so a reload or another device resumes at its `step`.

import type { AddSession, AddStep, AddTarget, FleetNode, InstallView, LocalNetwork } from '../api/fleet';
import { ADD_STEPS } from '../api/fleet';
import { editPatch, parseTags, type EditDraft } from './sparks';

export const STEP_LABEL: Record<AddStep, string> = { find: 'Find', connect: 'Connect', check: 'Check', install: 'Install', link: 'Link', name: 'Name', done: 'Done' };

/** The new nodes the session paired so far. */
export function newNodes(s: Pick<AddSession, 'targets'>): AddTarget[] {
  return s.targets.filter((t) => !!t.node);
}

/** Why a step is skipped for this session, or null when it is shown. Install: every device runs the
 *  fleet's version already (nothing to install). Link: fewer than two new Sparks on the LAN. */
export function skipped(s: AddSession, step: AddStep): string | null {
  if (step === 'install') {
    if (!s.targets.length || s.targets.some((t) => t.state !== 'paired')) return null;
    if (s.targets.every((t) => t.install?.state === 'skip' || t.preflight?.verdict?.install === 'skip')) {
      return s.targets.length === 1
        ? `${s.targets[0].label} runs TandemLLM ${s.fleet_version} already: nothing to install`
        : `every Spark runs TandemLLM ${s.fleet_version} already: nothing to install`;
    }
    return null;
  }
  if (step === 'link') {
    const all = s.targets;
    const lan = all.filter((t) => t.transport === 'lan');
    if (lan.length >= 2) return null;
    if (s.link.state === 'not_needed' && s.link.why) return s.link.why;
    const far = all.filter((t) => t.transport !== 'lan');
    if (all.length === 1) {
      const t = all[0];
      return t.transport === 'lan' ? `${t.label} is the only new Spark: a link needs two or more on one fabric. Test it later from its page` : `${t.label} is reached through a tunnel: no fabric reaches it from here`;
    }
    return `${far.map((t) => t.label).join(', ')} ${far.length === 1 ? 'is' : 'are'} reached through a tunnel: no fabric between ${far.length === 1 ? 'it' : 'them'} and the LAN${lan.length ? `; ${lan[0].label} is the only one on the LAN` : ''}`;
  }
  return null;
}

/** The step Next leads to: the following one that is not skipped. */
export function nextStep(s: AddSession, from: AddStep): AddStep {
  let i = ADD_STEPS.indexOf(from) + 1;
  while (i < ADD_STEPS.length - 1 && skipped(s, ADD_STEPS[i])) i++;
  return ADD_STEPS[Math.min(i, ADD_STEPS.length - 1)];
}

/** The step Back leads to: the one before that is not skipped (Find stays Find). */
export function prevStep(s: AddSession, from: AddStep): AddStep {
  let i = ADD_STEPS.indexOf(from) - 1;
  while (i > 0 && skipped(s, ADD_STEPS[i])) i--;
  return ADD_STEPS[Math.max(i, 0)];
}

/** The steps skipped on the way from `from` to `to`, each with its why. */
export function skippedBetween(s: AddSession, from: AddStep, to: AddStep): { step: AddStep; why: string }[] {
  const a = ADD_STEPS.indexOf(from);
  const b = ADD_STEPS.indexOf(to);
  const out: { step: AddStep; why: string }[] = [];
  for (let i = Math.min(a, b) + 1; i < Math.max(a, b); i++) {
    const why = skipped(s, ADD_STEPS[i]);
    if (why) out.push({ step: ADD_STEPS[i], why });
  }
  return out;
}

/** The TandemLLM version a device runs when it is not the fleet's (a red check row), else null. */
export function versionMismatch(t: AddTarget): string | null {
  const r = t.preflight?.rows.find((x) => x.id === 'tandemllm');
  return r?.state === 'fail' ? r.value : null;
}

/** Whether Next may leave `step`: true, or what it waits for in words. */
export function gate(s: AddSession, step: AddStep, names?: Record<string, NameDraft>, nodes: Pick<FleetNode, 'id' | 'alias' | 'colour'>[] = []): true | string {
  const ts = s.targets;
  switch (step) {
    case 'find':
      return ts.length ? true : 'Pick at least one device';
    case 'connect': {
      const todo = ts.filter((t) => t.state === 'todo');
      return todo.length ? `Connect ${todo.map((t) => t.label).join(' and ')}` : true;
    }
    case 'check': {
      const unchecked = ts.filter((t) => !t.preflight?.verdict);
      if (unchecked.length) return `Check ${unchecked.map((t) => t.label).join(' and ')}`;
      const old = ts.find((t) => versionMismatch(t));
      if (old) return `${old.label} runs TandemLLM ${versionMismatch(old)!.split(' ')[0]}; the fleet runs ${s.fleet_version}: update it there, then check again`;
      const red = ts.filter((t) => (t.preflight?.verdict?.fails ?? 0) > 0);
      return red.length ? 'Fix the red rows, then check again' : true;
    }
    case 'install': {
      if (ts.some((t) => t.install?.state === 'running' || t.install?.state === 'queued')) return 'Installing…';
      const bad = ts.find((t) => t.install?.state === 'failed');
      if (bad) return `The install on ${bad.label} failed: read its log, then Install again`;
      const todo = ts.filter((t) => t.state !== 'paired');
      return todo.length ? `Install ${todo.map((t) => t.label).join(' and ')}` : true;
    }
    case 'link':
      return skipped(s, 'link') || ['done', 'skipped'].includes(s.link.state) ? true : 'Test the link, or skip it';
    case 'name': {
      if (!names) return true;
      if (newNodes(s).some((t) => !names[t.id])) return 'Reading the new Sparks…';
      const err = nameErrors(s, names, nodes);
      return Object.values(err).find(Boolean) ?? true;
    }
    default:
      return true;
  }
}

/** The step a session is at as far as Next allows: its own `step`, never past what gates it. */
export function resumeStep(s: AddSession): AddStep {
  if (s.state === 'done') return 'done';
  const at = ADD_STEPS.indexOf(s.step);
  for (let i = 0; i < at; i++) {
    const st = ADD_STEPS[i];
    if (!skipped(s, st) && gate(s, st) !== true) return st;
  }
  return s.step;
}

// ---- Name ----------------------------------------------------------------------------------------

export interface NameDraft {
  alias: string;
  colour: number;
  icon: FleetNode['icon'];
  tags: string;
}

/** A target's Name draft: what the user typed (kept on the controller), else its node as paired. */
export function nameDraft(t: AddTarget, node: Pick<FleetNode, 'alias' | 'colour' | 'icon' | 'tags'> | null): NameDraft {
  const n = t.name ?? {};
  return {
    alias: n.alias ?? node?.alias ?? t.label,
    colour: n.colour ?? node?.colour ?? 1,
    icon: n.icon ?? node?.icon ?? (t.rented ? 'cloud' : 'desk'),
    tags: (n.tags ?? node?.tags ?? (t.rented ? ['rented'] : [])).join(', '),
  };
}

/** What the controller would refuse of the drafts, per target (an alias or a colour another Spark or
 *  another draft has), '' for a draft it takes. */
export function nameErrors(s: AddSession, names: Record<string, NameDraft>, nodes: Pick<FleetNode, 'id' | 'alias' | 'colour'>[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const t of newNodes(s)) {
    const d = names[t.id];
    if (!d) continue;
    const others = [
      ...nodes.filter((n) => n.id !== t.node),
      ...newNodes(s)
        .filter((x) => x.id !== t.id && names[x.id])
        .map((x) => ({ id: x.node!, alias: names[x.id].alias.trim(), colour: names[x.id].colour })),
    ];
    const me = nodes.find((n) => n.id === t.node);
    const pseudo = { id: t.node!, alias: me?.alias ?? '', colour: me?.colour ?? 0, icon: 'desk', tags: [] } as unknown as FleetNode;
    out[t.id] = editPatch(pseudo, d as EditDraft, others.filter((o) => o.id !== t.node)).error ?? '';
  }
  return out;
}

/** The PATCH body of the Name step: each target's draft, tags as a list. */
export function namesBody(s: AddSession, names: Record<string, NameDraft>): Record<string, NonNullable<AddTarget['name']>> {
  const out: Record<string, NonNullable<AddTarget['name']>> = {};
  for (const t of newNodes(s)) {
    const d = names[t.id];
    if (d) out[t.id] = { alias: d.alias.trim(), colour: d.colour, icon: d.icon, tags: parseTags(d.tags) };
  }
  return out;
}

// ---- Find: rented / remote -----------------------------------------------------------------------

export interface RentedForm {
  line: string;
  /** a key file's text, picked in the browser (kept in the controller's memory during the add) */
  key: string;
  /** a key file's path on the controller's machine instead */
  keyPath: string;
  jump: string;
  /** yyyy-mm-dd, the lease's last day (it ends at its end, local time) */
  leaseEnd: string;
  cost: string;
}

/** The ssh line with the form's own jump host and key path added as ssh options, as the controller
 *  reads it (-J, -i: the first value wins, so the line's own stay first). */
export function rentedLine(f: RentedForm): string {
  let line = f.line.trim();
  if (!/^ssh(\s|$)/.test(line)) line = `ssh ${line}`;
  const extra: string[] = [];
  if (f.jump.trim()) extra.push(`-J ${f.jump.trim()}`);
  if (f.keyPath.trim()) extra.push(`-i ${f.keyPath.trim()}`);
  return extra.length ? line.replace(/^ssh/, `ssh ${extra.join(' ')}`) : line;
}

/** The lease and cost of the form, or what is wrong in words. A lease ends at 23:59 of its day. */
export function rentedTerms(f: Pick<RentedForm, 'leaseEnd' | 'cost'>, now = Date.now() / 1000): { lease_end: number; cost_per_hour: number | null } | string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(f.leaseEnd.trim());
  if (!m) return 'Give the day the lease ends';
  const end = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 23, 59, 0).getTime() / 1000;
  if (!Number.isFinite(end) || end <= now) return 'The lease ends in the future';
  const c = f.cost.trim().replace(/^[€$£]\s*/, '').replace(',', '.');
  if (!c) return { lease_end: end, cost_per_hour: null };
  const cost = Number(c);
  if (!Number.isFinite(cost) || cost < 0) return 'Cost per hour is a number, like 2.40';
  return { lease_end: end, cost_per_hour: cost };
}

// ---- Install -------------------------------------------------------------------------------------

/** 0..100 of an install: done steps, a running one half; a skipped install is done. */
export function installPercent(v: InstallView | null | undefined): number {
  if (!v) return 0;
  if (v.state === 'skip' || v.state === 'done') return 100;
  const steps = (v.steps ?? []).filter((x) => x.state !== 'skip');
  if (!steps.length) return 0;
  const done = steps.filter((x) => x.state === 'done').length + steps.filter((x) => x.state === 'running').length * 0.5;
  return Math.round((done / steps.length) * 100);
}

/** The install's state in words: its running step, done, failed with its error, or skipped. */
export function installWords(v: InstallView | null | undefined): string {
  if (!v) return 'waiting';
  if (v.state === 'skip') return v.why ?? 'skipped';
  if (v.state === 'done') return `TandemNode running · TandemLLM ${v.version}`;
  if (v.state === 'failed') return `failed: ${v.error?.message ?? 'see the log'}`;
  const run = v.steps?.find((x) => x.state === 'running');
  return run ? `${run.label}…` : 'starting…';
}

// ---- Connect -------------------------------------------------------------------------------------

/** "spark@192.0.2.61:22 via hop@198.51.100.1" */
export function loginWords(t: Pick<AddTarget, 'ssh'>, user?: string): string {
  const s = t.ssh;
  const u = user ?? s.user;
  return `${u ? `${u}@` : ''}${s.host}${s.port !== 22 ? `:${s.port}` : ''}${s.jumps.length ? ` via ${s.jumps.map((j) => `${j.user ? `${j.user}@` : ''}${j.host}${j.port !== 22 ? `:${j.port}` : ''}`).join(', ')}` : ''}`;
}

// ---- Permissions ---------------------------------------------------------------------------------

export interface PermissionRow {
  id: 'lan' | 'notify' | 'keychain';
  name: string;
  state: string;
  ok: boolean | null;
  why: string;
  without: string;
}

/** The permissions the wizard needs where it needs them, each with one line of why and what works
 *  without it. Keychain is not a browser app's to ask for: said so, not faked. */
export function permissionRows(lan: LocalNetwork | null, notify: NotificationPermission | 'unsupported'): PermissionRow[] {
  return [
    {
      id: 'lan',
      name: 'Local Network',
      state: lan == null ? 'not read' : { not_needed: 'not needed here', not_asked: 'asked at the first search', granted: 'allowed', denied: 'denied' }[lan],
      ok: lan == null || lan === 'not_asked' ? null : lan !== 'denied',
      why: 'Find listens for Sparks (mDNS) and scans the subnet when you say yes.',
      without: 'add a Spark by address or as a rented one over SSH.',
    },
    {
      id: 'notify',
      name: 'Notifications',
      state: notify === 'unsupported' ? 'not offered here' : notify === 'default' ? 'not asked yet' : notify,
      ok: notify === 'granted' ? true : notify === 'denied' ? false : null,
      why: 'A rented Spark’s lease reminder, and the fleet’s alerts, as system notifications.',
      without: 'the reminder shows in Events & Alerts. Turn notifications on there.',
    },
    {
      id: 'keychain',
      name: 'Keychain',
      state: 'not available in a browser app',
      ok: null,
      why: 'TandemApp keeps no secret of yours: a password is used once and held in the controller’s memory only, a key file is read where it is.',
      without: 'nothing is lost: the native Mac shell will keep keys in the Keychain.',
    },
  ];
}
