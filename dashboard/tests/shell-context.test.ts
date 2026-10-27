// The right context sidebar's registry and its selection slot (VIS-242, src/lib/context.ts), the left
// panel's override hook, the fleet readout in the top bar (src/lib/fleet-state.ts) and what the scope
// bar offers (src/lib/scope.ts scopeChoices).
import { describe, expect, it } from 'vitest';
import { clearSelection, contextFor, currentSelection, onContext, openSelection, pageKey, panelFor, registerContext, registerPanel, setDefaultContext, setPage } from '../src/lib/context';
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
