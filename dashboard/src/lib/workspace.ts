// The workspace (VIS-256): the layout of the page that shows, on the device class it shows on (its two
// sidebars and its windows, lib/wm.ts), read from this browser at once and from TandemApp's controller
// when it answers (GET /v1/fleet/layouts/{page}?device=), and saved to both a moment after each change
// (PUT). The controller's copy wins when it was saved later; a server without the fleet API answers
// 404 once and is not asked again, so the browser's copy is the layout. Every storage access sits in a
// try/catch: a storage that throws leaves the defaults and a page that works. The sidebars' widths are
// one per device (resize once, every page follows); whether they are open is each page's own.
//
// A page may bring windows of its own (registerWindows: Chat's panes, kept per chat on its server):
// the layout keeps where they sit, the page what they show. The shell, <qse-workspace> and the pages
// listen with onWorkspace; the phone's tray and the window menus are here too, as the top bar, the
// bottom bar and the docks all open them.

import type { TemplateResult } from 'lit';
import type { Layout, LayoutDevice, WindowKind } from '../api/fleet';
import { fleet } from '../api/fleet';
import { describeError } from '../api/client';
import { CTX_W, RAIL_W, clampW, defaultSides, legacySides, type Sides } from './layout';
import { defaultLayout, normalise, withSource, withoutSource, type DockSide, type SourceWindow } from './wm';

export interface WindowSource {
  /** the windows the page shows now, in tab order (none while it keeps them put away) */
  windows(): (SourceWindow & { title: string; icon: string })[];
  /** a window's body: an element the page keeps, so a window that moves keeps what it holds */
  body(id: string, sheet: boolean): Node | TemplateResult;
  /** the dock its new windows go to */
  dock(): DockSide;
  activate(id: string): void;
  close(id: string): void;
  /** a dock's close: the page's windows are put away, not closed (Chat keeps its panes) */
  hide(): void;
  /** the kinds the page opens as its own from the add menu (Chat: the sandbox's) */
  kinds: readonly WindowKind[];
  open(kind: WindowKind): void;
  /** Reset layout on the page: its own defaults */
  reset?(): void;
  /** the window the page shows in front (Chat: the chat's active pane) */
  active?(): string | null;
}

interface Store {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
  removeItem(k: string): void;
}
interface Remote {
  get(page: string, device: LayoutDevice): Promise<Layout>;
  put(l: Layout): Promise<Layout>;
}

const KEY = 'tandemapp.layouts';
const WIDTHS = 'tandemapp.widths';
const key = (page: string, device: LayoutDevice) => `${page}|${device}`;

let storage: () => Store | null = () => localStorage;
let remote: Remote | null = { get: (p, d) => fleet.layout(p, d), put: (l) => fleet.saveLayout(l) };
let clock = () => Date.now();
let saveMs = 400;

/** Tests swap the storage, the controller and the clock. */
export function configureWorkspace(o: { storage?: () => Store | null; remote?: Remote | null; now?: () => number; saveMs?: number }): void {
  if (o.storage) storage = o.storage;
  if (o.remote !== undefined) remote = o.remote;
  if (o.now) clock = o.now;
  if (o.saveMs != null) saveMs = o.saveMs;
  page = '';
  layout = null;
  remoteOff = false;
  if (timer) clearTimeout(timer);
  timer = null;
}

let page = '';
let device: LayoutDevice = 'desk';
let width = 1440;
let layout: Layout | null = null;
let remoteOff = false;
let timer: ReturnType<typeof setTimeout> | null = null;
let tray = false;
/** the window the tray shows when one was picked (a tab, or a window just opened) */
let trayAct: string | null = null;
let menu: WindowMenu | null = null;
const sources = new Map<string, WindowSource>();
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((f) => f());

/** an open window menu: the add menu (with the dock it adds to) or the layout menu, at its button */
export interface WindowMenu {
  kind: 'add' | 'layout';
  at: { left: number; top: number; bottom: number; right: number };
  target?: DockSide | 'float' | 'auto';
}

function readAll(): Record<string, unknown> {
  try {
    const o = JSON.parse(storage()?.getItem(KEY) ?? '{}');
    return o && typeof o === 'object' ? o : {};
  } catch {
    return {};
  }
}
function writeAll(m: Record<string, unknown>): boolean {
  try {
    const s = storage();
    s?.setItem(KEY, JSON.stringify(m));
    return !!s;
  } catch {
    return false;
  }
}

/** the sidebars' widths on a device: saved, else 2.2's, else the defaults */
function widthsOf(d: LayoutDevice): { rail: number; ctx: number } {
  try {
    const o = JSON.parse(storage()?.getItem(WIDTHS) ?? '{}')?.[d];
    if (o && Number.isFinite(o.rail) && Number.isFinite(o.ctx)) return { rail: clampW(o.rail, RAIL_W), ctx: clampW(o.ctx, CTX_W) };
  } catch {
    /* the defaults */
  }
  const old = legacy()?.all;
  return { rail: old?.rail?.w ?? RAIL_W.def, ctx: old?.ctx?.w ?? CTX_W.def };
}
function writeWidths(d: LayoutDevice, w: { rail: number; ctx: number }): void {
  try {
    const s = storage();
    const o = JSON.parse(s?.getItem(WIDTHS) ?? '{}') ?? {};
    s?.setItem(WIDTHS, JSON.stringify({ ...o, [d]: w }));
  } catch {
    /* the layout holds for this page load */
  }
}
function legacy(): ReturnType<typeof legacySides> {
  try {
    return legacySides(storage());
  } catch {
    return null;
  }
}

/** a page's layout before it was ever saved on this device */
function fresh(p: string, d: LayoutDevice, wd: number): Layout {
  const w = widthsOf(d);
  const s: Sides = defaultSides(wd, p === 'chat', w);
  const old = legacy();
  if (old && d === 'desk') {
    if (p === 'chat') {
      if (old.chat.rail != null) s.rail.open = old.chat.rail;
      if (old.chat.ctx != null) s.ctx.open = old.chat.ctx;
    } else {
      if (old.all.rail) s.rail.open = old.all.rail.open;
      if (old.all.ctx) s.ctx.open = old.all.ctx.open;
    }
  }
  return defaultLayout(p, d, s);
}

const withWidths = (l: Layout): Layout => {
  const w = widthsOf(l.device);
  return { ...l, rail: { ...l.rail, w: w.rail }, ctx: { ...l.ctx, w: w.ctx } };
};

/** The shell says which page shows, on which device class and how wide the window is. */
export function showPage(p: string, d: LayoutDevice, wd: number): void {
  width = wd;
  if (p === page && d === device && layout) return;
  flush();
  page = p;
  device = d;
  const saved = normalise(readAll()[key(p, d)], p, d);
  layout = saved ? withWidths(saved) : fresh(p, d, wd);
  menu = null;
  emit();
  void pull(p, d);
}

async function pull(p: string, d: LayoutDevice): Promise<void> {
  if (!remote || remoteOff) return;
  try {
    const r = normalise(await remote.get(p, d), p, d);
    if (p !== page || d !== device || !r || r.saved_at == null || !layout) return;
    if (layout.saved_at != null && r.saved_at <= layout.saved_at) return;
    layout = withWidths(r);
    writeAll({ ...readAll(), [key(p, d)]: layout });
    emit();
  } catch (e) {
    if (describeError(e).status === 404) remoteOff = true;
  }
}

/** the page's layout as saved (its own windows' placements included, shown or not) */
export function currentLayout(): Layout {
  return (layout ??= fresh(page || 'overview', device, width));
}

/** the page's layout as it shows: with the page's own windows in it */
export function shownLayout(): Layout {
  const l = currentLayout();
  const src = sources.get(page);
  return src ? withSource(l, src.windows(), src.dock()) : l;
}

export function currentPage(): { page: string; device: LayoutDevice; width: number } {
  return { page, device, width };
}

/** A change to the shown layout; saved a moment later. */
export function change(fn: (l: Layout) => Layout): void {
  const before = currentLayout();
  const shown = shownLayout();
  const next = fn(shown);
  if (next === shown) return;
  layout = sources.get(page) ? withoutSource(next, before) : next;
  emit();
  schedule();
}

function schedule(): void {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    save();
  }, saveMs);
}

/** A change still waiting is saved now (the page changes, the tab hides). */
export function flush(): void {
  if (!timer) return;
  clearTimeout(timer);
  timer = null;
  save();
}

function save(): void {
  if (!layout) return;
  const l: Layout = { ...layout, saved_at: Math.floor(clock() / 1000) };
  layout = l;
  writeAll({ ...readAll(), [key(l.page, l.device)]: l });
  writeWidths(l.device, { rail: l.rail.w, ctx: l.ctx.w });
  if (remote && !remoteOff)
    void remote.put(l).catch((e) => {
      if (describeError(e).status === 404) remoteOff = true;
    });
  emit();
}

/** Reset layout: the page's default windows and sidebars on this device (and the page's own). */
export function resetPage(): void {
  sources.get(page)?.reset?.();
  layout = fresh(page, device, width);
  emit();
  schedule();
}

/** Every page's layout on every device back to its default, and the sidebars' widths. */
export function resetAll(): void {
  const all = readAll();
  const now = Math.floor(clock() / 1000);
  try {
    storage()?.removeItem(WIDTHS);
  } catch {
    /* nothing kept */
  }
  const next: Record<string, Layout> = {};
  for (const k of Object.keys(all)) {
    const [p, d] = k.split('|') as [string, LayoutDevice];
    // the defaults saved now, so the controller's older copies do not come back
    next[k] = { ...fresh(p, d, d === device ? width : 1440), saved_at: now };
    if (remote && !remoteOff) void remote.put(next[k]).catch(() => undefined);
  }
  writeAll(next);
  sources.get(page)?.reset?.();
  layout = fresh(page, device, width);
  emit();
  schedule();
}

/** what this browser keeps: each page's layout per device, how many windows, when it was saved */
export function savedLayouts(): { page: string; device: LayoutDevice; windows: number; saved_at: number | null }[] {
  return Object.entries(readAll())
    .map(([k, v]) => {
      const [p, d] = k.split('|') as [string, LayoutDevice];
      const l = normalise(v, p, d);
      return l ? { page: p, device: d, windows: l.windows.filter((w) => !w.ref.source).length, saved_at: l.saved_at ?? null } : null;
    })
    .filter((x): x is NonNullable<typeof x> => !!x);
}

/** A page's own windows; returns the unregister. */
export function registerWindows(p: string, src: WindowSource): () => void {
  sources.set(p, src);
  emit();
  return () => {
    if (sources.get(p) !== src) return;
    sources.delete(p);
    emit();
  };
}

export function pageSource(): WindowSource | null {
  return sources.get(page) ?? null;
}

/** the page's own windows changed (a chat opened, a pane came or went) */
export const windowsChanged = emit;

export function onWorkspace(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// ---- the phone's tray and the window menus ------------------------------------------------------

export function trayOpen(): boolean {
  return tray;
}
export function setTray(open: boolean): void {
  if (tray === open) return;
  tray = open;
  menu = null;
  emit();
}
/** The sheet the tray shows: the one picked, else the page's own in front, else the newest. */
export function trayShows(ids: string[]): string | null {
  if (trayAct && ids.includes(trayAct)) return trayAct;
  const own = sources.get(page)?.active?.();
  if (own && ids.includes(own)) return own;
  return ids[ids.length - 1] ?? null;
}
/** a sheet picked in the tray, or none (a window just opened shows: the page's own or the newest) */
export function showInTray(id: string | null): void {
  trayAct = id;
  emit();
}

export function windowMenu(): WindowMenu | null {
  return menu;
}
export function openWindowMenu(m: WindowMenu | null): void {
  menu = m;
  emit();
}
