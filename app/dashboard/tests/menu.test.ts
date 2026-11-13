// TandemApp's menu (VIS-242, concept §4, src/lib/menu.ts): the groups and their order, the section
// of every view, the crumb, each section's sub-pages with the one you are on marked, and the scope
// that travels with a link to another Monitor page.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CONDUCTOR_SCREENS, MENU, MONITOR, PHONE_BAR, crumb, sectionHref, sectionOf, subPages } from '../src/lib/menu';
import { parseHash, VIEWS } from '../src/lib/router';

const NODES = [
  { id: 'desk', alias: 'Desk' },
  { id: 'r3', alias: 'R-3' },
];
const subs = (hash: string, nodes = NODES) => subPages(parseHash(hash), nodes).map((s) => `${s.on ? '*' : ''}${s.label} ${s.href}`);
// the scope is read from the page's hash
const loc = { hash: '' };
beforeEach(() => {
  loc.hash = '';
  vi.stubGlobal('location', loc);
});
afterEach(() => vi.unstubAllGlobals());

describe('the menu', () => {
  it('Chat, then Monitor, Fleet, Tune and Settings, in the concept’s order', () => {
    expect(MENU.map((g) => [g.label, g.items.map((i) => i.label)])).toEqual([
      [null, ['Chat']],
      ['Monitor', ['Overview', 'Performance', 'Usage', 'Hardware', 'Events & Alerts']],
      ['Fleet', ['Sparks', 'Models', 'API']],
      ['Tune', ['Conductor', 'Playground', 'Dev']],
      ['Settings', ['Get started', 'Add Spark', 'App']],
    ]);
    expect(MONITOR).toEqual(['overview', 'performance', 'usage', 'hardware', 'events']);
    expect(PHONE_BAR).toEqual(['chat', 'overview', 'performance']);
  });
  it('every view belongs to a section of the menu; today’s System, Backends and setup guide to theirs', () => {
    const ids = MENU.flatMap((g) => g.items.map((i) => i.id));
    for (const v of VIEWS) expect(ids, v).toContain(sectionOf(v));
    expect(sectionOf('system')).toBe('hardware');
    expect(sectionOf('backends')).toBe('api');
    expect(sectionOf('setup')).toBe('models');
  });
  it('the crumb names the group and the section', () => {
    expect(crumb('system')).toEqual({ group: 'Monitor', label: 'Hardware' });
    expect(crumb('setup')).toEqual({ group: 'Fleet', label: 'Models' });
    expect(crumb('chat')).toEqual({ group: null, label: 'Chat' });
    expect(crumb('add-spark')).toEqual({ group: 'Settings', label: 'Add Spark' });
  });
});

describe('sub-pages', () => {
  it('Performance: Live, Requests (Parallel before) and History (VIS-254)', () => {
    expect(subs('#/performance')).toEqual(['*Live #/performance', 'Requests #/performance?tab=requests', 'History #/performance?tab=history']);
    expect(subs('#/performance?tab=parallel')).toEqual(['Live #/performance', '*Requests #/performance?tab=requests', 'History #/performance?tab=history']);
    expect(subs('#/performance?tab=history')).toEqual(['Live #/performance', 'Requests #/performance?tab=requests', '*History #/performance?tab=history']);
  });
  it('Hardware: the fleet and this box’s System page', () => {
    expect(subs('#/hardware/system')).toEqual(['Fleet #/hardware', '*System #/hardware/system']);
  });
  it('Sparks: List, Topology and every node of the fleet', () => {
    expect(subs('#/sparks')).toEqual(['*List #/sparks', 'Topology #/sparks/topology', 'Desk #/sparks/desk', 'R-3 #/sparks/r3']);
    expect(subs('#/sparks/r3')).toEqual(['List #/sparks', 'Topology #/sparks/topology', 'Desk #/sparks/desk', '*R-3 #/sparks/r3']);
    expect(subs('#/sparks', [])).toEqual(['*List #/sparks', 'Topology #/sparks/topology']);
  });
  it('Models: Deployments, Serve a model (placement first, VIS-252), the Setup Guide (also from an old #/setup link) and Weights', () => {
    expect(subs('#/models')).toEqual(['*Deployments #/models', 'Serve a model #/models/place', 'Setup Guide #/models/serve', 'Weights #/models/weights']);
    expect(subs('#/setup/qwen3.8-27b/review')).toEqual(['Deployments #/models', 'Serve a model #/models/place', '*Setup Guide #/models/serve', 'Weights #/models/weights']);
    expect(subs('#/models/place?step=placement')).toEqual(['Deployments #/models', '*Serve a model #/models/place', 'Setup Guide #/models/serve', 'Weights #/models/weights']);
    expect(subs('#/models/weights')[3]).toBe('*Weights #/models/weights');
  });
  it('API: Endpoint and keys, and Backends', () => {
    expect(subs('#/api/backends?backend=x')).toEqual(['Endpoint and keys #/api', '*Backends #/api/backends']);
  });
  it('Conductor: its nine screens', () => {
    const c = subs('#/conductor?screen=memory');
    expect(c).toHaveLength(CONDUCTOR_SCREENS.length);
    expect(c[0]).toBe('Overview #/conductor');
    expect(c.filter((x) => x.startsWith('*'))).toEqual(['*Memory #/conductor?screen=memory']);
  });
  it('a section without sub-pages has none', () => {
    for (const h of ['#/chat/abc', '#/usage', '#/overview', '#/events', '#/dev', '#/playground', '#/add-spark', '#/app']) expect(subs(h), h).toEqual([]);
  });
});

describe('the scope travels with the Monitor links', () => {
  it('a Monitor page’s link keeps the scope; the others do not carry it', () => {
    loc.hash = '#/overview?scope=node%3Ar3&range=1h';
    expect(sectionHref('performance')).toBe('#/performance?scope=node%3Ar3&range=1h');
    expect(sectionHref('usage')).toBe('#/usage?scope=node%3Ar3&range=1h');
    expect(sectionHref('chat')).toBe('#/chat');
    expect(sectionHref('sparks')).toBe('#/sparks');
    loc.hash = '#/performance?scope=node%3Ar3&range=1h&live=0';
    expect(subs(loc.hash)).toEqual([
      '*Live #/performance?scope=node%3Ar3&range=1h&live=0',
      'Requests #/performance?scope=node%3Ar3&range=1h&live=0&tab=requests',
      'History #/performance?scope=node%3Ar3&range=1h&live=0&tab=history',
    ]);
  });
  it('the default scope adds nothing', () => {
    loc.hash = '#/overview';
    expect(sectionHref('hardware')).toBe('#/hardware');
  });
});
