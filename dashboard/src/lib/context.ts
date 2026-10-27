// The right context sidebar's content (VIS-242, concept §11): every page has one. A view registers
// what it shows there -- a title, a body and tools for the header -- and a page without its own gets
// the default (the fleet's health). At its top sits the selection slot: a row, a node or a link a
// page opens with `openSelection` shows there, above the page's content, and replaces the right-side
// drawers of before. A selection belongs to the page it was opened on; leaving the page closes it.
// The left panel has the same hook: a view may show its own panel there instead of the menu (Chat
// will put its list of chats there, VIS-254). The shell (app.ts) listens and draws.

import type { TemplateResult } from 'lit';
import type { View } from './router';

export interface ContextContent {
  title: string;
  body: TemplateResult | string;
  tools?: TemplateResult;
}

export type ContextProvider = () => ContextContent;

export interface Selection {
  title: string;
  body: TemplateResult | string;
  /** the page it was opened on (pageKey) */
  page: string;
}

const registry = new Map<View, ContextProvider>();
const panels = new Map<View, () => TemplateResult>();
let fallback: ContextProvider = () => ({ title: 'Context', body: '' });
let selection: Selection | null = null;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((f) => f());

/** The page a selection belongs to: the view and its path, not its params (a scope change keeps
 *  the selection, another node's page does not). */
export function pageKey(view: View, path: string[]): string {
  return [view, ...path].join('/');
}

let current = '';
/** The shell says which page shows; a selection from another page closes. */
export function setPage(key: string): void {
  if (key === current) return;
  current = key;
  if (selection && selection.page !== key) {
    selection = null;
    changed();
  }
}

/** A view's content for the sidebar; returns the unregister. Registering again replaces it. */
export function registerContext(view: View, fn: ContextProvider): () => void {
  registry.set(view, fn);
  changed();
  return () => {
    if (registry.get(view) !== fn) return;
    registry.delete(view);
    changed();
  };
}

/** A view's own left panel (in place of the menu); returns the unregister. */
export function registerPanel(view: View, fn: () => TemplateResult): () => void {
  panels.set(view, fn);
  changed();
  return () => {
    if (panels.get(view) !== fn) return;
    panels.delete(view);
    changed();
  };
}

/** the view's own left panel, or null for the menu */
export function panelFor(view: View): TemplateResult | null {
  return panels.get(view)?.() ?? null;
}

/** The content for every page that registered none. */
export function setDefaultContext(fn: ContextProvider): void {
  fallback = fn;
  changed();
}

export function contextFor(view: View): ContextContent {
  return (registry.get(view) ?? fallback)();
}

/** Shows a selection at the top of the sidebar (and opens it). */
export function openSelection(title: string, body: TemplateResult | string): void {
  selection = { title, body, page: current };
  changed();
}

export function clearSelection(): void {
  if (!selection) return;
  selection = null;
  changed();
}

export function currentSelection(): Selection | null {
  return selection;
}

/** Hears every change: a registration, the default, a selection opened or closed. */
export function onContext(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** content that changed in place (live figures): the shell draws the sidebar again */
export const refreshContext = changed;
