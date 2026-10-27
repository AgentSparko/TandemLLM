// The phone layout's decisions, kept pure so they can be tested: which sections sit in the bottom
// bar and which go behind More, what counts as typing (the bar steps aside then), and where a
// scrolling strip of pills stands so its edges can fade. The shell (app.ts) and the composer
// read these.

import type { MenuItem, Section } from './menu';

export interface TabSplit {
  /** the slots in the bar, in order */
  bar: MenuItem[];
  /** the sections behind More, in the menu's order */
  more: MenuItem[];
  /** the More slot shows this section's icon and label when the active one is behind More, so the
   *  page you are on is never invisible (the setup guide in setup mode lands here too, as Models) */
  moreShows: MenuItem | null;
}

/** The phone's bottom bar: the primary sections in the menu's order, the rest behind More. */
export function splitTabs(items: MenuItem[], primary: Section[], active: Section): TabSplit {
  const bar = items.filter((t) => primary.includes(t.id));
  const more = items.filter((t) => !primary.includes(t.id));
  return { bar, more, moreShows: more.find((t) => t.id === active) ?? null };
}

export type Edge = 'none' | 'start' | 'mid' | 'end';

/** Where a horizontal scroller stands: nothing to scroll, at the start, in the middle, at the end.
 *  The strip fades the edge that has more behind it. */
export function stripEdge(scrollLeft: number, scrollWidth: number, clientWidth: number): Edge {
  if (scrollWidth <= clientWidth + 1) return 'none';
  if (scrollLeft <= 1) return 'start';
  if (scrollLeft + clientWidth >= scrollWidth - 1) return 'end';
  return 'mid';
}

const TYPING_INPUTS = new Set(['text', 'search', 'url', 'email', 'tel', 'password', 'number']);

/** True for an element that brings up a keyboard: a textarea, a text-like input, or editable text. */
export function isTypingTarget(el: Element | null): boolean {
  if (!el) return false;
  const tag = el.tagName;
  if (tag === 'TEXTAREA') return !(el as HTMLTextAreaElement).readOnly;
  if (tag === 'INPUT') {
    const inp = el as HTMLInputElement;
    return TYPING_INPUTS.has((inp.type || 'text').toLowerCase()) && !inp.readOnly;
  }
  return (el as HTMLElement).isContentEditable === true;
}
