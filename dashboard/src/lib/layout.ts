// The shell's measures (VIS-242, VIS-256): the width class from the window's width, the two sidebars'
// limits and the open state a first visit gets. The sidebars' state itself is part of each page's
// layout on each device (lib/wm.ts, lib/workspace.ts); 2.2 kept one for every page under
// `tandemapp.layout`, read here once so a device keeps what it had. Kept pure so it can be tested.

export type WidthClass = 'desk' | 'tablet' | 'phone';

/** the prototype's breakpoints: a tablet's panels become drawers, a phone gets the bottom bar */
export const TABLET_MAX = 1100;
export const PHONE_MAX = 600;

export function widthClass(w: number): WidthClass {
  return w <= PHONE_MAX ? 'phone' : w <= TABLET_MAX ? 'tablet' : 'desk';
}

/** The window's width class, read as the CSS reads it (a media query on the layout viewport): a
 *  phone that zooms out over something too wide reports a wider innerWidth, never a wider class. */
export function currentWidthClass(matches: (query: string) => boolean = (q) => matchMedia(q).matches): WidthClass {
  return matches(`(max-width: ${PHONE_MAX}px)`) ? 'phone' : matches(`(max-width: ${TABLET_MAX}px)`) ? 'tablet' : 'desk';
}

export const RAIL_W = { min: 200, max: 420, def: 248 };
export const CTX_W = { min: 280, max: 560, def: 340 };

export interface Side {
  open: boolean;
  w: number;
}

export interface Sides {
  rail: Side;
  ctx: Side;
}

/** A first visit opens a sidebar only where today's pages keep about the 1000 px they were laid
 *  out for (a 1101 px window less the old 76 px rail): the panel from 1400 px, the context from
 *  1700 px. Below, they start as the strip and the 44 px title; opened once, they stay open. */
export const RAIL_OPEN_MIN = 1400;
export const CTX_OPEN_MIN = 1700;
/** Chat's list is how you move between chats, so it starts open; its Context panel opened from
 *  1180 px before it moved into the shell, and still does. */
export const CHAT_CTX_OPEN_MIN = 1180;

export const clampW = (w: number, lim: { min: number; max: number }) => Math.round(Math.min(lim.max, Math.max(lim.min, w)));

/** The sidebars of a first visit to a page on a window this wide. */
export function defaultSides(width = CTX_OPEN_MIN, chat = false, w = { rail: RAIL_W.def, ctx: CTX_W.def }): Sides {
  if (chat) return { rail: { open: true, w: w.rail }, ctx: { open: width >= CHAT_CTX_OPEN_MIN, w: w.ctx } };
  return { rail: { open: width >= RAIL_OPEN_MIN, w: w.rail }, ctx: { open: width >= CTX_OPEN_MIN, w: w.ctx } };
}

const OLD_KEY = 'tandemapp.layout';
const OLD_CHAT = { rail: 'qse.chat.rail', ctx: 'qse.chat.ctx' };

/** What a device saved before layouts were per page: 2.2's one layout for every page, and Chat's
 *  own list and Context panel. Missing or unreadable, null; a field that is wrong is left out. */
export function legacySides(store: Pick<Storage, 'getItem'> | null): { all: Partial<Sides>; chat: { rail?: boolean; ctx?: boolean } } | null {
  if (!store) return null;
  let o: Record<string, unknown> | null = null;
  let rail: string | null = null;
  let ctx: string | null = null;
  try {
    o = JSON.parse(store.getItem(OLD_KEY) ?? 'null');
    rail = store.getItem(OLD_CHAT.rail);
    ctx = store.getItem(OLD_CHAT.ctx);
  } catch {
    /* unreadable: nothing to carry over */
  }
  const side = (v: unknown, lim: { min: number; max: number }): Side | undefined => {
    const s = v as Partial<Side> | null;
    return typeof s?.open === 'boolean' && typeof s.w === 'number' && Number.isFinite(s.w) ? { open: s.open, w: clampW(s.w, lim) } : undefined;
  };
  const all: Partial<Sides> = {};
  if (o && typeof o === 'object') {
    const r = side(o.rail, RAIL_W);
    const c = side(o.ctx, CTX_W);
    if (r) all.rail = r;
    if (c) all.ctx = c;
  }
  const chat: { rail?: boolean; ctx?: boolean } = {};
  if (rail === 'open' || rail === 'closed') chat.rail = rail === 'open';
  if (ctx === 'open' || ctx === 'closed') chat.ctx = ctx === 'open';
  return all.rail || all.ctx || chat.rail != null || chat.ctx != null ? { all, chat } : null;
}
