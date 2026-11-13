// The global scope (VIS-242): what every monitoring chart shows -- all Sparks, one Spark or one
// deployment -- over which time range, live or paused. It lives in the hash query beside the view's
// own filters (#/performance?scope=node:r3&range=1h&live=0), so a reload and a shared link keep it.
// Defaults stay out of the URL; an unknown value reads as the default.

import { RANGE_KEYS, type Range } from '../api/fleet';
import { parseHash, setParams } from './router';

export type ScopeKind = 'all' | 'node' | 'deployment';

export interface Scope {
  kind: ScopeKind;
  /** the node or deployment id; absent for `all` */
  id?: string;
  range: Range;
  /** false: the charts hold still at the moment of the pause */
  live: boolean;
}

export const DEFAULT_RANGE: Range = '6h';
export const DEFAULT_SCOPE: Scope = { kind: 'all', range: DEFAULT_RANGE, live: true };

const ID = /^[a-z0-9][a-z0-9._-]{0,63}$/i;

export function parseScope(params: URLSearchParams): Scope {
  const out: Scope = { ...DEFAULT_SCOPE };
  const m = /^(node|deployment):(.+)$/.exec(params.get('scope') ?? '');
  if (m && ID.test(m[2])) {
    out.kind = m[1] as ScopeKind;
    out.id = m[2];
  }
  const r = params.get('range');
  if (r && (RANGE_KEYS as string[]).includes(r)) out.range = r as Range;
  if (params.get('live') === '0') out.live = false;
  return out;
}

/** The scope's hash params: null for a default, so setParams drops it. */
export function scopeParams(s: Scope): Record<string, string | null> {
  return {
    scope: s.kind !== 'all' && s.id ? `${s.kind}:${s.id}` : null,
    range: s.range !== DEFAULT_RANGE ? s.range : null,
    live: s.live ? null : '0',
  };
}

/** The `source` of the fleet metrics API: all, node:<id>, deployment:<id>. */
export function scopeSource(s: Scope): string {
  return s.kind !== 'all' && s.id ? `${s.kind}:${s.id}` : 'all';
}

export function sameScope(a: Scope, b: Scope): boolean {
  return scopeSource(a) === scopeSource(b) && a.range === b.range && a.live === b.live;
}

/** A scope whose node or deployment is not in the fleet (forgotten, a stale link) falls back to All. */
export function resolveScope(s: Scope, fleet: { nodes: { id: string }[]; deployments: { id: string }[] }): Scope {
  if (s.kind === 'node' && fleet.nodes.some((n) => n.id === s.id)) return s;
  if (s.kind === 'deployment' && fleet.deployments.some((d) => d.id === s.id)) return s;
  return s.kind === 'all' ? s : { kind: 'all', range: s.range, live: s.live };
}

export function currentScope(): Scope {
  return parseScope(parseHash().params);
}

/** Change part of the scope; the view and its own params stay. */
export function setScope(patch: Partial<Scope>): void {
  const next = { ...currentScope(), ...patch };
  if (next.kind === 'all') delete next.id;
  setParams(scopeParams(next));
}

export interface ScopeChoice {
  scope: Pick<Scope, 'kind' | 'id'>;
  /** the group in the dropdown: null for All */
  group: 'Sparks' | 'Backends' | 'Deployments' | null;
  /** the node or deployment id; absent for All */
  id?: string;
  on: boolean;
}

/** What the scope bar offers: All, each Spark, each backend on no node (SRV-749), each deployment, the current one marked (a scope the
 *  fleet does not know marks All, as it reads). A fleet of one offers nothing: there is no choice. */
export function scopeChoices(fleet: { nodes: { id: string; class?: string }[]; deployments: { id: string }[] }, s: Scope): ScopeChoice[] {
  if (fleet.nodes.length < 2) return [];
  const r = resolveScope(s, fleet);
  return [
    { scope: { kind: 'all' }, group: null, on: r.kind === 'all' },
    ...fleet.nodes.map((n): ScopeChoice => ({ scope: { kind: 'node', id: n.id }, group: n.class === 'backend' ? 'Backends' : 'Sparks', id: n.id, on: r.kind === 'node' && r.id === n.id })),
    ...fleet.deployments.map((d): ScopeChoice => ({ scope: { kind: 'deployment', id: d.id }, group: 'Deployments', id: d.id, on: r.kind === 'deployment' && r.id === d.id })),
  ];
}

/** Calls `fn` when the scope changes (not on every hash change); returns the unsubscribe. */
export function onScope(fn: (s: Scope) => void): () => void {
  let last = currentScope();
  const h = () => {
    const s = currentScope();
    if (sameScope(s, last)) return;
    last = s;
    fn(s);
  };
  window.addEventListener('hashchange', h);
  return () => window.removeEventListener('hashchange', h);
}
