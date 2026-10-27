// Hash routing: #/usage?client=k:…&range=90d. Reload keeps the page and its filters. Every view has
// a path below it: #/chat/<id>, #/chat/settings/<section> (VIS-99), #/sparks/<node> (VIS-242). Three
// of today's views are sub-pages of a TandemApp section (concept §4) and live below it: the System
// view at #/hardware/system, Backends at #/api/backends and the setup guide at #/models/serve, with
// its own path below that (#/models/serve/<model>/<step>, VIS-78). The old hashes (#/system,
// #/backends, #/setup/…, Performance's ?tab=parallel) are rewritten in place, so a bookmark or a
// link from before keeps working.

export type View =
  | 'chat'
  | 'overview'
  | 'performance'
  | 'usage'
  | 'hardware'
  | 'system'
  | 'events'
  | 'sparks'
  | 'models'
  | 'setup'
  | 'api'
  | 'backends'
  | 'conductor'
  | 'playground'
  | 'dev'
  | 'add-spark'
  | 'app';
export const VIEWS: View[] = ['chat', 'overview', 'performance', 'usage', 'hardware', 'system', 'events', 'sparks', 'models', 'setup', 'api', 'backends', 'conductor', 'playground', 'dev', 'add-spark', 'app'];
/** the page an unknown or empty hash opens (Usage before VIS-242) */
export const HOME: View = 'overview';

/** a view that is a sub-page of a section: [the section's segment, its own] */
const BELOW: Partial<Record<View, [View, string]>> = { system: ['hardware', 'system'], backends: ['api', 'backends'], setup: ['models', 'serve'] };
/** the hashes of the views that moved below a section */
const MOVED: Record<string, View> = { system: 'system', backends: 'backends', setup: 'setup' };

export interface Route {
  view: View;
  params: URLSearchParams;
  /** the segments after the view (#/models/serve/qwen3.8-27b/weights -> ['qwen3.8-27b', 'weights']) */
  path: string[];
}

/** where a view lives: `hardware/system` for the System view, its own name for the rest */
export function viewPath(view: View): string {
  const b = BELOW[view];
  return b ? b.join('/') : view;
}

/** The route of a hash; an old hash reads as the one it is rewritten to. */
export function parseHash(hash = location.hash): Route {
  const h = (redirectHash(hash) ?? hash).replace(/^#\/?/, '');
  const [full, query = ''] = h.split('?');
  const [first, ...rest] = full.split('/');
  let view: View = HOME;
  let path: string[] = [];
  if ((VIEWS as string[]).includes(first) && !BELOW[first as View]) {
    view = first as View;
    path = rest;
    const below = (Object.keys(BELOW) as View[]).find((v) => BELOW[v]![0] === view && BELOW[v]![1] === rest[0]);
    if (below) {
      view = below;
      path = rest.slice(1);
    }
  }
  return { view, params: new URLSearchParams(query), path: path.filter(Boolean).map((x) => decodeURIComponent(x)) };
}

/** The hash a bookmark from before TandemApp now has, or null when it is current: #/system ->
 *  #/hardware/system, #/backends?backend=x -> #/api/backends?backend=x, #/setup/<model>/<step> ->
 *  #/models/serve/<model>/<step>, #/performance?tab=parallel -> ?tab=requests. */
export function redirectHash(hash: string): string | null {
  const h = hash.replace(/^#\/?/, '');
  const q = h.indexOf('?');
  const full = q < 0 ? h : h.slice(0, q);
  let query = q < 0 ? '' : h.slice(q + 1);
  const [first, ...rest] = full.split('/');
  const moved = MOVED[first];
  if (moved) return `#/${[viewPath(moved), ...rest.filter(Boolean)].join('/')}${query ? '?' + query : ''}`;
  if (first === 'performance') {
    const p = new URLSearchParams(query);
    if (p.get('tab') !== 'parallel') return null;
    p.set('tab', 'requests');
    query = p.toString();
    return `#/${full}?${query}`;
  }
  return null;
}

/** Rewrites an old hash in place (no new history entry, no hashchange). */
export function fixHash(): void {
  const to = redirectHash(location.hash);
  if (to) history.replaceState(history.state, '', to);
}

function query(params?: URLSearchParams | Record<string, string | null | undefined>): string {
  let q: URLSearchParams;
  if (params instanceof URLSearchParams) q = params;
  else {
    q = new URLSearchParams();
    for (const [k, v] of Object.entries(params ?? {})) if (v != null && v !== '') q.set(k, v);
  }
  const s = q.toString();
  return s ? '?' + s : '';
}

export function hashFor(view: View, params?: URLSearchParams | Record<string, string | null | undefined>): string {
  return `#/${viewPath(view)}${query(params)}`;
}

/** A route's hash: its view, its path and its params. */
export function routeHash(r: Route): string {
  return `#/${[viewPath(r.view), ...r.path.map((x) => encodeURIComponent(x))].join('/')}${query(r.params)}`;
}

export function navigate(view: View, params?: URLSearchParams | Record<string, string | null | undefined>): void {
  const next = hashFor(view, params);
  if (location.hash !== next) location.hash = next;
}

/** Update the current view's params without changing the view. */
export function setParams(patch: Record<string, string | null | undefined>): void {
  const r = parseHash();
  for (const [k, v] of Object.entries(patch)) {
    if (v == null || v === '') r.params.delete(k);
    else r.params.set(k, v);
  }
  const next = routeHash(r);
  if (location.hash !== next) history.replaceState(null, '', next);
  window.dispatchEvent(new HashChangeEvent('hashchange'));
}

/** Calls `fn` on every hash change, after an old hash was rewritten. */
export function onRoute(fn: (r: Route) => void): () => void {
  const h = () => {
    fixHash();
    fn(parseHash());
  };
  window.addEventListener('hashchange', h);
  return () => window.removeEventListener('hashchange', h);
}
