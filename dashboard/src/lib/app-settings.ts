// App settings (6.3b, concept §8.13 and A14): the words of each permission the controller reports
// (with the browser's own notification state merged in), the admin password's state and form, the
// fleet's configuration file and an import's plan. views/fleet/app-settings.ts draws them; the
// controller's answers are /v1/fleet/app* (proxy/appsettings.py, proxy/adminpw.py).

import type { AppPermission, FleetNode, ImportItem, ImportPlan, ImportResult, PasswordState } from '../api/fleet';
import { mixedVersions, needsUpdate } from './fleet-update';

export type Tone = 'ok' | 'warn' | 'err' | 'muted';
/** the browser's notification permission, or `unsupported` (no Notification API, or a page not
 *  served over https, where browsers offer none) */
export type BrowserNotify = 'granted' | 'denied' | 'default' | 'unsupported';

export interface PermRow {
  id: AppPermission['id'];
  title: string;
  badge: string;
  tone: Tone;
  applies: boolean;
  why: string;
  detail: string;
  fallback: string | null;
  settings: string | null;
  action: AppPermission['action'];
  files: NonNullable<AppPermission['files']>;
}

const BADGE: Record<AppPermission['state'], [string, Tone]> = {
  granted: ['Allowed', 'ok'],
  lan_denied: ['Denied', 'err'],
  not_asked: ['Not asked yet', 'warn'],
  not_applicable: ['Does not apply', 'muted'],
  unknown: ['Unknown', 'muted'],
  file: ['Files, 0600', 'ok'],
  too_open: ['Too open', 'err'],
  keychain: ['Keychain', 'ok'],
  in_browser: ['In the browser', 'muted'],
  per_action: ['Asked per action', 'muted'],
};

/** what this browser says of notifications */
export function browserNotify(w: { Notification?: { permission: NotificationPermission }; isSecureContext?: boolean } = globalThis as never): BrowserNotify {
  if (!w.Notification || w.isSecureContext === false) return 'unsupported';
  return w.Notification.permission;
}

/** The rows in the controller's order; the notifications row says this browser's state. */
export function permRows(perms: AppPermission[], notify: BrowserNotify): PermRow[] {
  return perms.map((p) => {
    let [badge, tone] = BADGE[p.state];
    let detail = p.detail;
    let action = p.action;
    if (p.id === 'notifications') {
      [badge, tone] = ({ granted: ['Allowed', 'ok'], denied: ['Blocked', 'err'], default: ['Not asked yet', 'warn'], unsupported: ['Not offered', 'muted'] } as const)[notify];
      if (notify === 'denied') detail = `${detail} This browser blocks them for this site: allow them in its site settings.`;
      if (notify === 'unsupported') detail = `${detail} This browser offers none here (a page not served over https gets none).`;
      if (notify !== 'default') action = null;
    }
    return { id: p.id, title: p.title, badge, tone, applies: p.applies, why: p.why, detail, fallback: p.fallback, settings: p.settings ?? null, action, files: p.files ?? [] };
  });
}

// ---- the admin password -------------------------------------------------------------------------

/** The password's state in words: the title and the line under it. */
export function passwordStatus(p: PasswordState, date: (t: number) => string = (t) => new Date(t * 1000).toLocaleDateString()): { title: string; sub: string; tone: Tone } {
  if (!p.set) return { title: 'No admin password', sub: 'Anyone who opens this page can change the fleet. Pairing, moves and imports work from this machine only.', tone: 'warn' };
  const since = p.set_at ? ` since ${date(p.set_at)}` : '';
  if (p.password && p.token) return { title: `Set${since}`, sub: 'The admin token in the controller’s env file works too, and stays the bearer for tools.', tone: 'ok' };
  if (p.password) return { title: `Set${since}`, sub: 'Changes need it, reading does not. Tools on this machine need none.', tone: 'ok' };
  return { title: 'The admin token', sub: 'The controller runs with an admin token (its env file). Set a password to sign in with something you can type; the token keeps working.', tone: 'ok' };
}

export interface PasswordForm {
  current: string;
  next: string;
  again: string;
}

/** What is wrong with the form (null: nothing), as the controller would refuse it. */
export function passwordError(p: PasswordState, f: PasswordForm): string | null {
  if (p.set && !f.current) return 'Your current password, or the admin token, first.';
  if (f.next.length < p.min_length || f.next.length > p.max_length) return `A password is ${p.min_length} to ${p.max_length} characters.`;
  if (!f.next.trim()) return 'A password is more than spaces.';
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(f.next)) return 'A password has no control characters.';
  if (f.next !== f.again) return 'The two new passwords differ.';
  return null;
}

/** Who may set it from here, or why not (the first one only from the controller's machine). */
export function passwordBlocked(p: PasswordState): string | null {
  return !p.set && !p.this_machine ? 'Set the first password from the controller’s own machine (its browser), or run `tandem-app password set` there.' : null;
}

// ---- the configuration file ---------------------------------------------------------------------

export const MAX_FILE = 4 * 1024 * 1024;

/** `tandemapp-fleet-config-2026-10-28.json` */
export function exportName(at: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `tandemapp-fleet-config-${at.getFullYear()}-${p(at.getMonth() + 1)}-${p(at.getDate())}.json`;
}

/** The chosen file read as a configuration, or why not (before anything is sent). */
export function readConfig(text: string, size: number): { doc: Record<string, unknown> } | { error: string } {
  if (size > MAX_FILE) return { error: 'The file is over 4 MiB: no export of this app is that big.' };
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch {
    return { error: 'This is not a JSON file.' };
  }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return { error: 'This is not a TandemApp fleet configuration.' };
  const kind = (doc as { kind?: unknown }).kind;
  if (kind === 'tandemapp-export') return { error: 'This is a controller move’s export (it holds keys): use Take the fleet instead.' };
  if (kind !== 'tandemapp-fleet-config') return { error: 'This is not a TandemApp fleet configuration.' };
  return { doc: doc as Record<string, unknown> };
}

const OP_TITLE: Record<ImportItem['op'], string> = { conflict: 'Cannot import', add: 'Added', change: 'Changed', remove: 'Removed', keep: 'Already so' };
export const OPS: ImportItem['op'][] = ['conflict', 'add', 'change', 'remove', 'keep'];
const KIND_WORD: Record<ImportItem['kind'], string> = { node: 'Spark', deployment: 'Deployment', rule: 'Alert rule', layout: 'Layout', setting: 'Setting' };
export const kindWord = (k: ImportItem['kind']) => KIND_WORD[k];

/** The plan's items by what happens to them, conflicts first; empty groups left out. */
export function importGroups(p: Pick<ImportPlan, 'items'>): { op: ImportItem['op']; title: string; items: ImportItem[] }[] {
  return OPS.map((op) => ({ op, title: OP_TITLE[op], items: p.items.filter((i) => i.op === op) })).filter((g) => g.items.length);
}

/** `2 added · 4 changed · 3 skipped`; nothing: `nothing to change` */
export function planSummary(c: ImportPlan['counts']): string {
  const parts = [c.add && `${c.add} added`, c.change && `${c.change} changed`, c.remove && `${c.remove} removed`, c.conflict && `${c.conflict} skipped`].filter(Boolean);
  return parts.length ? parts.join(' · ') : 'nothing to change';
}

/** the apply's button: `Import 6 changes` */
export function applyLabel(c: ImportPlan['counts']): string {
  const n = c.add + c.change + c.remove;
  return `Import ${n} change${n === 1 ? '' : 's'}`;
}

export function resultWords(r: ImportResult): string {
  const s = planSummary({ add: r.applied.add, change: r.applied.change, remove: r.applied.remove, keep: 0, conflict: r.skipped });
  return `Imported: ${s}.`;
}

// ---- updates ------------------------------------------------------------------------------------

/** The Updates block's line: the controller's release and the Sparks on another one. */
export function updateLine(release: string | null, nodes: Pick<FleetNode, 'alias' | 'version' | 'status'>[]): { text: string; behind: number; mixed: boolean } {
  const target = release ?? '';
  const behind = target ? nodes.filter((n) => n.status !== 'off' && n.version && needsUpdate(n.version, target)).length : 0;
  const mixed = mixedVersions(nodes) != null;
  const rel = release ? `This controller carries TandemLLM ${release}.` : 'This controller’s release is not known.';
  if (!nodes.length) return { text: `${rel} No Spark in the fleet yet.`, behind, mixed };
  if (!behind) return { text: `${rel} Every Spark that answers runs it.`, behind, mixed };
  return { text: `${rel} ${behind} Spark${behind === 1 ? '' : 's'} run${behind === 1 ? 's' : ''} another release${mixed ? ': the fleet runs more than one' : ''}.`, behind, mixed };
}
