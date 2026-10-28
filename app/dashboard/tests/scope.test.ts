// The global scope in the hash (VIS-242): parse and serialise round-trip, defaults stay out of the URL,
// unknown values read as the default, a scope the fleet does not know falls back to All, and the
// subscriber hears scope changes only.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_SCOPE, currentScope, onScope, parseScope, resolveScope, scopeParams, scopeSource, setScope, type Scope } from '../src/lib/scope';

const P = (s: string) => new URLSearchParams(s);
const toQuery = (s: Scope) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(scopeParams(s))) if (v != null) q.set(k, v);
  return q.toString();
};

describe('parse and serialise', () => {
  it('round-trips every kind, range and live state', () => {
    const all: Scope[] = [
      DEFAULT_SCOPE,
      { kind: 'node', id: 'r3', range: '6h', live: true },
      { kind: 'node', id: 'desk', range: '5m', live: false },
      { kind: 'deployment', id: 'qwen27', range: '7d', live: true },
      { kind: 'all', range: '24h', live: false },
    ];
    for (const s of all) expect(parseScope(P(toQuery(s)))).toEqual(s);
  });
  it('the example link reads as written; defaults stay out of the URL', () => {
    expect(parseScope(P('scope=node:r3&range=6h'))).toEqual({ kind: 'node', id: 'r3', range: '6h', live: true });
    expect(toQuery(DEFAULT_SCOPE)).toBe('');
    expect(toQuery({ kind: 'node', id: 'r3', range: '1h', live: false })).toBe('scope=node%3Ar3&range=1h&live=0');
  });
  it('ignores what it does not know', () => {
    for (const q of ['scope=r3', 'scope=host:mac', 'scope=node:', 'scope=node:../etc', 'scope=deployment:a%20b', 'scope=node:' + 'x'.repeat(65)]) expect(parseScope(P(q)).kind).toBe('all');
    expect(parseScope(P('range=3h')).range).toBe('6h');
    expect(parseScope(P('live=no')).live).toBe(true);
    expect(parseScope(P('scope=node:r3&range=1y&live=1&client=x'))).toEqual({ kind: 'node', id: 'r3', range: '6h', live: true });
  });
  it('the metrics source', () => {
    expect(scopeSource(DEFAULT_SCOPE)).toBe('all');
    expect(scopeSource({ kind: 'deployment', id: 'dsv41', range: '6h', live: true })).toBe('deployment:dsv41');
    expect(scopeSource({ kind: 'node', range: '6h', live: true })).toBe('all');
  });
  it('a node or deployment the fleet does not have falls back to All, keeping range and live', () => {
    const fleet = { nodes: [{ id: 'desk' }, { id: 'r3' }], deployments: [{ id: 'qwen27' }] };
    const r3: Scope = { kind: 'node', id: 'r3', range: '1h', live: false };
    expect(resolveScope(r3, fleet)).toBe(r3);
    expect(resolveScope({ ...r3, id: 'r9' }, fleet)).toEqual({ kind: 'all', range: '1h', live: false });
    expect(resolveScope({ kind: 'deployment', id: 'gone', range: '6h', live: true }, fleet)).toEqual(DEFAULT_SCOPE);
    expect(resolveScope({ kind: 'deployment', id: 'qwen27', range: '6h', live: true }, fleet).kind).toBe('deployment');
  });
});

describe('in the hash', () => {
  const loc = { hash: '' };
  let win: EventTarget;
  beforeEach(() => {
    loc.hash = '#/performance?client=k1';
    win = new EventTarget();
    vi.stubGlobal('location', loc);
    vi.stubGlobal('window', win);
    vi.stubGlobal('history', { replaceState: (_s: unknown, _t: string, url: string) => (loc.hash = url) });
    vi.stubGlobal('HashChangeEvent', class extends Event {});
  });
  afterEach(() => vi.unstubAllGlobals());

  it('setScope writes beside the view’s own params; a reload reads it back', () => {
    setScope({ kind: 'node', id: 'r3' });
    expect(loc.hash).toBe('#/performance?client=k1&scope=node%3Ar3');
    setScope({ range: '1h', live: false });
    expect(currentScope()).toEqual({ kind: 'node', id: 'r3', range: '1h', live: false });
    setScope({ kind: 'all', range: '6h', live: true });
    expect(loc.hash).toBe('#/performance?client=k1');
  });
  it('onScope hears scope changes, not the view’s other params; unsubscribe stops it', () => {
    const seen: string[] = [];
    const off = onScope((s) => seen.push(`${scopeSource(s)} ${s.range}`));
    setScope({ kind: 'deployment', id: 'dsv41' });
    loc.hash = '#/performance?client=k2&scope=deployment%3Adsv41';
    win.dispatchEvent(new Event('hashchange'));
    setScope({ range: '24h' });
    off();
    setScope({ range: '5m' });
    expect(seen).toEqual(['deployment:dsv41 6h', 'deployment:dsv41 24h']);
  });
});
