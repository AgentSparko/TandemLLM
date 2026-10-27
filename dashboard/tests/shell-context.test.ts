// The right context sidebar's registry and its selection slot (VIS-242, src/lib/context.ts), the left
// panel's override hook, the fleet readout in the top bar (src/lib/fleet-state.ts) and what the scope
// bar offers (src/lib/scope.ts scopeChoices).
import { describe, expect, it, onTestFinished, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { clearSelection, contextFor, currentSelection, onContext, onSidebars, openSelection, pageKey, panelFor, registerContext, registerPanel, setDefaultContext, setPage, setSidebarControl, sidebarShown, sidebarsChanged, toggleSidebar } from '../src/lib/context';
import type { Section } from '../src/lib/menu';
import { pageContext, type PageData } from '../src/ui/page-context';
import { readout, type FleetState } from '../src/lib/fleet-state';
import { DEFAULT_SCOPE, scopeChoices } from '../src/lib/scope';
import type { FleetAlert, FleetNode } from '../src/api/fleet';
import type { TemplateResult } from 'lit';

const T = (s: string) => s as unknown as TemplateResult;

describe('the context registry', () => {
  it('a page without its own content gets the default; a registration replaces it until unregistered', () => {
    setDefaultContext(() => ({ title: 'Fleet health', body: 'nodes' }));
    expect(contextFor('usage').title).toBe('Fleet health');
    const off = registerContext('usage', () => ({ title: 'Breakdown', body: 'by node' }));
    expect(contextFor('usage')).toEqual({ title: 'Breakdown', body: 'by node' });
    expect(contextFor('dev').title).toBe('Fleet health');
    off();
    expect(contextFor('usage').title).toBe('Fleet health');
  });
  it('an old unregister does not remove a newer registration', () => {
    const a = registerContext('dev', () => ({ title: 'A', body: '' }));
    const b = registerContext('dev', () => ({ title: 'B', body: '' }));
    a();
    expect(contextFor('dev').title).toBe('B');
    b();
  });
  it('the content is asked for when drawn, so a live figure is current', () => {
    let n = 0;
    const off = registerContext('performance', () => ({ title: `Now ${++n}`, body: '' }));
    expect(contextFor('performance').title).toBe('Now 1');
    expect(contextFor('performance').title).toBe('Now 2');
    off();
  });
  it('every change is heard: a registration, the default, a selection', () => {
    let heard = 0;
    const stop = onContext(() => heard++);
    const off = registerContext('system', () => ({ title: 'x', body: '' }));
    off();
    setDefaultContext(() => ({ title: 'Fleet health', body: '' }));
    setPage('system');
    openSelection('Alert', 'R-3 thermal');
    clearSelection();
    clearSelection();
    stop();
    registerContext('system', () => ({ title: 'y', body: '' }))();
    expect(heard).toBe(5);
  });
});

describe('the selection slot', () => {
  it('opens on the page it was opened on and closes when the page changes', () => {
    setPage(pageKey('chat', []));
    openSelection('R-3', 'thermal throttling');
    expect(currentSelection()).toMatchObject({ title: 'R-3', body: 'thermal throttling', page: 'chat' });
    // the same page (a scope change, a param) keeps it
    setPage(pageKey('chat', []));
    expect(currentSelection()?.title).toBe('R-3');
    setPage(pageKey('chat', ['c1']));
    expect(currentSelection()).toBeNull();
  });
  it('a new selection replaces the one before; clear closes it', () => {
    setPage('dev');
    openSelection('Request a1', 'one');
    openSelection('Request b2', 'two');
    expect(currentSelection()?.title).toBe('Request b2');
    clearSelection();
    expect(currentSelection()).toBeNull();
  });
  it('the page key is the view and its path, not its params', () => {
    expect(pageKey('setup', ['qwen3.8-27b', 'review'])).toBe('setup/qwen3.8-27b/review');
    expect(pageKey('usage', [])).toBe('usage');
  });
});

describe('the left panel’s override', () => {
  it('a view may show its own panel instead of the menu', () => {
    expect(panelFor('chat')).toBeNull();
    const off = registerPanel('chat', () => T('chat list'));
    expect(panelFor('chat')).toBe('chat list');
    expect(panelFor('usage')).toBeNull();
    off();
    expect(panelFor('chat')).toBeNull();
  });
});

const node = (id: string, colour: number, status: FleetNode['status'] = 'up', status_text = ''): FleetNode => ({ id, alias: id.toUpperCase(), colour, status, status_text }) as FleetNode;
const alert = (id: string, node: string, acked = false): FleetAlert => ({ id, node, acked, severity: 'warn', title: 't', since: 0, rule: 'r', detail: '', action: null, acked_at: null });
const fleet = (p: Partial<FleetState>): FleetState => ({ nodes: [], deployments: [], alerts: [], tps: {}, ...p });

describe('the fleet readout', () => {
  it('a swatch per Spark in its colour, the fleet’s rate summed, the active alerts', () => {
    const r = readout(fleet({ nodes: [node('desk', 1), node('r3', 4, 'alert', 'thermal')], alerts: [alert('a1', 'r3'), alert('a2', 'desk', true)], tps: { desk: 41.5, r3: 20 } }));
    expect(r.swatches.map((s) => [s.id, s.colour, s.alert])).toEqual([
      ['desk', 1, false],
      ['r3', 4, true],
    ]);
    expect(r.swatches[1].title).toBe('R3 · thermal');
    expect(r.tps).toBe(61.5);
    expect(r.alerts).toBe(1);
    expect(r.label).toBe('Fleet: 2 Sparks, 62 tok/s, 1 active alert. Open Events & Alerts');
  });
  it('a Spark is ringed by an alert on it even while its status says up; a draining or off one is faint', () => {
    const r = readout(fleet({ nodes: [node('a', 1), node('b', 2, 'draining'), node('c', 3, 'off')], alerts: [alert('x', 'a')] }));
    expect(r.swatches.map((s) => [s.alert, s.drain])).toEqual([
      [true, false],
      [false, true],
      [false, true],
    ]);
  });
  it('a fleet of one reads as one swatch; no rate yet is said, not guessed', () => {
    const r = readout(fleet({ nodes: [node('gold', 1)] }));
    expect(r.swatches).toHaveLength(1);
    expect(r.tps).toBeNull();
    expect(r.label).toBe('Fleet: 1 Spark, no rate yet, no alerts. Open Events & Alerts');
  });
});

describe('what the scope bar offers', () => {
  const f = { nodes: [{ id: 'desk' }, { id: 'r3' }], deployments: [{ id: 'qwen27' }] };
  it('All, every Spark, every deployment, the current one marked', () => {
    const c = scopeChoices(f, { kind: 'node', id: 'r3', range: '1h', live: true });
    expect(c.map((x) => `${x.on ? '*' : ''}${x.group ?? 'all'}:${x.id ?? ''}`)).toEqual(['all:', 'Sparks:desk', '*Sparks:r3', 'Deployments:qwen27']);
    expect(c[3].scope).toEqual({ kind: 'deployment', id: 'qwen27' });
  });
  it('a scope the fleet does not know marks All', () => {
    expect(scopeChoices(f, { kind: 'node', id: 'gone', range: '6h', live: true }).filter((x) => x.on).map((x) => x.scope.kind)).toEqual(['all']);
  });
  it('a fleet of one offers nothing to pick', () => {
    expect(scopeChoices({ nodes: [{ id: 'gold' }], deployments: [{ id: 'q' }, { id: 'w' }] }, DEFAULT_SCOPE)).toEqual([]);
  });
});

// ---- VIS-256: the sidebars on every page, Chat's moved into the shell's -------------------------------
describe('the sidebars on every page (VIS-256)', () => {
  it('a body may be an element the view keeps and feeds itself; the left panel too', () => {
    const el = { nodeType: 1 } as unknown as Node;
    const off = registerContext('chat', () => ({ title: 'Context', body: el }));
    expect(contextFor('chat').body).toBe(el);
    const offP = registerPanel('chat', () => el);
    expect(panelFor('chat')).toBe(el);
    off();
    offP();
    expect(panelFor('chat')).toBeNull();
  });
  it('the shell lends its toggles; a view hears the sidebars open and close', () => {
    const shown = { rail: true, ctx: false };
    setSidebarControl({ toggle: (w) => (shown[w] = !shown[w]), shown: (w) => shown[w] });
    let heard = 0;
    const stop = onSidebars(() => heard++);
    expect(sidebarShown('ctx')).toBe(false);
    toggleSidebar('ctx');
    expect(sidebarShown('ctx')).toBe(true);
    sidebarsChanged();
    expect(heard).toBe(1);
    stop();
    setSidebarControl(null);
    expect(sidebarShown('rail')).toBe(false);
    toggleSidebar('rail');
  });
  it('every page has its own content: a title of its own and sections from what the shell reads', () => {
    vi.stubGlobal('location', { origin: 'http://box:8000' });
    onTestFinished(() => void vi.unstubAllGlobals());
    const sections: Section[] = ['overview', 'performance', 'usage', 'hardware', 'events', 'sparks', 'models', 'api', 'conductor', 'playground', 'dev', 'add-spark', 'app'];
    const f = { nodes: [node('desk', 1), node('r3', 4, 'alert', 'thermal')], deployments: [], alerts: [alert('a1', 'r3')], tps: { desk: 40 } } as FleetState;
    const data = { route: { view: 'overview', path: [], params: new URLSearchParams() }, fleet: f, summary: null, meta: { version: '0.15.0', code: null }, layouts: [], theme: 'dark', toggleTheme: () => {} } as unknown as PageData;
    const titles = sections.map((s) => pageContext(s, { ...data, route: { ...data.route, view: s } } as PageData).title);
    expect(new Set(titles).size).toBe(sections.length);
    expect(titles.slice(0, 3)).toEqual(['Fleet now', 'Now, per Spark', 'Today']);
    // without the fleet API the page still says something of its own
    for (const s of sections) expect(pageContext(s, { ...data, fleet: null }).title, s).toBeTruthy();
  });
  it('Chat’s list is the shell’s left panel, its Context panel with its eight sections the right sidebar, its panes the page’s windows', () => {
    const view = readFileSync(new URL('../src/chat/view.ts', import.meta.url), 'utf8');
    expect(view).toContain("registerPanel('chat', () => this.railHost!)");
    expect(view).toContain("registerContext('chat', () => ({ title: 'Context', body: this.ctxHost! }))");
    expect(view).toContain("registerWindows('chat', this.source)");
    // no column of its own any more: one right column on Chat
    expect(view).not.toMatch(/<aside class="ch-(rail|ctx)"/);
    expect(view).not.toContain('qse-chat-panes');
    const panel = readFileSync(new URL('../src/chat/context-panel.ts', import.meta.url), 'utf8');
    for (const sec of ['renderWindow', 'renderWorkers', 'renderNow', 'renderModel', 'renderSkills', 'renderMemory', 'renderCompaction', 'renderUsage']) expect(panel, sec).toContain(`\${this.${sec}(c)}`);
    // the header's buttons drive the shell's sidebars
    expect(view).toContain("@click=${() => toggleSidebar('rail')}");
    expect(view).toContain("@click=${() => toggleSidebar('ctx')}");
  });
});
