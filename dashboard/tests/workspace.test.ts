// The workspace store (VIS-256, QA VIS-257, src/lib/workspace.ts): a page's layout per device class,
// read from the browser at once and from the controller when it saved later, saved to both a moment
// after a change (and at once when the tab goes); a server without the fleet API asked once; a storage
// that throws, or a browser whose storage property throws, leaving the defaults and a working page;
// the sidebars' widths one per device and their open state each page's own; Reset for a page and for
// all; what 2.2 saved carried over; a page's own windows (Chat's panes) placed and kept.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../src/api/client';
import type { Layout, LayoutDevice } from '../src/api/fleet';
import { open, move, resizeSide, setSide } from '../src/lib/wm';
import {
  change,
  configureWorkspace,
  currentLayout,
  flush,
  onWorkspace,
  openWindowMenu,
  pageSource,
  registerWindows,
  resetAll,
  resetPage,
  savedLayouts,
  setTray,
  showPage,
  shownLayout,
  trayOpen,
  windowMenu,
  type WindowSource,
} from '../src/lib/workspace';

const mem = (o: Record<string, string> = {}) => {
  const m = new Map<string, string>(Object.entries(o));
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k), m };
};
const saved = (s: ReturnType<typeof mem>) => JSON.parse(s.m.get('tandemapp.layouts') ?? '{}') as Record<string, Layout>;

function remote(answers: Record<string, Layout | 404> = {}) {
  const puts: Layout[] = [];
  const gets: string[] = [];
  return {
    puts,
    gets,
    get: async (p: string, d: LayoutDevice) => {
      gets.push(`${p}|${d}`);
      const a = answers[`${p}|${d}`];
      if (a === 404) throw new ApiError('/v1/fleet/layouts', 404, 'not found');
      if (a) return a;
      return { page: p, device: d, rail: { open: true, w: 248 }, ctx: { open: true, w: 340 }, windows: [], docks: { right: { f: 0.38, act: null }, bottom: { f: 0.34, act: null } }, saved_at: null };
    },
    put: async (l: Layout) => {
      puts.push(l);
      return l;
    },
  };
}

let now = 1_800_000_000_000;
const tick = async () => {
  await vi.advanceTimersByTimeAsync(500);
};

beforeEach(() => {
  vi.useFakeTimers();
  now = 1_800_000_000_000;
});
afterEach(() => {
  vi.useRealTimers();
});

describe('a page’s layout', () => {
  it('a first visit gets the page’s defaults; a change is saved in the browser and on the controller', async () => {
    const s = mem();
    const r = remote();
    configureWorkspace({ storage: () => s, remote: r, now: () => now, saveMs: 300 });
    let heard = 0;
    const stop = onWorkspace(() => heard++);
    showPage('overview', 'desk', 1440);
    expect(currentLayout().windows.map((w) => w.kind)).toEqual(['logs', 'chart']);
    expect(currentLayout().saved_at).toBeNull();
    expect(heard).toBeGreaterThan(0);
    change((l) => open(l, 'inspector', { node: 'r3' }, 'right', 'overview').layout);
    expect(saved(s)['overview|desk']).toBeUndefined();
    await tick();
    expect(saved(s)['overview|desk'].windows.map((w) => w.kind)).toEqual(['logs', 'chart', 'inspector']);
    expect(saved(s)['overview|desk'].saved_at).toBe(now / 1000);
    expect(r.puts.at(-1)).toMatchObject({ page: 'overview', device: 'desk', saved_at: now / 1000 });
    stop();
  });
  it('comes back from the browser after a reload, the same windows in the same places with the same targets', async () => {
    const s = mem();
    configureWorkspace({ storage: () => s, remote: null, now: () => now, saveMs: 300 });
    showPage('hardware', 'desk', 1440);
    change((l) => open(l, 'terminal', { node: 'r1' }, 'bottom', 'hardware').layout);
    change((l) => open(l, 'logs', { node: 'r3' }, 'right', 'hardware').layout);
    await tick();
    const before = currentLayout();
    configureWorkspace({ storage: () => s, remote: null });
    showPage('hardware', 'desk', 1440);
    expect(currentLayout()).toEqual(before);
    expect(currentLayout().windows.map((w) => `${w.kind}:${w.ref.node}@${w.at}`)).toEqual(['terminal:null@bottom', 'terminal:r1@bottom', 'logs:r3@right']);
  });
  it('the controller’s copy wins when it was saved later; an older one does not', async () => {
    const s = mem();
    configureWorkspace({ storage: () => s, remote: null, now: () => now, saveMs: 300 });
    showPage('usage', 'desk', 1440);
    change((l) => open(l, 'logs', {}, 'right', 'usage').layout);
    await tick();
    const local = currentLayout();
    const newer: Layout = { ...local, windows: [], saved_at: local.saved_at! + 60 };
    const older: Layout = { ...local, windows: [], saved_at: local.saved_at! - 60 };
    configureWorkspace({ storage: () => s, remote: remote({ 'usage|desk': older }) });
    showPage('usage', 'desk', 1440);
    await tick();
    expect(currentLayout().windows).toHaveLength(1);
    configureWorkspace({ storage: () => s, remote: remote({ 'usage|desk': newer }) });
    showPage('usage', 'desk', 1440);
    await tick();
    expect(currentLayout().windows).toHaveLength(0);
    expect(saved(s)['usage|desk'].saved_at).toBe(newer.saved_at);
  });
  it('a server without the fleet API answers 404 once and is not asked again', async () => {
    const s = mem();
    const r = remote({ 'overview|desk': 404 });
    configureWorkspace({ storage: () => s, remote: r, now: () => now, saveMs: 300 });
    showPage('overview', 'desk', 1440);
    await tick();
    change((l) => setSide(l, 'ctx', true));
    showPage('usage', 'desk', 1440);
    await tick();
    expect(r.gets).toEqual(['overview|desk']);
    expect(r.puts).toEqual([]);
    expect(saved(s)['overview|desk'].ctx.open).toBe(true);
  });
  it('a waiting change is saved at once when the page goes, and by flush', async () => {
    const s = mem();
    configureWorkspace({ storage: () => s, remote: null, now: () => now, saveMs: 300 });
    showPage('dev', 'desk', 1440);
    change((l) => setSide(l, 'rail', false));
    showPage('usage', 'desk', 1440);
    expect(saved(s)['dev|desk'].rail.open).toBe(false);
    change((l) => setSide(l, 'rail', false));
    flush();
    expect(saved(s)['usage|desk'].rail.open).toBe(false);
  });
});

describe('devices and sidebars', () => {
  it('each device class keeps its own layout', async () => {
    const s = mem();
    configureWorkspace({ storage: () => s, remote: null, now: () => now, saveMs: 300 });
    showPage('overview', 'desk', 1440);
    change((l) => open(l, 'inspector', {}, 'float', 'overview').layout);
    showPage('overview', 'phone', 375);
    expect(currentLayout().device).toBe('phone');
    expect(currentLayout().windows.map((w) => w.kind)).toEqual(['logs', 'chart']);
    change((l) => move(l, l.windows[0].id, 'min'));
    await tick();
    expect(Object.keys(saved(s)).sort()).toEqual(['overview|desk', 'overview|phone']);
    expect(saved(s)['overview|desk'].windows).toHaveLength(3);
    expect(saved(s)['overview|phone'].windows[0].at).toBe('min');
  });
  it('the widths are one per device, the open state each page’s own', async () => {
    const s = mem();
    configureWorkspace({ storage: () => s, remote: null, now: () => now, saveMs: 300 });
    showPage('overview', 'desk', 1440);
    expect(currentLayout().ctx.open).toBe(false);
    change((l) => resizeSide(setSide(l, 'ctx', true), 'ctx', 416));
    await tick();
    showPage('usage', 'desk', 1440);
    expect(currentLayout().ctx).toEqual({ open: false, w: 416 });
    showPage('overview', 'desk', 1440);
    expect(currentLayout().ctx).toEqual({ open: true, w: 416 });
    showPage('overview', 'tablet', 900);
    expect(currentLayout().ctx.w).toBe(340);
    // Chat starts with its list open, its Context panel from 1180 px
    showPage('chat', 'desk', 1440);
    expect([currentLayout().rail.open, currentLayout().ctx.open]).toEqual([true, true]);
  });
  it('what 2.2 saved for every page, and Chat’s own list and Context, carry over on a first visit', () => {
    const s = mem({ 'tandemapp.layout': JSON.stringify({ rail: { open: false, w: 300 }, ctx: { open: true, w: 400 }, full: { rail: false, ctx: false } }), 'qse.chat.ctx': 'closed' });
    configureWorkspace({ storage: () => s, remote: null });
    showPage('usage', 'desk', 1440);
    expect([currentLayout().rail, currentLayout().ctx]).toEqual([
      { open: false, w: 300 },
      { open: true, w: 400 },
    ]);
    showPage('chat', 'desk', 1440);
    expect([currentLayout().rail.open, currentLayout().ctx.open]).toEqual([true, false]);
  });
});

describe('a storage that throws', () => {
  const throwing = {
    getItem: () => {
      throw new Error('SecurityError');
    },
    setItem: () => {
      throw new Error('QuotaExceededError');
    },
    removeItem: () => {
      throw new Error('SecurityError');
    },
  };
  it('leaves the defaults and a page whose windows and sidebars still work', async () => {
    configureWorkspace({ storage: () => throwing, remote: null, now: () => now, saveMs: 300 });
    showPage('overview', 'desk', 1440);
    expect(currentLayout().windows).toHaveLength(2);
    change((l) => setSide(open(l, 'chart', {}, 'right', 'overview').layout, 'rail', false));
    await tick();
    expect(currentLayout().windows).toHaveLength(3);
    expect(currentLayout().rail.open).toBe(false);
    expect(savedLayouts()).toEqual([]);
    resetAll();
    expect(currentLayout().windows).toHaveLength(2);
  });
  it('and a browser whose storage property itself throws', async () => {
    configureWorkspace({
      storage: () => {
        throw new Error('SecurityError');
      },
      remote: null,
      now: () => now,
      saveMs: 300,
    });
    showPage('dev', 'desk', 1440);
    expect(currentLayout().windows.map((w) => w.kind)).toEqual(['terminal', 'diff']);
    change((l) => setSide(l, 'ctx', true));
    await tick();
    expect(currentLayout().ctx.open).toBe(true);
  });
});

describe('Reset', () => {
  it('a page back to its default windows and sidebars, the page’s own defaults too', async () => {
    const s = mem();
    configureWorkspace({ storage: () => s, remote: null, now: () => now, saveMs: 300 });
    showPage('overview', 'desk', 1440);
    change((l) => setSide(open(l, 'inspector', {}, 'float', 'overview').layout, 'ctx', true));
    await tick();
    resetPage();
    expect(currentLayout().windows.map((w) => w.kind)).toEqual(['logs', 'chart']);
    expect(currentLayout().ctx.open).toBe(false);
    await tick();
    expect(saved(s)['overview|desk'].windows).toHaveLength(2);
  });
  it('every page on every device, and the widths, the controller told', async () => {
    const s = mem();
    const r = remote();
    configureWorkspace({ storage: () => s, remote: r, now: () => now, saveMs: 300 });
    showPage('overview', 'desk', 1440);
    change((l) => resizeSide(open(l, 'inspector', {}, 'float', 'overview').layout, 'rail', 320));
    showPage('dev', 'phone', 375);
    change((l) => ({ ...l, windows: [] }));
    await tick();
    expect(savedLayouts().map((x) => `${x.page}|${x.device}:${x.windows}`).sort()).toEqual(['dev|phone:0', 'overview|desk:3']);
    r.puts.length = 0;
    resetAll();
    expect(savedLayouts().map((x) => `${x.page}|${x.device}:${x.windows}`).sort()).toEqual(['dev|phone:2', 'overview|desk:2']);
    expect(s.m.get('tandemapp.widths')).toBeUndefined();
    expect(r.puts.map((l) => `${l.page}|${l.device}`).sort()).toEqual(['dev|phone', 'overview|desk']);
    showPage('overview', 'desk', 1440);
    expect(currentLayout().rail.w).toBe(248);
  });
});

describe('a page’s own windows', () => {
  it('show in the layout, keep where they were moved, and go with the page', async () => {
    const s = mem();
    configureWorkspace({ storage: () => s, remote: null, now: () => now, saveMs: 300 });
    showPage('chat', 'desk', 1440);
    let panes = [
      { id: 'chat:t', kind: 'terminal' as const, title: 'Terminal', icon: 'terminal' },
      { id: 'chat:f', kind: 'files' as const, title: 'Files', icon: 'folder' },
    ];
    const reset = vi.fn();
    const src: WindowSource = { windows: () => panes, body: () => document as never, dock: () => 'bottom', activate: () => {}, close: () => {}, hide: () => {}, kinds: ['terminal', 'files'], open: () => {}, reset };
    const off = registerWindows('chat', src);
    expect(pageSource()).toBe(src);
    expect(shownLayout().windows.map((w) => `${w.id}@${w.at}`)).toEqual(['chat:t@bottom', 'chat:f@bottom']);
    change((l) => move(l, 'chat:f', 'float'));
    panes = [panes[0]];
    expect(shownLayout().windows.map((w) => w.id)).toEqual(['chat:t']);
    change((l) => move(l, 'chat:t', 'right'));
    panes = [{ id: 'chat:t', kind: 'terminal', title: 'Terminal', icon: 'terminal' }, { id: 'chat:f', kind: 'files', title: 'Files', icon: 'folder' }];
    expect(shownLayout().windows.map((w) => `${w.id}@${w.at}`)).toEqual(['chat:t@right', 'chat:f@float']);
    resetPage();
    expect(reset).toHaveBeenCalledOnce();
    off();
    expect(pageSource()).toBeNull();
    expect(shownLayout().windows.filter((w) => !w.ref.source)).toEqual([]);
  });
});

describe('the tray and the menus', () => {
  it('open and close, and a new page closes a menu', () => {
    configureWorkspace({ storage: () => mem(), remote: null });
    showPage('overview', 'phone', 375);
    setTray(true);
    expect(trayOpen()).toBe(true);
    openWindowMenu({ kind: 'add', at: { left: 0, top: 0, right: 10, bottom: 10 } });
    expect(windowMenu()?.kind).toBe('add');
    showPage('usage', 'phone', 375);
    expect(windowMenu()).toBeNull();
    setTray(false);
    expect(trayOpen()).toBe(false);
  });
});
