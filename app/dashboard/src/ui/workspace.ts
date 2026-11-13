// <qse-workspace>: the page's internal windows (VIS-256, the v3 prototype's window manager). Two docks,
// one beside the page and one below it, each a strip of tabs (a window's icon, title and close) with
// its tools: add a window here, move to the other dock, float, maximise, minimise, close the dock; a
// separator on its edge resizes it (pointer, or the arrow keys by 5 %). A floating window has a title
// bar to drag it (near the right or the bottom edge it docks there), a corner to size it, and the same
// tools; the one pressed last is on top. Minimised windows wait as chips in the corner. On a phone the
// windows are a tray of full-width sheets, one at a time, opened from the bottom bar's Windows slot.
// The add menu and the layout menu (dock everything, float, tile, minimise, the sidebars, Reset) open
// from the top bar's buttons and the docks. Short transitions, none with reduced motion (CSS).
//
// The layout is lib/workspace.ts's (one page, one device class); a window's body is an element that
// stays while the window moves, so a terminal keeps its session: a kind's own (windows/kinds.ts, its
// chunk loaded on first use) or the page's (Chat's panes, registerWindows). The element renders into
// the light DOM as `display: contents`, so its docks sit in the centre's grid beside and below the page.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { repeat } from 'lit/directives/repeat.js';
import { sparksOf, type FleetNode, type LayoutWindow, type WindowKind } from '../api/fleet';
import { onFleet } from '../lib/fleet-state';
import { toggleSidebar, sidebarShown } from '../lib/context';
import { allTo, activate, close, dockOf, move, open, place, restore, setDockF, setRef, tile, type DockSide, type Ref, type Where } from '../lib/wm';
import {
  change,
  currentPage,
  onWorkspace,
  openWindowMenu,
  pageSource,
  resetAll,
  resetPage,
  setTray,
  showInTray,
  shownLayout,
  trayOpen,
  trayShows,
  windowMenu,
  type WindowMenu,
} from '../lib/workspace';
import { menuItem, type Section } from '../lib/menu';
import { timeShort } from '../lib/format';
import { GROUPS, KINDS, windowTitle } from '../windows/kinds';
import { LightElement } from './base';
import { icon } from './bits';
import type { WidthClass } from '../lib/layout';

/** a window body element: what it shows, and whether it is on a phone's tray */
interface WinBody extends HTMLElement {
  win: LayoutWindow;
  sheet: boolean;
}

/** Opens a window on the page that shows: the page's own kinds go to the page (Chat: a pane of the
 *  open chat) when nothing more is asked of them; on a phone the tray opens on it. */
export function openWindow(kind: WindowKind, ref: Ref = {}, where: Where = 'auto'): void {
  const src = pageSource();
  showInTray(null);
  if (src && src.kinds.includes(kind) && !Object.keys(ref).length) src.open(kind);
  else {
    const { page } = currentPage();
    change((l) => open(l, kind, { ...KINDS[kind].init(), ...ref }, where, page).layout);
  }
  if (currentPage().device === 'phone') setTray(true);
}

const DRAG_START = 7;
const ZONE = 0.74;

@customElement('qse-workspace')
export class QseWorkspace extends LightElement {
  @property({ type: String }) device: WidthClass = 'desk';
  @state() private nodes: FleetNode[] = [];
  /** the window shown over the page, maximised (not saved: a reload brings the layout back) */
  @state() private max: string | null = null;
  @state() private zone: DockSide | null = null;
  @state() private dragging = false;
  private bodies = new Map<string, WinBody>();
  private drag: { mode: 'move' | 'size' | 'dock'; id: string; side?: DockSide; x: number; y: number; x0: number; y0: number; w0: number; h0: number; moved: boolean; el?: HTMLElement | null } | null = null;
  private unsub: (() => void)[] = [];
  /** the scope bar above the page and the right dock: the dock leaves it its height */
  private scopeObs: ResizeObserver | null = null;
  private scopeEl: Element | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    this.unsub.push(
      onWorkspace(() => this.requestUpdate()),
      onFleet((f) => (this.nodes = sparksOf(f?.nodes))),
    );
    window.addEventListener('pointermove', this.onMove);
    window.addEventListener('pointerup', this.onUp);
    window.addEventListener('keydown', this.onKey);
    document.addEventListener('pointerdown', this.onDocDown, true);
    this.addEventListener('wm-ref', this.onRef as EventListener);
    this.addEventListener('wm-open', this.onOpen as EventListener);
  }
  disconnectedCallback(): void {
    this.unsub.forEach((u) => u());
    this.unsub = [];
    window.removeEventListener('pointermove', this.onMove);
    window.removeEventListener('pointerup', this.onUp);
    window.removeEventListener('keydown', this.onKey);
    document.removeEventListener('pointerdown', this.onDocDown, true);
    this.removeEventListener('wm-ref', this.onRef as EventListener);
    this.removeEventListener('wm-open', this.onOpen as EventListener);
    this.scopeObs?.disconnect();
    this.scopeObs = null;
    this.scopeEl = null;
    super.disconnectedCallback();
  }

  /** a window's body changed what it shows (a node, a filter): saved with the layout */
  private onRef = (e: CustomEvent<{ id: string; patch: Ref }>) => {
    e.stopPropagation();
    change((l) => setRef(l, e.detail.id, e.detail.patch));
  };
  /** a window opens another (the inspector's Terminal, the Files' Edit) */
  private onOpen = (e: CustomEvent<{ kind: WindowKind; ref?: Ref; where?: Where }>) => {
    e.stopPropagation();
    openWindow(e.detail.kind, e.detail.ref ?? {}, e.detail.where ?? 'auto');
  };
  private onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Escape') return;
    if (windowMenu()) openWindowMenu(null);
    else if (this.max) this.max = null;
  };
  private onDocDown = (e: Event) => {
    const t = e.target as HTMLElement | null;
    if (windowMenu() && t && !t.closest('.wm-menu, [data-wm-menu]')) openWindowMenu(null);
    // the floating window pressed comes on top
    const f = t?.closest<HTMLElement>('[data-float]');
    if (f) {
      const l = shownLayout();
      const w = l.windows.find((x) => x.id === f.dataset.float);
      if (w && w.z !== Math.max(...l.windows.map((x) => x.z))) change((x) => activate(x, w.id));
    }
  };

  private alias = (id: string) => this.nodes.find((n) => n.id === id)?.alias ?? id;

  private winTitle(w: LayoutWindow): string {
    if (w.ref.source) return pageSource()?.windows().find((s) => s.id === w.id)?.title ?? KINDS[w.kind].label;
    // a window on "a Spark" (Hardware's default) is on the fleet's first until one is picked
    const ref = w.ref.node === null && this.nodes[0] ? { ...w.ref, node: this.nodes[0].id } : w.ref;
    return windowTitle(w.kind, ref, this.alias);
  }
  private iconOf(w: LayoutWindow): string {
    if (w.ref.source) return pageSource()?.windows().find((s) => s.id === w.id)?.icon ?? KINDS[w.kind].icon;
    return KINDS[w.kind].icon;
  }

  /** a window's body, kept while the window lives */
  private body(w: LayoutWindow): Node | TemplateResult {
    const sheet = this.device === 'phone';
    if (w.ref.source) return pageSource()?.body(w.id, sheet) ?? nothing as unknown as TemplateResult;
    const k = KINDS[w.kind];
    let el = this.bodies.get(w.id);
    if (!el || el.localName !== k.tag) {
      el = document.createElement(k.tag) as WinBody;
      el.classList.add('wm-content');
      this.bodies.set(w.id, el);
      void k.load();
    }
    el.win = w;
    el.sheet = sheet;
    return el;
  }

  // ---- what a tool does ------------------------------------------------------------------------

  private closeWin(w: LayoutWindow): void {
    if (this.max === w.id) this.max = null;
    if (w.ref.source) pageSource()?.close(w.id);
    else {
      this.bodies.delete(w.id);
      change((l) => close(l, w.id));
    }
  }
  private closeDock(side: DockSide): void {
    const { wins } = dockOf(shownLayout(), side);
    if (wins.some((w) => w.ref.source)) pageSource()?.hide();
    for (const w of wins) if (!w.ref.source) this.bodies.delete(w.id);
    change((l) => wins.filter((w) => !w.ref.source).reduce((x, w) => close(x, w.id), l));
    this.max = null;
  }
  private show(w: LayoutWindow): void {
    if (w.ref.source) pageSource()?.activate(w.id);
    change((l) => activate(l, w.id));
  }
  private moveTo(w: LayoutWindow, at: LayoutWindow['at']): void {
    if (at === 'min' && this.max === w.id) this.max = null;
    change((l) => move(l, w.id, at));
  }

  private openMenu(kind: WindowMenu['kind'], e: Event, target?: WindowMenu['target']): void {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const m = windowMenu();
    if (m && m.kind === kind && m.target === target) return openWindowMenu(null);
    openWindowMenu({ kind, at: { left: r.left, top: r.top, bottom: r.bottom, right: r.right }, target });
  }

  // ---- drag: move a window (a tab or a title bar), size a float, size a dock ---------------------

  private startDrag(e: PointerEvent, mode: 'move' | 'size', w: LayoutWindow): void {
    if (e.button || this.device === 'phone') return;
    if (mode === 'move' && (e.target as HTMLElement).closest('button:not(.wm-tab-b), select, input, textarea, a')) return;
    this.drag = { mode, id: w.id, x: e.clientX, y: e.clientY, x0: w.x, y0: w.y, w0: w.w, h0: w.h, moved: false };
    if (mode === 'size') e.preventDefault();
  }
  private startDock(e: PointerEvent, side: DockSide): void {
    if (e.button) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).classList.add('is-drag');
    this.drag = { mode: 'dock', id: '', side, x: e.clientX, y: e.clientY, x0: 0, y0: 0, w0: 0, h0: 0, moved: true, el: e.currentTarget as HTMLElement };
  }
  private box(): DOMRect {
    return this.querySelector<HTMLElement>('.wm-floats')?.getBoundingClientRect() ?? new DOMRect(0, 0, innerWidth, innerHeight);
  }
  private onMove = (e: PointerEvent) => {
    const d = this.drag;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    const b = this.box();
    if (d.mode === 'dock') {
      const f = d.side === 'right' ? (b.right - e.clientX) / b.width : (b.bottom - e.clientY) / b.height;
      change((l) => setDockF(l, d.side!, f));
      return;
    }
    const el = this.querySelector<HTMLElement>(`[data-float="${d.id}"]`);
    if (d.mode === 'size') {
      if (el) {
        el.style.width = `${Math.max(260, d.w0 + dx)}px`;
        el.style.height = `${Math.max(160, d.h0 + dy)}px`;
      }
      return;
    }
    if (!d.moved) {
      if (Math.hypot(dx, dy) < DRAG_START) return;
      d.moved = true;
      this.dragging = true;
      const w = shownLayout().windows.find((x) => x.id === d.id);
      if (w && w.at !== 'float') {
        // a docked tab torn off: it floats under the pointer
        d.x0 = Math.max(0, Math.min(e.clientX - b.left - 120, b.width - 300));
        d.y0 = Math.max(0, Math.min(e.clientY - b.top - 16, b.height - 100));
        d.x = e.clientX;
        d.y = e.clientY;
        change((l) => place(move(l, d.id, 'float'), d.id, { x: d.x0, y: d.y0 }));
        return;
      }
    }
    const x = Math.max(-200, d.x0 + dx);
    const y = Math.max(0, d.y0 + dy);
    if (el) {
      el.style.left = `${x}px`;
      el.style.top = `${y}px`;
    }
    const px = e.clientX - b.left;
    const py = e.clientY - b.top;
    const zone = px > b.width * ZONE ? 'right' : py > b.height * ZONE ? 'bottom' : null;
    if (zone !== this.zone) this.zone = zone;
  };
  private onUp = (e: PointerEvent) => {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    this.dragging = false;
    const zone = this.zone;
    this.zone = null;
    if (d.mode === 'dock') {
      d.el?.classList.remove('is-drag');
      return;
    }
    if (d.mode === 'size') {
      change((l) => place(l, d.id, { w: d.w0 + e.clientX - d.x, h: d.h0 + e.clientY - d.y }));
      return;
    }
    if (!d.moved) return;
    if (zone) change((l) => move(l, d.id, zone));
    else change((l) => place(l, d.id, { x: Math.max(-200, d.x0 + e.clientX - d.x), y: d.y0 + e.clientY - d.y }));
  };
  private onSepKey(e: KeyboardEvent, side: DockSide): void {
    const grow = side === 'right' ? e.key === 'ArrowLeft' : e.key === 'ArrowUp';
    const shrink = side === 'right' ? e.key === 'ArrowRight' : e.key === 'ArrowDown';
    if (!grow && !shrink) return;
    e.preventDefault();
    change((l) => setDockF(l, side, l.docks[side].f + (grow ? 0.05 : -0.05)));
  }

  // ---- render ------------------------------------------------------------------------------------

  willUpdate(): void {
    const l = shownLayout();
    // a maximised window that closed or went to the corner is not maximised any more
    if (this.max && !l.windows.some((w) => w.id === this.max && w.at !== 'min')) this.max = null;
  }

  updated(): void {
    // the centre's grid and the page's room: the right dock's column, the bottom dock's height
    const c = this.parentElement;
    if (!c) return;
    const l = shownLayout();
    const phone = this.device === 'phone';
    const right = !phone && l.windows.some((w) => w.at === 'right');
    const bottom = !phone && l.windows.some((w) => w.at === 'bottom');
    const f = l.docks.right.f;
    c.style.setProperty('--wm-cols', right ? `minmax(0, ${Math.round((1 - f) * 1000)}fr) minmax(240px, ${Math.round(f * 1000)}fr)` : 'minmax(0, 1fr) 0px');
    c.style.setProperty('--dock-b', bottom ? `calc((100dvh - var(--topbar-h)) * ${l.docks.bottom.f})` : '0px');
    c.toggleAttribute('data-dock-r', right);
    c.toggleAttribute('data-dock-b', bottom);
    const sb = c.querySelector(':scope > .scopebar');
    if (sb !== this.scopeEl) {
      this.scopeEl = sb;
      this.scopeObs ??= new ResizeObserver(() => c.style.setProperty('--scope-h', `${(this.scopeEl as HTMLElement | null)?.offsetHeight ?? 0}px`));
      this.scopeObs.disconnect();
      if (sb) this.scopeObs.observe(sb);
      c.style.setProperty('--scope-h', `${(sb as HTMLElement | null)?.offsetHeight ?? 0}px`);
    }
    // a window that went away takes its body with it
    for (const id of this.bodies.keys()) if (!l.windows.some((w) => w.id === id)) this.bodies.delete(id);
  }

  render() {
    const l = shownLayout();
    const phone = this.device === 'phone';
    const menu = windowMenu();
    if (phone) return html`${trayOpen() ? this.renderTray() : nothing}${menu ? this.renderMenu(menu) : nothing}`;
    const floats = l.windows.filter((w) => w.at === 'float').sort((a, b) => a.z - b.z);
    const top = floats[floats.length - 1]?.id;
    const mins = l.windows.filter((w) => w.at === 'min');
    return html`${this.renderDock('right')}${this.renderDock('bottom')}
      <div class="wm-floats ${this.dragging ? 'is-dragging' : ''}">
        ${repeat(floats, (w) => w.id, (w) => this.renderFloat(w, w.id === top))}
        ${this.dragging ? html`<div class="wm-drop" aria-hidden="true"><i class="z-r ${this.zone === 'right' ? 'is-hot' : ''}">Dock right</i><i class="z-b ${this.zone === 'bottom' ? 'is-hot' : ''}">Dock bottom</i></div>` : nothing}
      </div>
      ${mins.length ? html`<div class="wm-mins" role="toolbar" aria-label="Minimised windows">${mins.map((w) => html`<button type="button" class="wm-min" title="Restore" @click=${() => change((x) => restore(x, w.id))}>${icon(this.iconOf(w))}<span>${this.winTitle(w)}</span></button>`)}</div>` : nothing}
      ${menu ? this.renderMenu(menu) : nothing}`;
  }

  private renderDock(side: DockSide): TemplateResult | typeof nothing {
    const l = shownLayout();
    const { wins, act } = dockOf(l, side);
    if (!wins.length || !act) return nothing;
    const other: DockSide = side === 'right' ? 'bottom' : 'right';
    const maxed = this.max != null && wins.some((w) => w.id === this.max);
    const shown = maxed ? wins.find((w) => w.id === this.max)! : act;
    const label = side === 'right' ? 'Right dock' : 'Bottom dock';
    return html`<section class="wm-dock wm-dock-${side} ${maxed ? 'is-max' : ''}" aria-label=${label} data-dock=${side}>
      <div class="wm-rz" role="separator" aria-orientation=${side === 'right' ? 'vertical' : 'horizontal'} aria-label=${`Resize the ${side} dock`} aria-valuemin=${side === 'right' ? 20 : 15} aria-valuemax=${side === 'right' ? 75 : 80} aria-valuenow=${Math.round(l.docks[side].f * 100)} tabindex="0" @pointerdown=${(e: PointerEvent) => this.startDock(e, side)} @keydown=${(e: KeyboardEvent) => this.onSepKey(e, side)}></div>
      <div class="wm-strip">
        <div class="wm-tabs" role="tablist" aria-label=${label}>
          ${wins.map(
            (w) => html`<div class="wm-tab ${w.id === shown.id ? 'is-on' : ''}" role="presentation" data-win=${w.id} @pointerdown=${(e: PointerEvent) => this.startDrag(e, 'move', w)}>
              <button type="button" class="wm-tab-b" role="tab" aria-selected=${w.id === shown.id} title=${this.winTitle(w)} @click=${() => this.show(w)}>${icon(this.iconOf(w))}<span>${this.winTitle(w)}</span></button>
              <button type="button" class="wm-x" aria-label=${`Close ${this.winTitle(w)}`} @click=${() => this.closeWin(w)}>${icon('x')}</button>
            </div>`,
          )}
        </div>
        <div class="wm-tools">
          <button type="button" data-wm-menu aria-label="Add a window" title="Add a window here" aria-haspopup="menu" @click=${(e: Event) => this.openMenu('add', e, side)}>${icon('plus')}</button>
          <button type="button" aria-label=${`Move to the ${other} dock`} title=${`Move to the ${other} dock`} @click=${() => this.moveTo(shown, other)}>${icon(other === 'right' ? 'dock-right' : 'dock-bottom')}</button>
          <button type="button" aria-label="Float" title="Float" @click=${() => this.moveTo(shown, 'float')}>${icon('float')}</button>
          <button type="button" aria-label=${maxed ? 'Restore' : 'Maximise'} title=${maxed ? 'Restore' : 'Maximise'} aria-pressed=${maxed} @click=${() => (this.max = maxed ? null : shown.id)}>${icon(maxed ? 'unmax' : 'max')}</button>
          <button type="button" aria-label="Minimise" title="Minimise" @click=${() => this.moveTo(shown, 'min')}>${icon('min')}</button>
          <button type="button" aria-label=${`Close the ${side} dock`} title="Close every window in this dock" @click=${() => this.closeDock(side)}>${icon('x')}</button>
        </div>
      </div>
      <div class="wm-bodies">${repeat(wins, (w) => w.id, (w) => html`<div class="wm-body" role="tabpanel" aria-label=${this.winTitle(w)} data-win=${w.id} ?hidden=${w.id !== shown.id}>${this.body(w)}</div>`)}</div>
    </section>`;
  }

  private renderFloat(w: LayoutWindow, top: boolean): TemplateResult {
    const maxed = this.max === w.id;
    return html`<section class="wm-win ${top ? 'is-top' : ''} ${maxed ? 'is-max' : ''}" role="dialog" aria-label=${this.winTitle(w)} data-float=${w.id} data-win=${w.id} style=${`left:${w.x}px;top:${w.y}px;width:${w.w}px;height:${w.h}px;z-index:${w.z}`}>
      <div class="wm-win-h" @pointerdown=${(e: PointerEvent) => this.startDrag(e, 'move', w)} @dblclick=${() => (this.max = maxed ? null : w.id)}>
        <span class="wm-win-t">${icon(this.iconOf(w))}<span>${this.winTitle(w)}</span></span>
        <div class="wm-tools">
          <button type="button" aria-label="Dock right" title="Dock right" @click=${() => this.moveTo(w, 'right')}>${icon('dock-right')}</button>
          <button type="button" aria-label="Dock bottom" title="Dock bottom" @click=${() => this.moveTo(w, 'bottom')}>${icon('dock-bottom')}</button>
          <button type="button" aria-label=${maxed ? 'Restore' : 'Maximise'} title=${maxed ? 'Restore' : 'Maximise'} aria-pressed=${maxed} @click=${() => (this.max = maxed ? null : w.id)}>${icon(maxed ? 'unmax' : 'max')}</button>
          <button type="button" aria-label="Minimise" title="Minimise" @click=${() => this.moveTo(w, 'min')}>${icon('min')}</button>
          <button type="button" aria-label=${`Close ${this.winTitle(w)}`} title="Close" @click=${() => this.closeWin(w)}>${icon('x')}</button>
        </div>
      </div>
      <div class="wm-body">${this.body(w)}</div>
      <div class="wm-size" aria-hidden="true" @pointerdown=${(e: PointerEvent) => this.startDrag(e, 'size', w)}></div>
    </section>`;
  }

  private renderTray(): TemplateResult {
    const l = shownLayout();
    const wins = l.windows.filter((w) => w.at !== 'min');
    const actId = trayShows(wins.map((w) => w.id));
    const act = wins.find((w) => w.id === actId) ?? null;
    return html`<section class="wm-tray" role="dialog" aria-label="Windows">
      <div class="wm-tray-h">
        <div class="wm-tray-tabs" role="tablist" aria-label="Windows">${wins.map((w) => html`<button type="button" role="tab" class=${w.id === act?.id ? 'is-on' : ''} aria-selected=${w.id === act?.id} data-win=${w.id} @click=${() => (showInTray(w.id), this.show(w))}>${icon(this.iconOf(w))}<span>${this.winTitle(w)}</span></button>`)}</div>
        <button type="button" class="icon-btn" data-wm-menu aria-label="Add a window" @click=${(e: Event) => this.openMenu('add', e)}>${icon('plus')}</button>
        ${act ? html`<button type="button" class="icon-btn" aria-label=${`Close ${this.winTitle(act)}`} @click=${() => this.closeWin(act)}>${icon('trash')}</button>` : nothing}
        <button type="button" class="icon-btn" aria-label="Hide the windows" @click=${() => setTray(false)}>${icon('down')}</button>
      </div>
      ${act
        ? html`<div class="wm-bodies">${repeat(wins, (w) => w.id, (w) => html`<div class="wm-body" role="tabpanel" data-win=${w.id} ?hidden=${w.id !== act.id}>${this.body(w)}</div>`)}</div>`
        : html`<p class="wm-empty">No window open on this page. Press + to add a terminal, files, logs, a chart or a preview.</p>`}
    </section>`;
  }

  // ---- the menus ---------------------------------------------------------------------------------

  private renderMenu(m: WindowMenu): TemplateResult {
    const w = 270;
    const left = Math.max(8, Math.min(m.at.right - w, innerWidth - w - 8));
    const below = m.at.bottom + 6;
    const style = below + 320 < innerHeight || m.at.top < 320 ? `left:${left}px;top:${below}px` : `left:${left}px;bottom:${innerHeight - m.at.top + 6}px`;
    return html`<div class="wm-menu" role="menu" aria-label=${m.kind === 'add' ? 'Add a window' : 'Layout'} style=${style}>${m.kind === 'add' ? this.addMenu(m) : this.layoutMenu()}</div>`;
  }

  private addMenu(m: WindowMenu): TemplateResult {
    const { page, device } = currentPage();
    const target = m.target ?? 'auto';
    const src = pageSource();
    const pick = (k: WindowKind) => {
      openWindowMenu(null);
      if (src && src.kinds.includes(k)) {
        showInTray(null);
        src.open(k);
        if (device === 'phone') setTray(true);
      } else openWindow(k, KINDS[k].init(), target);
    };
    return html`<div class="wm-menu-h"><span class="eyebrow">Add a window</span><span class="muted small">${menuItem(page as Section).label}</span></div>
      ${device === 'phone'
        ? nothing
        : html`<div class="seg wm-target" role="radiogroup" aria-label="Where it goes">${(['auto', 'right', 'bottom', 'float'] as const).map((t) => html`<button type="button" class="seg-btn ${t === target ? 'is-on' : ''}" role="radio" aria-checked=${t === target} @click=${() => openWindowMenu({ ...m, target: t })}>${t === 'auto' ? 'Auto' : t === 'right' ? 'Right' : t === 'bottom' ? 'Bottom' : 'Float'}</button>`)}</div>`}
      ${GROUPS.map(
        ([g, kinds]) => html`<div class="wm-menu-g eyebrow">${g}</div>
          ${kinds.map((k) => html`<button type="button" role="menuitem" class="wm-mi" data-kind=${k} @click=${() => pick(k)}>${icon(KINDS[k].icon)}<span>${KINDS[k].label}</span><span class="wm-mi-meta">${src?.kinds.includes(k) ? 'this chat' : KINDS[k].hint}</span></button>`)}`,
      )}`;
  }

  private layoutMenu(): TemplateResult {
    const l = shownLayout();
    const { page } = currentPage();
    const n = l.windows.length;
    const all = (at: LayoutWindow['at']) => () => (openWindowMenu(null), change((x) => allTo(x, at)));
    const b = this.box();
    return html`<div class="wm-menu-h"><span class="eyebrow">Layout of ${menuItem(page as Section).label}</span></div>
      <p class="wm-menu-note muted small" data-saved>${n} window${n === 1 ? '' : 's'} · ${l.saved_at ? `saved at ${timeShort(new Date(l.saved_at * 1000).toISOString())}` : 'the default'}</p>
      ${this.device === 'phone'
        ? nothing
        : html`<button type="button" role="menuitem" class="wm-mi" ?disabled=${!n} @click=${all('right')}>${icon('dock-right')}<span>All windows to the right dock</span></button>
            <button type="button" role="menuitem" class="wm-mi" ?disabled=${!n} @click=${all('bottom')}>${icon('dock-bottom')}<span>All windows to the bottom dock</span></button>
            <button type="button" role="menuitem" class="wm-mi" ?disabled=${!n} @click=${all('float')}>${icon('float')}<span>Float all</span></button>
            <button type="button" role="menuitem" class="wm-mi" ?disabled=${!l.windows.some((w) => w.at === 'float')} @click=${() => (openWindowMenu(null), change((x) => tile(x, b.width, b.height)))}>${icon('overview')}<span>Tile the floating windows</span></button>
            <button type="button" role="menuitem" class="wm-mi" ?disabled=${!n} @click=${all('min')}>${icon('min')}<span>Minimise all</span></button>
            <hr />`}
      <button type="button" role="menuitem" class="wm-mi" @click=${() => (openWindowMenu(null), toggleSidebar('rail'))}>${icon('side-left')}<span>Left sidebar</span><span class="wm-mi-meta">${sidebarShown('rail') ? 'shown' : 'hidden'}</span></button>
      <button type="button" role="menuitem" class="wm-mi" @click=${() => (openWindowMenu(null), toggleSidebar('ctx'))}>${icon('side-right')}<span>Right sidebar</span><span class="wm-mi-meta">${sidebarShown('ctx') ? 'shown' : 'hidden'}</span></button>
      <hr />
      <button type="button" role="menuitem" class="wm-mi" data-reset="page" @click=${() => (openWindowMenu(null), (this.max = null), resetPage())}>${icon('refresh')}<span>Reset this page’s layout</span></button>
      <button type="button" role="menuitem" class="wm-mi is-danger" data-reset="all" @click=${() => (openWindowMenu(null), (this.max = null), resetAll())}>${icon('trash')}<span>Reset every layout and the sidebars</span></button>`;
  }
}
