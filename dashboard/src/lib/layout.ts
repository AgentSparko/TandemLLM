// The shell's layout (VIS-242): the width class from the window's width, and the two sidebars' state
// -- the left panel and the right context sidebar, open or not and how wide -- kept per device in
// localStorage until the fleet's layouts route holds it (VIS-256). Storage that throws (a private
// window, a full quota, a browser that refuses the property itself) leaves the defaults and the
// page working. Kept pure so it can be tested.

export type WidthClass = 'desk' | 'tablet' | 'phone';

/** the prototype's breakpoints: a tablet's panels become drawers, a phone gets the bottom bar */
export const TABLET_MAX = 1100;
export const PHONE_MAX = 600;

export function widthClass(w: number): WidthClass {
  return w <= PHONE_MAX ? 'phone' : w <= TABLET_MAX ? 'tablet' : 'desk';
}

export const RAIL_W = { min: 200, max: 420, def: 248 };
export const CTX_W = { min: 280, max: 560, def: 340 };

export interface Side {
  open: boolean;
  w: number;
}

export interface ShellLayout {
  rail: Side;
  ctx: Side;
  /** the open state on a page that brings its own sidebars (Chat: its list and its Context
   *  panel, until VIS-254 moves them into the shell's): closed until you open them there */
  full: { rail: boolean; ctx: boolean };
}

export const DEFAULT_LAYOUT: ShellLayout = { rail: { open: true, w: RAIL_W.def }, ctx: { open: true, w: CTX_W.def }, full: { rail: false, ctx: false } };

/** the narrowest desk on which both sidebars start open (the design's 1440 px): on a narrower one
 *  they start as the strip and the 44 px title, so today's pages keep the width they were laid out
 *  for; opened once, they stay open */
export const BOTH_OPEN_MIN = 1440;

/** The layout of a first visit on a window this wide. */
export function defaultLayout(width = BOTH_OPEN_MIN): ShellLayout {
  const open = width >= BOTH_OPEN_MIN;
  return { rail: { open, w: RAIL_W.def }, ctx: { open, w: CTX_W.def }, full: { rail: false, ctx: false } };
}

const KEY = 'tandemapp.layout';

export const clampW = (w: number, lim: { min: number; max: number }) => Math.round(Math.min(lim.max, Math.max(lim.min, w)));

const side = (o: unknown, d: Side, lim: { min: number; max: number }): Side => {
  const s = o as Partial<Side> | null;
  return { open: typeof s?.open === 'boolean' ? s.open : d.open, w: typeof s?.w === 'number' && Number.isFinite(s.w) ? clampW(s.w, lim) : d.w };
};

/** The saved layout, every field checked; what is missing or wrong reads as the default for a
 *  window this wide. */
export function loadLayout(store?: Pick<Storage, 'getItem'> | null, width?: number): ShellLayout {
  const d = defaultLayout(width);
  let o: Record<string, unknown> | null = null;
  try {
    o = JSON.parse((store === undefined ? localStorage : store)?.getItem(KEY) ?? 'null');
  } catch {
    /* unreadable: the defaults */
  }
  if (!o || typeof o !== 'object') return d;
  const f = o.full as Partial<ShellLayout['full']> | null;
  return {
    rail: side(o.rail, d.rail, RAIL_W),
    ctx: side(o.ctx, d.ctx, CTX_W),
    full: { rail: typeof f?.rail === 'boolean' ? f.rail : false, ctx: typeof f?.ctx === 'boolean' ? f.ctx : false },
  };
}

/** Saves the layout; false when the storage refused it (the layout holds for this page load). */
export function saveLayout(l: ShellLayout, store?: Pick<Storage, 'setItem'> | null): boolean {
  try {
    const s = store === undefined ? localStorage : store;
    s?.setItem(KEY, JSON.stringify(l));
    return !!s;
  } catch {
    return false;
  }
}

/** Whether a sidebar shows open on a desk: a full page keeps its own state. */
export function isOpen(l: ShellLayout, which: 'rail' | 'ctx', full: boolean): boolean {
  return full ? l.full[which] : l[which].open;
}

/** The layout with a sidebar toggled (on a full page, its own state). */
export function toggled(l: ShellLayout, which: 'rail' | 'ctx', full: boolean): ShellLayout {
  if (full) return { ...l, full: { ...l.full, [which]: !l.full[which] } };
  return { ...l, [which]: { ...l[which], open: !l[which].open } };
}

/** The layout with a sidebar opened (a selection shows in the context sidebar). */
export function opened(l: ShellLayout, which: 'rail' | 'ctx', full: boolean): ShellLayout {
  return isOpen(l, which, full) ? l : toggled(l, which, full);
}

/** The layout with a sidebar's width set, kept within its limits. */
export function resized(l: ShellLayout, which: 'rail' | 'ctx', w: number): ShellLayout {
  return { ...l, [which]: { ...l[which], w: clampW(w, which === 'rail' ? RAIL_W : CTX_W) } };
}
