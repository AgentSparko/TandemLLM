// The window kinds (VIS-256, QA VIS-257, src/windows/): every kind of the contract has a label, an icon,
// a group in the add menu, a ref to start with, a title from its ref and an element defined by its own
// chunk (never imported up front); a body's live data runs while it is on the page and stops when it
// leaves (Feed); a sandbox window shows the pane its ref names, and says why when it has none; a node
// that nothing reaches yet is named, never filled in.
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WINDOW_KINDS } from '../src/api/fleet';
import { GROUPS, KINDS, onNode, windowTitle } from '../src/windows/kinds';
import { paneFor } from '../src/windows/sandbox-pane';
import { Feed } from '../src/windows/base';
import { KEEP_TURNS } from '../src/windows/model';

const src = (f: string) => readFileSync(new URL(`../src/${f}`, import.meta.url), 'utf8');

describe('the kinds', () => {
  it('cover every window kind of the contract, each once in the add menu', () => {
    expect(Object.keys(KINDS).sort()).toEqual([...WINDOW_KINDS].sort());
    const listed = GROUPS.flatMap(([, ks]) => ks);
    expect(listed.sort()).toEqual([...WINDOW_KINDS].sort());
    expect(GROUPS.map(([g]) => g)).toEqual(['Fleet', 'Files and code', 'View', 'Model']);
    for (const [g, ks] of GROUPS) for (const k of ks) expect(KINDS[k].group, k).toBe(g);
  });
  it('each element is defined in the chunk its kind loads, and no chunk is imported up front', () => {
    const chunk: Record<string, string> = { 'qse-win-sandbox': 'windows/sandbox.ts', 'qse-win-logs': 'windows/fleet.ts', 'qse-win-chart': 'windows/fleet.ts', 'qse-win-inspector': 'windows/fleet.ts', 'qse-win-minichat': 'windows/model.ts', 'qse-win-miniplay': 'windows/model.ts' };
    for (const k of WINDOW_KINDS) {
      const tag = KINDS[k].tag;
      expect(chunk[tag], k).toBeDefined();
      expect(src(chunk[tag]), tag).toContain(`@customElement('${tag}')`);
      expect(typeof KINDS[k].load).toBe('function');
    }
    const kinds = src('windows/kinds.ts');
    for (const f of ['fleet', 'sandbox', 'model']) expect(kinds).toContain(`import('./${f}')`);
    for (const f of ['app.ts', 'ui/workspace.ts', 'windows/kinds.ts', 'main.ts']) expect(src(f), f).not.toMatch(/^import [^;]*['"](\.\.?\/)+windows\/(fleet|sandbox|model)['"]/m);
  });
  it('a new window’s title from its ref: a node by its alias, a file by its name', () => {
    const alias = (id: string) => ({ r3: 'R-3', desk: 'Desk' })[id] ?? id;
    const t = (k: (typeof WINDOW_KINDS)[number], ref: Record<string, unknown> = KINDS[k].init()) => windowTitle(k, ref, alias);
    expect(t('terminal')).toBe('Terminal · chat sandbox');
    expect(t('terminal', { node: 'r3' })).toBe('Terminal · R-3');
    expect(t('terminal', { node: null })).toBe('Terminal · a Spark');
    expect(t('files', { node: 'desk' })).toBe('Files · Desk');
    expect(t('logs')).toBe('Logs · all Sparks');
    expect(t('logs', { node: 'r3' })).toBe('Logs · R-3');
    expect(t('chart', { metric: 'power' })).toBe('Chart · power');
    expect(t('inspector', { node: 'r3' })).toBe('Inspector · R-3');
    expect(t('editor', { path: '/w/src/worker.py' })).toBe('Editor · worker.py');
    expect(t('preview', { ref: 'file:/workspace/site/index.html' })).toBe('Preview · index.html');
    expect(t('preview', { ref: 'port:8080', title: 'port 8080' })).toBe('port 8080');
    expect(t('plan')).toBe('Plan');
    expect(t('chat', { model: 'qwen3.8-27b' })).toBe('Chat · qwen3.8-27b');
    expect(onNode({ node: 'sandbox' })).toBeNull();
    expect(onNode({ node: 'r3' })).toBe('r3');
    for (const k of WINDOW_KINDS) expect(windowTitle(k, KINDS[k].init()).length, k).toBeGreaterThan(0);
  });
});

describe('a body’s live data (Feed)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('document', { visibilityState: 'visible', addEventListener: () => {}, removeEventListener: () => {} });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
  it('runs on mount, every interval while mounted, once more on a refresh, never after unmount', async () => {
    const fn = vi.fn(async () => {});
    const f = new Feed(fn, 1_000);
    expect(f.running).toBe(false);
    f.start();
    f.start();
    expect(f.running).toBe(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(fn).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(3_000);
    expect(fn).toHaveBeenCalledTimes(4);
    f.refresh();
    await vi.advanceTimersByTimeAsync(0);
    expect(fn).toHaveBeenCalledTimes(5);
    f.stop();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fn).toHaveBeenCalledTimes(5);
    expect(f.running).toBe(false);
    f.refresh();
    expect(fn).toHaveBeenCalledTimes(5);
  });
  it('every kind with live data stops it when its body leaves the page', () => {
    for (const f of ['windows/fleet.ts', 'windows/sandbox.ts']) {
      const s = src(f);
      const starts = s.match(/this\.feed\.start\(\)/g)?.length ?? 0;
      expect(starts, f).toBeGreaterThan(0);
      expect(s.match(/disconnectedCallback\(\): void \{[^}]*this\.feed\.stop\(\)/g)?.length, f).toBe(s.match(/new Feed\(/g)?.length);
    }
    // the chat windows stop a stream that runs when they go
    expect(src('windows/model.ts')).toMatch(/disconnectedCallback\(\): void \{[^}]*this\.ctrl\?\.abort\(\)/);
    expect(KEEP_TURNS).toBe(12);
  });
});

describe('a sandbox window', () => {
  const msg = (path: string) => ({ id: 'm', role: 'assistant', parts: [{ type: 'tool_call', id: 'c', name: 'write_file', input: JSON.stringify({ path }), status: 'done', output: { content: 'ok', is_error: false } }] }) as never;
  it('shows the pane its ref names, else the kind’s own', () => {
    expect(paneFor('terminal', {}, null)).toEqual({ kind: 'terminal', ref: 'shell', title: 'Terminal' });
    expect(paneFor('files', { path: '/workspace/site' }, null)).toMatchObject({ kind: 'files', ref: '/workspace/site' });
    expect(paneFor('editor', { path: '/workspace/a.py' }, null)).toMatchObject({ kind: 'editor', ref: '/workspace/a.py', title: 'a.py' });
    expect(paneFor('diff', {}, null)).toMatchObject({ kind: 'diff', ref: 'diff:' });
    expect(paneFor('plan', {}, null)).toMatchObject({ kind: 'plan', ref: 'md:plan' });
    expect(paneFor('context', {}, null)).toMatchObject({ kind: 'context', ref: '' });
    expect(paneFor('image', { ref: 'file:/workspace/a.png' }, null)).toMatchObject({ kind: 'image', ref: 'file:/workspace/a.png' });
  });
  it('a Preview shows the page the chat made last; with none, and a file kind without a file, it says why', () => {
    expect(paneFor('preview', {}, { messages: [msg('site/index.html')] })).toMatchObject({ kind: 'preview', ref: 'file:/workspace/site/index.html', title: 'index.html' });
    expect(paneFor('preview', {}, { messages: [] })).toHaveProperty('none');
    expect(paneFor('editor', {}, null)).toHaveProperty('none');
    for (const k of ['markdown', 'image', 'pdf', 'video', 'audio', 'binary'] as const) expect(paneFor(k, {}, null), k).toHaveProperty('none');
  });
  it('a Terminal or Files on a Spark names it and keeps it, and offers the chat sandbox; no shell is faked', () => {
    const s = src('windows/sandbox.ts');
    expect(s).toContain('through its TandemNode agent, which has no shell or file channel yet');
    expect(s).toContain('Use the chat sandbox');
    expect(s).toMatch(/data-unreachable=\$\{node\}/);
    expect(s).not.toMatch(/nvidia-smi|termExec/);
  });
});
