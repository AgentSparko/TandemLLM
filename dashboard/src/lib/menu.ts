// The menu (concept §4, VIS-242): Chat, then four groups -- Monitor, Fleet, Tune, Settings -- and the
// sub-pages of the section you are in. One model for the icon strip, the panel beside it, the phone's
// bottom bar and its More sheet, and the crumb in the top bar. Kept pure so it can be tested; the
// shell (app.ts) draws it.

import type { Route, View } from './router';
import { currentScope, scopeParams } from './scope';

export type Section = 'chat' | 'overview' | 'performance' | 'usage' | 'hardware' | 'events' | 'sparks' | 'models' | 'api' | 'conductor' | 'playground' | 'dev' | 'add-spark' | 'app';

export interface MenuItem {
  id: Section;
  label: string;
}

export interface MenuGroup {
  /** null for Chat, which stands above the groups */
  label: string | null;
  items: MenuItem[];
}

export const MENU: MenuGroup[] = [
  { label: null, items: [{ id: 'chat', label: 'Chat' }] },
  {
    label: 'Monitor',
    items: [
      { id: 'overview', label: 'Overview' },
      { id: 'performance', label: 'Performance' },
      { id: 'usage', label: 'Usage' },
      { id: 'hardware', label: 'Hardware' },
      { id: 'events', label: 'Events & Alerts' },
    ],
  },
  {
    label: 'Fleet',
    items: [
      { id: 'sparks', label: 'Sparks' },
      { id: 'models', label: 'Models' },
      { id: 'api', label: 'API' },
    ],
  },
  {
    label: 'Tune',
    items: [
      { id: 'conductor', label: 'Conductor' },
      { id: 'playground', label: 'Playground' },
      { id: 'dev', label: 'Dev' },
    ],
  },
  {
    label: 'Settings',
    items: [
      { id: 'add-spark', label: 'Add Spark' },
      { id: 'app', label: 'App' },
    ],
  },
];

/** the pages that watch the fleet: the scope bar stands above them and their links keep the scope */
export const MONITOR: Section[] = ['overview', 'performance', 'usage', 'hardware', 'events'];

/** the phone's bottom bar: Chat one tap away, the two pages watched daily, then Windows and More */
export const PHONE_BAR: Section[] = ['chat', 'overview', 'performance'];

/** the section a view belongs to: today's System, Backends and setup guide sit below theirs */
const PARENT: Partial<Record<View, Section>> = { system: 'hardware', backends: 'api', setup: 'models' };

export function sectionOf(view: View): Section {
  return PARENT[view] ?? (view as Section);
}

export function menuItem(id: Section): MenuItem {
  for (const g of MENU) for (const it of g.items) if (it.id === id) return it;
  return { id, label: id };
}

/** what the top bar's crumb says: the group and the page */
export function crumb(view: View): { group: string | null; label: string } {
  const s = sectionOf(view);
  const g = MENU.find((x) => x.items.some((it) => it.id === s));
  return { group: g?.label ?? null, label: menuItem(s).label };
}

/** the scope's params (what is not the default) and `extra`, as a query */
function withScope(extra: Record<string, string> = {}): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...scopeParams(currentScope()), ...extra })) if (v != null) q.set(k, v);
  const s = q.toString();
  return s ? '?' + s : '';
}

/** A section's link. A Monitor page keeps the scope you set on the one before it. */
export function sectionHref(id: Section): string {
  return `#/${id}${MONITOR.includes(id) ? withScope() : ''}`;
}

/** The nine screens of the Conductor (VIS-42): its own tab strip and the panel's sub-pages. */
export const CONDUCTOR_SCREENS = [
  { id: 'overview', label: 'Overview' },
  { id: 'routing', label: 'Routing' },
  { id: 'monitor', label: 'Monitor' },
  { id: 'profiles', label: 'Profiles' },
  { id: 'packages', label: 'Packages' },
  { id: 'deciders', label: 'Deciders' },
  { id: 'sessions', label: 'Sessions' },
  { id: 'memory', label: 'Memory' },
  { id: 'compare', label: 'Compare' },
] as const;
export type ConductorScreen = (typeof CONDUCTOR_SCREENS)[number]['id'];

export interface SubPage {
  label: string;
  href: string;
  on: boolean;
}

/** The sub-pages of the route's section, the one you are on marked; [] for a section without
 *  any. Sparks lists every node of the fleet after its List and Topology. */
export function subPages(r: Route, nodes: { id: string; alias: string }[] = []): SubPage[] {
  const tab = r.params.get('tab');
  const p0 = r.path[0] ?? null;
  switch (sectionOf(r.view)) {
    case 'performance':
      return [
        { label: 'Live', href: `#/performance${withScope()}`, on: tab !== 'requests' },
        { label: 'Requests', href: `#/performance${withScope({ tab: 'requests' })}`, on: tab === 'requests' },
      ];
    case 'hardware':
      return [
        { label: 'Fleet', href: `#/hardware${withScope()}`, on: r.view === 'hardware' },
        { label: 'System', href: '#/hardware/system', on: r.view === 'system' },
      ];
    case 'sparks':
      return [
        { label: 'List', href: '#/sparks', on: p0 === null },
        { label: 'Topology', href: '#/sparks/topology', on: p0 === 'topology' },
        ...nodes.map((n) => ({ label: n.alias, href: `#/sparks/${encodeURIComponent(n.id)}`, on: p0 === n.id })),
      ];
    case 'models':
      return [
        { label: 'Deployments', href: '#/models', on: r.view === 'models' && p0 === null },
        { label: 'Serve a model', href: '#/models/serve', on: r.view === 'setup' },
        { label: 'Weights', href: '#/models/weights', on: r.view === 'models' && p0 === 'weights' },
      ];
    case 'api':
      return [
        { label: 'Endpoint and keys', href: '#/api', on: r.view === 'api' },
        { label: 'Backends', href: '#/api/backends', on: r.view === 'backends' },
      ];
    case 'conductor': {
      const screen = r.params.get('screen') ?? 'overview';
      return CONDUCTOR_SCREENS.map((s) => ({ label: s.label, href: s.id === 'overview' ? '#/conductor' : `#/conductor?screen=${s.id}`, on: s.id === screen }));
    }
    default:
      return [];
  }
}
