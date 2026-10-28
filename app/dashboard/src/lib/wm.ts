// The window manager's model (VIS-256, concept §11): one page's layout on one device class, in the
// fleet contract's shape (docs/contract/fleet-v1 layout.schema.json): its two sidebars, its docks
// (right and bottom, each a set of tabs with the one it shows and its share of the work area) and its
// windows, each docked, floating (placed and sized in pixels, raised by z) or minimised (with where it
// returns to). Every change is a pure function from a layout to a new one, so the rules can be tested;
// lib/workspace.ts keeps the current one, saves it and tells the shell. A page may bring windows of
// its own (Chat: the open chat's panes, server state per chat): the layout keeps where they sit, the
// page keeps what they show (withSource / withoutSource).

import type { Layout, LayoutDevice, LayoutWindow, WindowKind } from '../api/fleet';
import { WINDOW_KINDS } from '../api/fleet';
import { CTX_W, RAIL_W, clampW, type Sides } from './layout';

export type At = LayoutWindow['at'];
export type DockSide = 'right' | 'bottom';
export type Ref = Record<string, unknown>;
export type Where = At | 'auto';

/** a dock's share of the work area: its limits and the start */
export const DOCK_F = { right: { min: 0.2, max: 0.75, def: 0.4 }, bottom: { min: 0.15, max: 0.8, def: 0.36 } } as const;
export const FLOAT_MIN = { w: 260, h: 160 };
export const FLOAT_DEF = { w: 560, h: 360 };
/** the placements of a page's own windows (other chats' panes) a layout keeps */
export const SOURCE_KEEP = 24;

interface Seed {
  kind: WindowKind;
  ref: Ref;
  at: DockSide;
}

/** The windows a page opens with (the prototype's): Overview a log and a chart below the page,
 *  Hardware a terminal on one Spark (the first, until one is picked), Dev a terminal in a chat's
 *  sandbox and that chat's changes on the right. Chat's are its open chat's own (CHAT_DEFAULTS). */
export const PAGE_WINDOWS: Partial<Record<string, { wins: Seed[]; f?: Partial<Record<DockSide, number>> }>> = {
  overview: { wins: [{ kind: 'logs', ref: { node: 'all' }, at: 'bottom' }, { kind: 'chart', ref: { metric: 'power', scope: 'page' }, at: 'bottom' }], f: { bottom: 0.3 } },
  hardware: { wins: [{ kind: 'terminal', ref: { node: null }, at: 'bottom' }], f: { bottom: 0.34 } },
  dev: { wins: [{ kind: 'terminal', ref: { node: 'sandbox' }, at: 'right' }, { kind: 'diff', ref: {}, at: 'right' }], f: { right: 0.36 } },
  chat: { wins: [], f: { bottom: 0.38 } },
};

/** Chat's default windows: the open chat's terminal, its files and the page it made (chat/view.ts) */
export const CHAT_DEFAULTS: WindowKind[] = ['terminal', 'files', 'preview'];

/** where a window goes when no dock is asked for: the dock that has windows when only one has,
 *  else the page's own (below on Chat, Overview and Hardware, beside it elsewhere) */
export function autoDock(l: Layout, page: string): DockSide {
  const has = (d: DockSide) => l.windows.some((w) => w.at === d);
  if (has('bottom') && !has('right')) return 'bottom';
  if (has('right') && !has('bottom')) return 'right';
  return PAGE_WINDOWS[page]?.wins[0]?.at ?? (page === 'chat' ? 'bottom' : 'right');
}

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const clampF = (side: DockSide, f: number) => Math.min(DOCK_F[side].max, Math.max(DOCK_F[side].min, f));

function nextId(wins: LayoutWindow[]): string {
  let n = 0;
  for (const w of wins) {
    const m = /^w(\d+)$/.exec(w.id);
    if (m) n = Math.max(n, Number(m[1]));
  }
  return `w${n + 1}`;
}
const topZ = (wins: LayoutWindow[]) => wins.reduce((z, w) => Math.max(z, w.z), 10);

/** a new window's float place: a cascade, so two never cover each other exactly */
function cascade(i: number, kind: WindowKind): Pick<LayoutWindow, 'x' | 'y' | 'w' | 'h'> {
  return { x: 60 + (i % 6) * 28, y: 40 + (i % 6) * 24, w: kind === 'terminal' ? 620 : FLOAT_DEF.w, h: FLOAT_DEF.h + 20 };
}

/** The layout a page gets before its first save, on a device, with the sidebars it should open with. */
export function defaultLayout(page: string, device: LayoutDevice, sides: Sides): Layout {
  const def = PAGE_WINDOWS[page];
  const l: Layout = {
    page,
    device,
    rail: { ...sides.rail },
    ctx: { ...sides.ctx },
    windows: [],
    docks: { right: { f: def?.f?.right ?? DOCK_F.right.def, act: null }, bottom: { f: def?.f?.bottom ?? DOCK_F.bottom.def, act: null } },
    saved_at: null,
  };
  let out = l;
  for (const s of def?.wins ?? []) out = open(out, s.kind, { ...s.ref }, s.at, page).layout;
  return { ...out, docks: { right: { ...out.docks.right, act: out.windows.find((w) => w.at === 'right')?.id ?? null }, bottom: { ...out.docks.bottom, act: out.windows.find((w) => w.at === 'bottom')?.id ?? null } } };
}

/** The keys of a window's ref that make it the same window: a second Terminal on R-3 brings the first
 *  back, a Terminal on R-1 is another. Kinds without keys open a new window every time. */
const SAME: Partial<Record<WindowKind, string[]>> = {
  terminal: ['node', 'chat'],
  files: ['node', 'chat'],
  logs: ['node'],
  chart: ['metric', 'scope'],
  inspector: ['node'],
  editor: ['chat', 'path'],
  diff: ['chat', 'ref'],
  markdown: ['chat', 'ref'],
  preview: ['chat', 'ref'],
  image: ['chat', 'ref'],
  pdf: ['chat', 'ref'],
  video: ['chat', 'ref'],
  audio: ['chat', 'ref'],
  binary: ['chat', 'ref'],
  plan: ['chat'],
  context: ['chat'],
};

export function sameWindow(kind: WindowKind, a: Ref, b: Ref): boolean {
  const keys = SAME[kind];
  return !!keys && keys.every((k) => (a[k] ?? null) === (b[k] ?? null));
}

/** Opens a window of a kind (or brings back the same one, its ref updated), docked where asked
 *  (auto: autoDock), floating or minimised; it becomes its dock's tab, or the top float. */
export function open(l: Layout, kind: WindowKind, ref: Ref, where: Where, page: string): { layout: Layout; id: string } {
  const at: At = where === 'auto' ? autoDock(l, page) : where;
  const same = l.windows.find((w) => w.kind === kind && !w.ref.source && sameWindow(kind, w.ref, ref));
  if (same) {
    const back = same.at === 'min' ? (same.prev ?? at) : same.at;
    const moved = { ...l, windows: l.windows.map((w) => (w.id === same.id ? { ...w, ref: { ...w.ref, ...ref } } : w)) };
    return { layout: move(moved, same.id, back), id: same.id };
  }
  const id = nextId(l.windows);
  const w: LayoutWindow = { id, kind, ref, at, ...cascade(l.windows.length, kind), z: topZ(l.windows) + 1 };
  return { layout: activate({ ...l, windows: [...l.windows, w] }, id), id };
}

/** Closes a window; its dock shows the tab before it, or the next. */
export function close(l: Layout, id: string): Layout {
  const gone = l.windows.find((w) => w.id === id);
  if (!gone) return l;
  return { ...l, windows: l.windows.filter((w) => w.id !== id), docks: refocus(l.docks, l.windows, gone.at, id) };
}

/** a dock whose shown tab leaves shows the tab before it, or the one after it, or none */
function refocus(docks: Layout['docks'], before: LayoutWindow[], from: At, id: string): Layout['docks'] {
  if (from !== 'right' && from !== 'bottom') return docks;
  const tabs = before.filter((w) => w.at === from);
  const i = tabs.findIndex((w) => w.id === id);
  if (docks[from].act !== id) return docks;
  const next = tabs[i - 1] ?? tabs[i + 1] ?? null;
  return { ...docks, [from]: { ...docks[from], act: next?.id ?? null } };
}

/** Moves a window to a dock (its last tab), to float or to minimise. A minimised window remembers
 *  where it was. */
export function move(l: Layout, id: string, at: At): Layout {
  const w = l.windows.find((x) => x.id === id);
  if (!w) return l;
  const prev: LayoutWindow['prev'] = at === 'min' ? (w.at === 'min' ? w.prev : w.at) : undefined;
  const z = at === 'float' ? topZ(l.windows) + 1 : w.z;
  const { prev: _p, ...rest } = w;
  void _p;
  const moved: LayoutWindow = prev ? { ...rest, at, prev, z } : { ...rest, at, z };
  // a window docked anew is its dock's last tab; in place otherwise
  const windows = w.at !== at && (at === 'right' || at === 'bottom') ? [...l.windows.filter((x) => x.id !== id), moved] : l.windows.map((x) => (x.id === id ? moved : x));
  let docks = w.at === at ? l.docks : refocus(l.docks, l.windows, w.at, id);
  if (at === 'right' || at === 'bottom') docks = { ...docks, [at]: { ...docks[at], act: id } };
  return { ...l, windows, docks };
}

/** Shows a window: its dock's tab, the top float, or back from minimised. */
export function activate(l: Layout, id: string): Layout {
  const w = l.windows.find((x) => x.id === id);
  if (!w) return l;
  if (w.at === 'min') return move(l, id, w.prev ?? 'float');
  if (w.at === 'float') return w.z === topZ(l.windows) ? l : { ...l, windows: l.windows.map((x) => (x.id === id ? { ...x, z: topZ(l.windows) + 1 } : x)) };
  return l.docks[w.at].act === id ? l : { ...l, docks: { ...l.docks, [w.at]: { ...l.docks[w.at], act: id } } };
}

/** A minimised window back where it was (or floating). */
export function restore(l: Layout, id: string): Layout {
  const w = l.windows.find((x) => x.id === id);
  return w && w.at === 'min' ? move(l, id, w.prev ?? 'float') : l;
}

/** A window's ref with some keys changed (what it shows: a node, a metric, a filter). */
export function setRef(l: Layout, id: string, patch: Ref): Layout {
  return { ...l, windows: l.windows.map((w) => (w.id === id ? { ...w, ref: { ...w.ref, ...patch } } : w)) };
}

/** A dock's share of the work area, kept within its limits. */
export function setDockF(l: Layout, side: DockSide, f: number): Layout {
  return { ...l, docks: { ...l.docks, [side]: { ...l.docks[side], f: Math.round(clampF(side, f) * 1000) / 1000 } } };
}

/** A floating window placed or sized; never smaller than FLOAT_MIN, never entirely above the top. */
export function place(l: Layout, id: string, r: Partial<Pick<LayoutWindow, 'x' | 'y' | 'w' | 'h'>>): Layout {
  return {
    ...l,
    windows: l.windows.map((w) =>
      w.id === id
        ? {
            ...w,
            x: Math.round(r.x ?? w.x),
            y: Math.round(Math.max(0, r.y ?? w.y)),
            w: Math.round(Math.max(FLOAT_MIN.w, r.w ?? w.w)),
            h: Math.round(Math.max(FLOAT_MIN.h, r.h ?? w.h)),
          }
        : w,
    ),
  };
}

/** Every window to one place (the layout menu): both docks, float (cascaded) or minimised. */
export function allTo(l: Layout, at: At): Layout {
  let out = l;
  l.windows.forEach((w, i) => {
    out = move(out, w.id, at);
    if (at === 'float') out = place(out, w.id, cascade(i, w.kind));
  });
  return out;
}

/** The floating windows tiled over a work area of this size. */
export function tile(l: Layout, width: number, height: number): Layout {
  const fl = l.windows.filter((w) => w.at === 'float');
  if (!fl.length) return l;
  const cols = Math.ceil(Math.sqrt(fl.length));
  const rows = Math.ceil(fl.length / cols);
  const w = Math.max(FLOAT_MIN.w, Math.floor(width / cols) - 12);
  const h = Math.max(FLOAT_MIN.h, Math.floor(height / rows) - 12);
  let out = l;
  fl.forEach((x, i) => (out = place(out, x.id, { x: (i % cols) * (w + 12) + 6, y: Math.floor(i / cols) * (h + 12) + 6, w, h })));
  return out;
}

/** The windows of a dock in tab order, and the one it shows (its act, else its first). */
export function dockOf(l: Layout, side: DockSide): { wins: LayoutWindow[]; act: LayoutWindow | null } {
  const wins = l.windows.filter((w) => w.at === side);
  return { wins, act: wins.find((w) => w.id === l.docks[side].act) ?? wins[0] ?? null };
}

// ---- the sidebars -----------------------------------------------------------------------------

export function setSide(l: Layout, which: 'rail' | 'ctx', open: boolean): Layout {
  return l[which].open === open ? l : { ...l, [which]: { ...l[which], open } };
}

export function resizeSide(l: Layout, which: 'rail' | 'ctx', w: number): Layout {
  return { ...l, [which]: { ...l[which], w: clampW(w, which === 'rail' ? RAIL_W : CTX_W) } };
}

// ---- a page's own windows ---------------------------------------------------------------------

/** a window a page brings itself; its id is the page's (Chat: `chat:<pane id>`) */
export interface SourceWindow {
  id: string;
  kind: WindowKind;
}

/** The layout as shown with a page's own windows in it: each where it sat last, or a new one in
 *  `dock`; the placements of windows the page does not show now (another chat's panes) left out. */
export function withSource(l: Layout, src: SourceWindow[], dock: DockSide): Layout {
  const ids = new Set(src.map((s) => s.id));
  const kept = l.windows.filter((w) => !w.ref.source || ids.has(w.id));
  const add: LayoutWindow[] = [];
  let z = topZ(l.windows);
  for (const s of src) {
    const had = kept.find((w) => w.id === s.id);
    if (had) continue;
    add.push({ id: s.id, kind: s.kind, ref: { source: true }, at: dock, ...cascade(kept.length + add.length, s.kind), z: ++z });
  }
  const order = new Map(src.map((s, i) => [s.id, i]));
  // the page's windows keep the page's order among themselves
  const own = [...kept.filter((w) => w.ref.source), ...add].sort((a, b) => order.get(a.id)! - order.get(b.id)!);
  return { ...l, windows: [...kept.filter((w) => !w.ref.source), ...own.map((w) => ({ ...w, kind: src[order.get(w.id)!].kind }))] };
}

/** The layout to keep after a change made on the shown one: the shown windows, and the placements
 *  of the page's windows that were not shown (the newest SOURCE_KEEP of those). */
export function withoutSource(shown: Layout, before: Layout): Layout {
  const ids = new Set(shown.windows.map((w) => w.id));
  const off = before.windows.filter((w) => w.ref.source && !ids.has(w.id));
  const own = shown.windows.filter((w) => w.ref.source).length;
  return { ...shown, windows: [...shown.windows, ...off.slice(Math.max(0, off.length - Math.max(0, SOURCE_KEEP - own)))] };
}

// ---- reading a saved layout ---------------------------------------------------------------------

const ATS: At[] = ['right', 'bottom', 'float', 'min'];

/** A saved layout, every field checked; null when it is not one of this page on this device. A
 *  window of a kind this build does not know, or placed nowhere it knows, is dropped; numbers out of
 *  their limits are brought back in; a dock's tab that is not in it is forgotten. */
export function normalise(raw: unknown, page: string, device: LayoutDevice): Layout | null {
  const o = raw as Partial<Layout> | null;
  if (!o || typeof o !== 'object' || o.page !== page || o.device !== device || !Array.isArray(o.windows)) return null;
  const side = (s: unknown, lim: { min: number; max: number }, def: number) => {
    const v = s as Partial<{ open: boolean; w: number }> | null;
    return { open: typeof v?.open === 'boolean' ? v.open : true, w: num(v?.w) ? clampW(v.w, lim) : def };
  };
  const seen = new Set<string>();
  const windows: LayoutWindow[] = [];
  for (const x of o.windows as Partial<LayoutWindow>[]) {
    if (!x || typeof x.id !== 'string' || seen.has(x.id) || !(WINDOW_KINDS as readonly string[]).includes(x.kind as string) || !ATS.includes(x.at as At)) continue;
    seen.add(x.id);
    const ref = x.ref && typeof x.ref === 'object' && !Array.isArray(x.ref) ? (x.ref as Ref) : {};
    const w: LayoutWindow = {
      id: x.id,
      kind: x.kind as WindowKind,
      ref,
      at: x.at as At,
      x: num(x.x) ? Math.round(x.x) : 60,
      y: num(x.y) ? Math.max(0, Math.round(x.y)) : 40,
      w: num(x.w) ? Math.max(FLOAT_MIN.w, Math.round(x.w)) : FLOAT_DEF.w,
      h: num(x.h) ? Math.max(FLOAT_MIN.h, Math.round(x.h)) : FLOAT_DEF.h,
      z: num(x.z) ? x.z : 10,
    };
    if (x.at === 'min' && (x.prev === 'right' || x.prev === 'bottom' || x.prev === 'float')) w.prev = x.prev;
    windows.push(w);
  }
  const dock = (side: DockSide) => {
    const d = (o.docks as Partial<Layout['docks']> | undefined)?.[side];
    const act = typeof d?.act === 'string' && windows.some((w) => w.id === d.act && w.at === side) ? d.act : null;
    return { f: num(d?.f) ? clampF(side, d.f) : DOCK_F[side].def, act };
  };
  return {
    page,
    device,
    rail: side(o.rail, RAIL_W, RAIL_W.def),
    ctx: side(o.ctx, CTX_W, CTX_W.def),
    windows,
    docks: { right: dock('right'), bottom: dock('bottom') },
    saved_at: num(o.saved_at) ? o.saved_at : null,
  };
}
