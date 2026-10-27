// The routes of TandemApp's shell (VIS-242, src/lib/router.ts): every view parses from its hash and
// back, the three views below a section live there, every old hash is rewritten in place (bookmarks
// and deep links from before keep working), and an unknown hash opens Overview.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HOME, VIEWS, fixHash, hashFor, onRoute, parseHash, redirectHash, routeHash, setParams, viewPath } from '../src/lib/router';

describe('parse', () => {
  it('every view round-trips through its hash', () => {
    for (const v of VIEWS) {
      const r = parseHash(hashFor(v));
      expect(r.view, v).toBe(v);
      expect(r.path).toEqual([]);
    }
  });
  it('System, Backends and the setup guide live below their sections', () => {
    expect(viewPath('system')).toBe('hardware/system');
    expect(viewPath('backends')).toBe('api/backends');
    expect(viewPath('setup')).toBe('models/serve');
    expect(parseHash('#/hardware/system').view).toBe('system');
    expect(parseHash('#/hardware').view).toBe('hardware');
    expect(parseHash('#/api/backends?backend=lms').params.get('backend')).toBe('lms');
    expect(parseHash('#/models/serve/qwen3.8-27b/weights')).toMatchObject({ view: 'setup', path: ['qwen3.8-27b', 'weights'] });
    expect(parseHash('#/models/weights')).toMatchObject({ view: 'models', path: ['weights'] });
  });
  it('a path below any view: a chat, its settings, a Spark', () => {
    expect(parseHash('#/chat/c%201')).toMatchObject({ view: 'chat', path: ['c 1'] });
    expect(parseHash('#/chat/settings/models').path).toEqual(['settings', 'models']);
    expect(parseHash('#/sparks/r3')).toMatchObject({ view: 'sparks', path: ['r3'] });
  });
  it('an empty or unknown hash opens Overview (Usage before TandemApp)', () => {
    expect(HOME).toBe('overview');
    for (const h of ['', '#', '#/', '#/nope', '#/serve', '#/system-x']) expect(parseHash(h).view, h).toBe('overview');
  });
  it('routeHash writes the view, its path and its params', () => {
    expect(routeHash({ view: 'setup', path: ['qwen3.8-27b', 'review'], params: new URLSearchParams() })).toBe('#/models/serve/qwen3.8-27b/review');
    expect(routeHash({ view: 'chat', path: ['a b'], params: new URLSearchParams('x=1') })).toBe('#/chat/a%20b?x=1');
    expect(hashFor('backends', { backend: 'lms', empty: '' })).toBe('#/api/backends?backend=lms');
  });
});

describe('old hashes', () => {
  const cases: [string, string][] = [
    ['#/system', '#/hardware/system'],
    ['#/backends', '#/api/backends'],
    ['#/backends?backend=lms', '#/api/backends?backend=lms'],
    ['#/setup', '#/models/serve'],
    ['#/setup/qwen3.8-27b/weights', '#/models/serve/qwen3.8-27b/weights'],
    ['#/setup/qwen3.8-27b/overview?x=1', '#/models/serve/qwen3.8-27b/overview?x=1'],
    ['#/performance?tab=parallel', '#/performance?tab=requests'],
    ['#/performance?tab=parallel&scope=node%3Ar3', '#/performance?tab=requests&scope=node%3Ar3'],
  ];
  it('every one is rewritten to its new place, its path and params kept', () => {
    for (const [from, to] of cases) expect(redirectHash(from), from).toBe(to);
  });
  it('and reads as the page it moved to', () => {
    for (const [from, to] of cases) expect(parseHash(from), from).toEqual(parseHash(to));
  });
  it('a current hash is left alone', () => {
    for (const h of ['#/usage', '#/chat/abc', '#/performance', '#/performance?tab=requests', '#/hardware/system', '#/models/serve/m/review', '#/overview?scope=node%3Ar3', '']) expect(redirectHash(h), h).toBeNull();
  });
});

describe('in the browser', () => {
  const loc = { hash: '' };
  let win: EventTarget;
  const replaced: string[] = [];
  beforeEach(() => {
    replaced.length = 0;
    win = new EventTarget();
    vi.stubGlobal('location', loc);
    vi.stubGlobal('window', win);
    vi.stubGlobal('history', {
      state: null,
      replaceState: (_s: unknown, _t: string, url: string) => {
        replaced.push(url);
        loc.hash = url;
      },
    });
    vi.stubGlobal('HashChangeEvent', class extends Event {});
  });
  afterEach(() => vi.unstubAllGlobals());

  it('fixHash rewrites an old bookmark in place, without a new history entry', () => {
    loc.hash = '#/setup/qwen3.8-27b/weights';
    fixHash();
    expect(loc.hash).toBe('#/models/serve/qwen3.8-27b/weights');
    expect(replaced).toEqual(['#/models/serve/qwen3.8-27b/weights']);
    fixHash();
    expect(replaced).toHaveLength(1);
  });
  it('onRoute hears the rewritten route when an old link is followed', () => {
    const seen: string[] = [];
    const off = onRoute((r) => seen.push(`${r.view} ${r.path.join('/')} ${r.params}`));
    loc.hash = '#/system';
    win.dispatchEvent(new Event('hashchange'));
    loc.hash = '#/performance?tab=parallel';
    win.dispatchEvent(new Event('hashchange'));
    off();
    expect(seen).toEqual(['system  ', 'performance  tab=requests']);
    expect(loc.hash).toBe('#/performance?tab=requests');
  });
  it('setParams keeps a sub-page and its path', () => {
    loc.hash = '#/models/serve/qwen3.8-27b/review';
    setParams({ x: '1' });
    expect(loc.hash).toBe('#/models/serve/qwen3.8-27b/review?x=1');
    loc.hash = '#/api/backends?backend=a';
    setParams({ backend: null });
    expect(loc.hash).toBe('#/api/backends');
  });
});
