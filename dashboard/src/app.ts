// <qse-app>: TandemApp's shell (VIS-242, concept §4, §11, §12). The top bar: the left sidebar's
// toggle, the brand, the engine's status pill (and the session monitor's mode beside it, VIS-44), the
// crumb, the fleet readout (a swatch per Spark in its colour, ringed when it alerts, the fleet's decode
// rate and the alerts; it opens Events & Alerts), the window and layout buttons (VIS-256), the right
// sidebar's toggle and the theme. Under it three columns: on the left an icon strip that is always
// there (the menu's groups, a sliding indicator on the page you are on, the Sparks' swatches) and a
// panel beside it, resizable and collapsible, with the grouped menu and the sub-pages of the section
// you are in (a view may put its own panel there); in the middle the scope bar on the Monitor pages
// and the page; on the right the context sidebar, on every page, resizable, collapsing to a strip
// with its title, with a selection slot at its top (lib/context.ts) and each page's own content
// (ui/page-context.ts; Chat's Context panel is Chat's). Around the page sit its windows (VIS-256,
// ui/workspace.ts): docked beside it and below it, floating or minimised, a tray on a phone. Each
// page's layout -- its windows and whether its sidebars are open -- is saved per device class, in this
// browser and on TandemApp's controller (lib/workspace.ts); the sidebars' widths are one per device.
// A tablet gets both sidebars as drawers over the page; a phone gets the strip and the panel in the
// left drawer, the context in the right one and a bottom bar: Chat, Overview, Performance, Windows
// (the tray) and More (the grouped menu as a sheet).
//
// The dashboard asks for no password anywhere (AU1, 2026-10-06): with no password set on the server
// every route is open; once one is set, a 401 shows a banner (text only, no prompt); the setup guide
// alone has a sign-in, for its own routes. The shell owns the summary poll (60 s) and hands it to the
// Usage view so the pill and the cards come from one request. In setup mode (OPS-93: /health says
// `mode: "setup"`, every /v1/dashboard/* route answers 503 setup_mode) the shell polls nothing but
// /health, shows the setup guide only (Models, Serve a model), and starts its polls once the box
// serves a model.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { api, apiBase, describeError, engineAway, onApi } from './api/client';
import { awayText, restartText, swAway, whenEngineUp } from './lib/away';
import { config } from './api/config';
import { perfEngineNote } from './lib/perf-note';
import type { Summary, LiveStatus } from './api/types';
import { duration, shortHash } from './lib/format';
import { modeWord } from './lib/supervisor';
import { poll, type Poller } from './lib/poll';
import { fixHash, navigate, onRoute, parseHash, type Route } from './lib/router';
import { currentTheme, toggleTheme, watchSystemTheme, type Theme } from './lib/theme';
import { registerWorker } from './lib/pwa';
import { knownJob, rememberStatusPort } from './lib/restart';
import { browserTz } from './lib/time';
import { isTypingTarget, splitTabs } from './lib/phone';
import { MENU, MONITOR, PHONE_BAR, crumb, sectionHref, sectionOf, subPages, type MenuItem, type Section } from './lib/menu';
import { CTX_W, RAIL_W, currentWidthClass, type WidthClass } from './lib/layout';
import { clearSelection, contextFor, currentSelection, onContext, pageKey, panelFor, setDefaultContext, setPage, setSidebarControl, sidebarsChanged } from './lib/context';
import { flush, onWorkspace, openWindowMenu, setTray, shownLayout, showPage, trayOpen, windowMenu, change, currentLayout, savedLayouts } from './lib/workspace';
import { resizeSide, setSide } from './lib/wm';
import './ui/workspace';
import { pageContext } from './ui/page-context';
import { onFleet, readout, type FleetState } from './lib/fleet-state';
import { LightElement, type Loadable } from './ui/base';
import { icon } from './ui/bits';
import { nodeColour, isHollow } from './ui/node-chip';
import './ui/scope-bar';
import { S } from './setup/strings';

/** the Chat tab is in beta in 0.8.0 (docs/chat.md "known limitations"): a small pill beside it */
const BETA = html`<span class="beta-pill" title="The Chat tab is in beta">Beta</span>`;

/** a focus that comes and goes within this window is a tap between two fields, not a keyboard
 *  that closed: the bar waits before it comes back */
const TYPING_SETTLE_MS = 60;
/** how long a new page's blocks take to rise in (the stagger's last delay and its length) */
const ENTER_MS = 900;
const MENU_ITEMS: MenuItem[] = MENU.flatMap((g) => g.items);

/** The Playground view is its own chunk; the element upgrades in place once the chunk has loaded. */
let playgroundChunk: Promise<unknown> | null = null;
const loadPlayground = () => (playgroundChunk ??= import('./views/playground'));
/** The Chat tab (VIS-99): the conversation, its panes and the Context panel, its own chunk; the
 *  mock backend and the remote client load inside it on demand. */
let chatChunk: Promise<unknown> | null = null;
const loadChat = () => (chatChunk ??= import('./chat/view'));
/** The Conductor view, its own chunk too: the editors are not needed on the pages watched daily. */
let conductorChunk: Promise<unknown> | null = null;
const loadConductor = () => (conductorChunk ??= import('./views/conductor'));
/** The Backends view (the proxy's registry), its own chunk: set up once, opened rarely. */
let backendsChunk: Promise<unknown> | null = null;
const loadBackends = () => (backendsChunk ??= import('./views/backends'));
/** The setup guide (VIS-78): the wizard, Wren and the reset levels, its own chunk. */
let setupChunk: Promise<unknown> | null = null;
const loadSetup = () => (setupChunk ??= import('./setup/view'));
/** Performance → Requests (every request on every backend at once), its own chunk. */
let parallelChunk: Promise<unknown> | null = null;
const loadParallel = () => (parallelChunk ??= import('./views/parallel'));
/** TandemApp's new pages (VIS-242) with the fleet chart and its modes, one chunk. */
let fleetChunk: Promise<unknown> | null = null;
const loadFleetPages = () => (fleetChunk ??= import('./views/fleet-pages'));

/** The two pages of Performance: the engine's own (the Live panel, the scatter, the history) and
 *  every request in flight on every backend at once. */
function perfTabs(on: 'live' | 'requests', parallel: number | null = null): TemplateResult {
  const go = (tab: string | null) => navigate('performance', { ...Object.fromEntries(parseHash().params), tab });
  return html`<div class="perf-tabs" role="tablist" aria-label="Performance pages">
    <div class="seg">
      <button class="seg-btn ${on === 'live' ? 'is-on' : ''}" role="tab" aria-selected=${on === 'live'} @click=${() => go(null)}>Live</button>
      <button class="seg-btn ${on === 'requests' ? 'is-on' : ''}" role="tab" aria-selected=${on === 'requests'} data-tab="requests" @click=${() => go('requests')}>Requests</button>
    </div>
    <span class="perf-tabs-note">${on === 'requests' ? 'every request in flight, on every backend' : perfEngineNote(parallel)}</span>
  </div>`;
}

@customElement('qse-app')
export class QseApp extends LightElement {
  /** a 401 came back: the server has an admin password set (never while none is) */
  @state() private refused = false;
  @state() private route: Route = (fixHash(), parseHash());
  @state() private theme: Theme = currentTheme();
  @state() private summary: Loadable<Summary> = { state: 'loading' };
  @state() private offline = false;
  /** OPS-131: the service worker served this page from its cache while the engine was away */
  @state() private restarting: { status: number; at: number } | null = null;
  private stopRestart: (() => void) | null = null;
  @state() private version: string | null = null;
  @state() private codeHash: string | null = null;
  /** the engine's parallel sequences (/v1/config engine.parallel.n), read once for the subtitle */
  @state() private engineParallel: number | null = null;
  /** OPS-93: the box runs in setup mode (only the dashboard and Wren); every other view is off */
  @state() private setupMode = false;
  /** /health has answered once (or failed): until then no view mounts, so a box in setup mode
   *  gets no request a view would make */
  @state() private booted = false;
  private parallelAsked = false;
  /** a new build's worker waits: the banner's Reload applies it */
  @state() private newBuild: (() => void) | null = null;
  /** VIS-211: a setup job runs (its page says so): the engine restarts under it, so a waiting new
   *  build waits for a safe moment and no banner shows */
  @state() private setupJob = false;
  /** the phone's More sheet (the grouped menu) is open */
  @state() private more = false;
  /** a text field has focus: on the phone the bottom bar steps aside and the view takes its room */
  @state() private typing = false;
  @state() private wclass: WidthClass = currentWidthClass();
  /** the page's layout or its windows changed (lib/workspace.ts) */
  @state() private wsTick = 0;
  /** a tablet's or a phone's open drawer */
  @state() private drawer: 'left' | 'right' | null = null;
  @state() private fleet: FleetState | null = null;
  /** the context sidebar's content changed (a registration, a selection) */
  @state() private ctxTick = 0;
  /** a new page: its blocks rise in, once */
  @state() private entering = true;
  private enterTimer: ReturnType<typeof setTimeout> | null = null;
  private summaryPoll: Poller | null = null;
  private setupPoll: Poller | null = null;
  private unsub: (() => void)[] = [];
  private tz = browserTz();
  private typingTimer: ReturnType<typeof setTimeout> | null = null;
  /** a More item was tapped: the sheet's history entry goes first, the route follows on popstate */
  private pendingNav: string | null = null;
  /** the button that opened a drawer: focus goes back to it when the drawer closes */
  private drawerFrom: HTMLElement | null = null;
  /** a sidebar's resize in progress */
  private drag: { which: 'rail' | 'ctx'; x: number; w: number; el: HTMLElement } | null = null;
  /** the sidebars as shown at the last draw: a change is told to the views that show it */
  private sides = '';
  /** the phone's tray has a history entry: the back gesture closes it, as it closed Chat's panes */
  private trayEntry = false;
  private indTop: number | null = null;
  /** a selection was showing at the last change (a new one opens the sidebar) */
  private hadSelection = false;
  private fleetOff: (() => void) | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    watchSystemTheme();
    registerWorker((apply) => (this.newBuild = apply));
    if (navigator.serviceWorker?.controller) void this.checkRestart();
    setPage(pageKey(this.route.view, this.route.path));
    showPage(sectionOf(this.route.view), this.wclass, window.innerWidth);
    this.enter();
    this.unsub.push(
      onRoute((r) => {
        const page = r.view !== this.route.view || r.path.join('/') !== this.route.path.join('/');
        this.route = r;
        if (this.setupMode && r.view !== 'setup') navigate('setup');
        if (this.more && !this.pendingNav) this.closeMore();
        setPage(pageKey(r.view, r.path));
        showPage(sectionOf(r.view), this.wclass, window.innerWidth);
        if (page) {
          this.enter();
          if (this.drawer) this.closeDrawer(false);
        }
      }),
      onContext(() => {
        const sel = currentSelection();
        // a selection opens the sidebar it shows in
        if (sel && !this.hadSelection) {
          if (this.wclass === 'desk') change((l) => setSide(l, 'ctx', true));
          else this.drawer = 'right';
        }
        this.hadSelection = !!sel;
        this.ctxTick++;
      }),
    );
    setDefaultContext(() =>
      pageContext(sectionOf(this.route.view), {
        route: this.route,
        fleet: this.fleet,
        summary: this.summary.state === 'ready' ? this.summary.data : null,
        meta: { version: this.version, code: this.codeHash },
        layouts: savedLayouts(),
        theme: this.theme,
        toggleTheme: () => (this.theme = toggleTheme()),
      }),
    );
    this.unsub.push(
      onWorkspace(() => {
        this.wsTick++;
        const open = trayOpen();
        if (open && !this.trayEntry) {
          history.pushState({ tray: true }, '');
          this.trayEntry = true;
        } else if (!open && this.trayEntry) {
          this.trayEntry = false;
          if (history.state?.tray) history.back();
        }
      }),
    );
    setSidebarControl({
      toggle: (which) => this.toggleSide(which === 'rail' ? 'rail' : 'ctx', null),
      shown: (which) => (this.wclass === 'desk' ? currentLayout()[which].open : this.drawer === (which === 'rail' ? 'left' : 'right')),
    });
    // a change still waiting is saved before the tab goes
    const onHide = () => document.visibilityState === 'hidden' && flush();
    document.addEventListener('visibilitychange', onHide);
    window.addEventListener('pagehide', flush);
    this.unsub.push(() => document.removeEventListener('visibilitychange', onHide), () => window.removeEventListener('pagehide', flush), () => setSidebarControl(null));
    const onSetupJob = (e: Event) => {
      const active = !!(e as CustomEvent<{ active: boolean }>).detail?.active;
      // a job that ended: the box may have left setup mode, so the guide no longer holds the page
      if (this.setupJob && !active && this.setupMode) void this.checkSetupMode();
      this.setupJob = active;
    };
    // VIS-211: a finished install's way on (Start chatting, the guide): a waiting build loads now
    const onSafe = () => this.newBuild?.();
    window.addEventListener('qse-setup-job', onSetupJob);
    window.addEventListener('qse-safe-moment', onSafe);
    this.unsub.push(() => window.removeEventListener('qse-setup-job', onSetupJob));
    this.unsub.push(() => window.removeEventListener('qse-safe-moment', onSafe));
    document.addEventListener('focusin', this.onFocusChange);
    document.addEventListener('focusout', this.onFocusChange);
    window.addEventListener('popstate', this.onPop);
    window.addEventListener('keydown', this.onKeyDown);
    window.addEventListener('resize', this.onResize);
    window.addEventListener('pointermove', this.onDragMove);
    window.addEventListener('pointerup', this.onDragEnd);
    this.unsub.push(() => document.removeEventListener('focusin', this.onFocusChange));
    this.unsub.push(() => document.removeEventListener('focusout', this.onFocusChange));
    this.unsub.push(() => window.removeEventListener('popstate', this.onPop));
    this.unsub.push(() => window.removeEventListener('keydown', this.onKeyDown));
    this.unsub.push(() => window.removeEventListener('resize', this.onResize));
    this.unsub.push(() => window.removeEventListener('pointermove', this.onDragMove));
    this.unsub.push(() => window.removeEventListener('pointerup', this.onDragEnd));
    this.unsub.push(
      onApi((ev) => {
        if (ev === 'unauthorized') this.refused = true;
        else if (ev === 'offline') {
          this.offline = true;
          if (knownJob() && this.route.view !== 'setup') navigate('setup');
        }
        else if (ev === 'online') this.offline = false;
      }),
    );
    const onTheme = (e: Event) => (this.theme = (e as CustomEvent<Theme>).detail);
    const onUnauth = () => (this.refused = true);
    const onSignedIn = () => {
      this.refused = false;
      if (!this.setupMode) {
        void this.loadSummary();
        void this.loadVersion();
      }
    };
    // a setup job ends: the box may have left setup mode (the job restarted it serving)
    const onJobFinal = () => void this.checkSetupMode();
    window.addEventListener('qse-theme', onTheme);
    window.addEventListener('qse-unauthorized', onUnauth);
    window.addEventListener('qse-signed-in', onSignedIn);
    window.addEventListener('job-final', onJobFinal);
    this.unsub.push(() => window.removeEventListener('qse-theme', onTheme));
    this.unsub.push(() => window.removeEventListener('qse-unauthorized', onUnauth));
    this.unsub.push(() => window.removeEventListener('qse-signed-in', onSignedIn));
    this.unsub.push(() => window.removeEventListener('job-final', onJobFinal));
    void this.checkSetupMode().then(() => {
      this.booted = true;
      if (!this.setupMode) this.start();
    });
  }
  disconnectedCallback(): void {
    for (const u of this.unsub) u();
    this.fleetOff?.();
    this.summaryPoll?.stop();
    this.setupPoll?.stop();
    if (this.typingTimer) clearTimeout(this.typingTimer);
    if (this.enterTimer) clearTimeout(this.enterTimer);
    super.disconnectedCallback();
  }
  updated(): void {
    // a view with its own sidebar toggles (Chat's header) hears the sidebars open and close
    const l = currentLayout();
    const sides = `${this.wclass}|${this.drawer}|${l.rail.open}|${l.ctx.open}`;
    if (sides !== this.sides) {
      this.sides = sides;
      sidebarsChanged();
    }
    // the strip's indicator slides from the item it stood on to the one now on
    const on = this.querySelector<HTMLElement>('.istrip a.is-on');
    const ind = this.querySelector<HTMLElement>('.istrip .ind');
    if (!ind) return;
    ind.hidden = !on;
    if (!on || on.offsetTop === this.indTop) return;
    ind.style.transform = `translateY(${on.offsetTop}px)`;
    this.indTop = on.offsetTop;
  }

  // ---- layout: the sidebars, the drawers, the width class -------------------------------------

  private onResize = () => {
    const w = currentWidthClass();
    if (w === this.wclass) return;
    this.wclass = w;
    this.dropDrawer();
    if (w !== 'phone') setTray(false);
    showPage(sectionOf(this.route.view), w, window.innerWidth);
  };
  /** a page's first paint: its blocks rise in, never again on a live re-render */
  private enter(): void {
    this.entering = true;
    if (this.enterTimer) clearTimeout(this.enterTimer);
    this.enterTimer = setTimeout(() => (this.entering = false), ENTER_MS);
  }
  private toggleSide(which: 'rail' | 'ctx', e: Event | null): void {
    if (this.wclass === 'desk') {
      change((l) => setSide(l, which, !l[which].open));
      return;
    }
    const d = which === 'rail' ? 'left' : 'right';
    if (this.drawer === d) this.closeDrawer();
    else this.openDrawer(d, (e?.currentTarget as HTMLElement | undefined) ?? (document.activeElement as HTMLElement | null));
  }
  private openDrawer(d: 'left' | 'right', from: HTMLElement | null): void {
    if (this.more) this.closeMore();
    this.dropTray();
    // a drawer has a history entry: the back gesture closes it (Chat's list and Context did)
    if (!this.drawer) history.pushState({ drawer: d }, '');
    else history.replaceState({ drawer: d }, '');
    this.drawer = d;
    this.drawerFrom = from;
    this.updateComplete.then(() => this.querySelector<HTMLElement>(d === 'left' ? '.lnav .is-on, .lnav a' : '.ctx-in button, .ctx-in a')?.focus());
  }
  private closeDrawer(refocus = true): void {
    if (this.drawer && history.state?.drawer) history.back();
    this.drawer = null;
    if (refocus) this.drawerFrom?.focus();
    this.drawerFrom = null;
  }
  private onDragStart(which: 'rail' | 'ctx', e: PointerEvent): void {
    if (e.button) return;
    e.preventDefault();
    const el = e.currentTarget as HTMLElement;
    el.classList.add('is-drag');
    this.drag = { which, x: e.clientX, w: currentLayout()[which].w, el };
  }
  private onDragMove = (e: PointerEvent) => {
    const d = this.drag;
    if (!d) return;
    const dx = e.clientX - d.x;
    change((l) => resizeSide(l, d.which, d.which === 'rail' ? d.w + dx : d.w - dx));
  };
  private onDragEnd = () => {
    if (!this.drag) return;
    this.drag.el.classList.remove('is-drag');
    this.drag = null;
  };
  private onSepKey(which: 'rail' | 'ctx', e: KeyboardEvent): void {
    const d = e.key === 'ArrowLeft' ? -16 : e.key === 'ArrowRight' ? 16 : 0;
    if (!d) return;
    e.preventDefault();
    change((l) => resizeSide(l, which, l[which].w + (which === 'rail' ? d : -d)));
  }
  /** the top bar's and the phone bar's window buttons: their menu, or the tray */
  private winMenu(kind: 'add' | 'layout', e: Event): void {
    const m = windowMenu();
    if (m?.kind === kind) return openWindowMenu(null);
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    openWindowMenu({ kind, at: { left: r.left, top: r.top, bottom: r.bottom, right: r.right } });
  }
  /** a drawer gives way to the tray, the menu sheet or another width: its history entry stays, emptied */
  private dropDrawer(): void {
    if (!this.drawer) return;
    if (history.state?.drawer) history.replaceState(null, '');
    this.drawer = null;
    this.drawerFrom = null;
  }
  /** the tray gives way to a drawer or the menu sheet: its history entry stays, emptied */
  private dropTray(): void {
    if (!trayOpen()) return;
    if (this.trayEntry) {
      this.trayEntry = false;
      history.replaceState(null, '');
    }
    setTray(false);
  }
  private toggleTray(): void {
    if (trayOpen()) return setTray(false);
    if (this.more) this.closeMore();
    this.dropDrawer();
    setTray(true);
  }

  // ---- the phone's chrome -------------------------------------------------------------------

  /** focus moved: after a short settle (a tap from one field to the next passes through nothing),
   *  the bar hides while a text field has focus and comes back when none has */
  private onFocusChange = () => {
    if (this.typingTimer) clearTimeout(this.typingTimer);
    this.typingTimer = setTimeout(() => {
      this.typingTimer = null;
      this.typing = isTypingTarget(document.activeElement);
    }, TYPING_SETTLE_MS);
  };
  private onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== 'Escape') return;
    if (this.more) this.closeMore();
    else if (this.drawer) this.closeDrawer();
    else if (trayOpen() && !windowMenu()) setTray(false);
  };
  private onPop = () => {
    if (this.drawer && !history.state?.drawer) this.closeDrawer(false);
    if (this.trayEntry && !history.state?.tray) {
      this.trayEntry = false;
      setTray(false);
    }
    if (this.more) this.more = false;
    if (this.pendingNav) {
      const h = this.pendingNav;
      this.pendingNav = null;
      location.hash = h;
    }
  };
  private openMore(): void {
    if (this.more) return;
    this.dropDrawer();
    this.dropTray();
    history.pushState({ more: true }, '');
    this.more = true;
    this.updateComplete.then(() => this.querySelector<HTMLElement>('.more-item:not(.is-off)')?.focus());
  }
  /** closes the sheet; its history entry goes with it (the back gesture closes it the same way) */
  private closeMore(): void {
    if (!this.more) return;
    this.more = false;
    if (history.state?.more) history.back();
    this.querySelector<HTMLElement>('.bb-more')?.focus();
  }
  /** a page in the sheet: the sheet's entry is popped first, then the route changes, so Back from
   *  the new page returns to where the sheet was opened, not to the open sheet */
  private goMore(e: Event, href: string): void {
    e.preventDefault();
    if (history.state?.more) {
      this.pendingNav = href;
      this.more = false;
      history.back();
    } else {
      this.more = false;
      location.hash = href;
    }
  }

  /** once per page load: how many requests the engine runs at once (SRV-221). The engine's own
   *  /v1/dashboard/parallel answers on every server (proxy on or off); its `engine_slots` is
   *  absent without side slots, so an answer without it means one. A server without that route
   *  falls back to /v1/config's engine block (proxy on); with neither, no number is claimed. */
  private askParallel(): void {
    if (this.parallelAsked) return;
    this.parallelAsked = true;
    api
      .parallel()
      .then((p) => (this.engineParallel = p.engine_slots?.n ?? 1))
      .catch(() =>
        config
          .overview()
          .then((o) => (this.engineParallel = o.engine?.embedded && o.engine.parallel ? o.engine.parallel.n : null))
          .catch(() => {
            /* no number: the subtitle says none */
          }),
      );
  }

  private start(): void {
    this.summaryPoll?.stop();
    this.summaryPoll = poll(() => this.loadSummary(), 60_000);
    this.summaryPoll.start();
    void this.loadVersion();
    if (!this.fleetOff) this.fleetOff = onFleet((f) => (this.fleet = f));
  }
  /** OPS-131: a page the worker served from its cache (the proxy answered 502, 503 or 504) says the
   *  engine restarts and reloads once /health answers */
  private async checkRestart(): Promise<void> {
    const a = await swAway();
    if (!a) return;
    this.restarting = a;
    // VIS-209: a restart under a Setup Guide job this browser follows: the guide's job view has its
    // card (and reads the job's end by itself), so the page goes there instead of waiting blank
    if (knownJob() && this.route.view !== 'setup') navigate('setup');
    this.stopRestart = whenEngineUp(
      () => fetch(apiBase() + '/health', { cache: 'no-store', credentials: 'same-origin' }).then((r) => r.ok),
      () => location.reload(),
    );
    this.unsub.push(() => this.stopRestart?.());
  }

  /** /health says `mode: "setup"` on a box started by `ops/start.sh --setup`: open the guide, and
   *  ask again every 10 s until the box serves a model (an install ends with a restart) */
  private async checkSetupMode(): Promise<void> {
    let h: { mode?: string } | null;
    try {
      h = await fetch(apiBase() + '/health', { cache: 'no-store', credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : null));
    } catch {
      // unreachable (an install restarts the engine): setup mode stays as it was
      return;
    }
    rememberStatusPort(h as { status_port?: number } | null);
    const was = this.setupMode;
    this.setupMode = h?.mode === 'setup';
    if (this.setupMode) {
      if (this.route.view !== 'setup') navigate('setup');
      if (!this.setupPoll) {
        this.setupPoll = poll(() => this.checkSetupMode(), 10_000);
        this.setupPoll.start();
      }
    } else if (was) {
      this.setupPoll?.stop();
      this.setupPoll = null;
      this.start();
    }
  }

  private async loadSummary(): Promise<void> {
    try {
      const s = await api.summary(this.tz);
      this.summary = { state: 'ready', data: s };
    } catch (e) {
      const d = describeError(e);
      if (d.status !== 401) this.summary = { state: 'error', endpoint: d.endpoint, message: d.message };
    }
  }

  private async loadVersion(): Promise<void> {
    try {
      const s = await api.system();
      this.version = s.engine.version;
      this.codeHash = s.engine.code_sha256;
    } catch {
      /* the pill shows what it can */
    }
  }

  private status(): LiveStatus | 'setup' | 'restarting' {
    // VIS-209: an engine away under a Setup Guide job this browser follows is a restart, not offline
    if ((this.offline || this.restarting || this.summary.state === 'error') && knownJob()) return 'restarting';
    if (this.setupMode) return 'setup';
    if (this.offline) return 'offline';
    if (this.summary.state === 'ready') return this.summary.data.live.status;
    if (this.summary.state === 'error') return 'offline';
    return 'ok';
  }

  /** in setup mode only the guide opens: every other section is a link without a target */
  private off(id: Section): boolean {
    return this.setupMode && id !== 'models';
  }
  private href(id: Section): string | typeof nothing {
    if (this.off(id)) return nothing;
    return this.setupMode ? '#/models/serve' : sectionHref(id);
  }

  render() {
    const st = this.status();
    const live = this.summary.state === 'ready' ? this.summary.data.live : null;
    const section = sectionOf(this.route.view);
    const desk = this.wclass === 'desk';
    void this.wsTick;
    const layout = shownLayout();
    const railOpen = desk ? layout.rail.open : this.drawer === 'left';
    const ctxOpen = desk ? layout.ctx.open : this.drawer === 'right';
    const cls = [
      'shell',
      this.typing ? 'is-typing' : '',
      this.more ? 'is-more' : '',
      this.drawer ? `drw-${this.drawer}` : '',
      railOpen ? '' : 'rail-closed',
      ctxOpen ? '' : 'ctx-closed',
      trayOpen() && this.wclass === 'phone' ? 'is-tray' : '',
    ]
      .filter(Boolean)
      .join(' ');
    const meta = html`${this.version ? html`<span title="engine version">${this.version}</span>` : nothing}
      ${this.codeHash ? html`<span title="code hash">${shortHash(this.codeHash)}</span>` : nothing}
      ${live ? html`<span title="uptime">up ${duration(live.uptime_s)}</span>` : nothing}`;
    const c = crumb(this.route.view);
    return html`
      <div class=${cls} data-view=${this.route.view} data-w=${this.wclass} style="--lw:${layout.rail.w}px;--cw:${layout.ctx.w}px">
        <header class="topbar">
          <button type="button" class="icon-btn side-btn" data-side="left" aria-pressed=${railOpen} aria-label="Show or hide the menu" title="Menu" @click=${(e: Event) => this.toggleSide('rail', e)}>${icon(desk ? 'side-left' : 'menu')}</button>
          <a class="brand" href="#/overview" aria-label="TandemApp home"><span class="brand-mark" aria-hidden="true"><i></i><i></i></span><span class="brand-name">TandemApp</span></a>
          <span class="pill pill-${st}" data-status=${st} role="status" title=${st === 'setup' ? S.setupModeBanner : nothing}>${st === 'setup' ? S.setupModePill : st}</span>
          ${live?.conductor ? html`<a class="pill pill-monitor is-${live.conductor.mode}" href="#/conductor?screen=monitor" data-monitor-mode=${live.conductor.mode} title="the session monitor's mode on this server (--conductor)">${modeWord(live.conductor)}</a>` : nothing}
          <nav class="crumb" aria-label="You are here">${c.group ? html`<span>${c.group}</span><span class="crumb-sl" aria-hidden="true">/</span>` : nothing}<b>${c.label}</b></nav>
          <span class="top-sp"></span>
          ${this.readout()}
          <button type="button" class="btn-win" data-wm-menu aria-haspopup="menu" aria-expanded=${windowMenu()?.kind === 'add'} title="Add a window to this page" @click=${(e: Event) => this.winMenu('add', e)}>${icon('plus')}<span class="btn-win-label">Window</span></button>
          <button type="button" class="icon-btn btn-layout" data-wm-menu aria-haspopup="menu" aria-expanded=${windowMenu()?.kind === 'layout'} aria-label="Layout" title="This page’s layout" @click=${(e: Event) => this.winMenu('layout', e)}>${icon('layout')}</button>
          <button type="button" class="icon-btn side-btn" data-side="right" aria-pressed=${ctxOpen} aria-label="Show or hide the context sidebar" title="Context" @click=${(e: Event) => this.toggleSide('ctx', e)}>${icon('side-right')}</button>
          <button type="button" class="icon-btn btn-theme" aria-label=${this.theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'} title="Theme" @click=${() => (this.theme = toggleTheme())}>${icon(this.theme === 'dark' ? 'sun' : 'moon')}</button>
        </header>
        <div class="body">
          ${this.renderNav(section, meta)}
          <div class="center">
            ${MONITOR.includes(section) && this.fleet?.nodes.length && !this.setupMode
              ? html`<qse-scope-bar class="scopebar" .nodes=${this.fleet.nodes} .deployments=${this.fleet.deployments} ?phone=${this.wclass === 'phone'} ?usage=${section === 'usage'}></qse-scope-bar>`
              : nothing}
            <main class="content ${this.entering ? 'is-entering' : ''}" id="main">
              ${this.newBuild && !this.setupJob && !this.restarting && !this.offline ? html`<div class="banner" role="status" data-update>${icon('refresh')} The dashboard on the box was updated. Reload to use the new version. <button type="button" class="btn btn-sm" @click=${() => this.newBuild?.()}>Reload</button></div>` : nothing}
              ${this.restarting && !knownJob() ? html`<div class="banner banner-offline" role="status" data-engine-restarting>${icon('refresh')} ${restartText(this.restarting.status)}</div>` : nothing}
              ${this.offline && !this.restarting && !knownJob() ? html`<div class="banner banner-offline" role="alert" data-engine-away=${engineAway()?.state ?? 'unreachable'}>${icon('warn')} ${awayText(engineAway())} — panels keep their last data and say which endpoint failed.</div>` : nothing}
              ${this.refused && this.route.view !== 'setup' ? html`<div class="banner banner-offline" role="status" data-refused>${icon('warn')} The server answered 401: an admin password is set on it, and the dashboard has no sign-in yet. Unset QSE_ADMIN_TOKEN to open it again (docs/server.md).</div>` : nothing}
              ${this.setupMode ? html`<div class="banner setup-mode-banner" role="status" data-setup-mode>${icon('info')} ${S.setupModeBanner}</div>` : nothing}
              ${this.renderView()}
            </main>
            <qse-workspace .device=${this.wclass}></qse-workspace>
          </div>
          ${this.renderCtx(ctxOpen)}
          <div class="drw-scrim" @click=${() => this.closeDrawer()}></div>
        </div>
        ${this.renderBottomBar(section)}
        <div class="more-scrim" @click=${() => this.closeMore()}></div>
        <section class="more-sheet" role="dialog" aria-modal="true" aria-label="Menu" ?inert=${!this.more}>
          <header class="more-head">
            <h2>Menu</h2>
            <button type="button" class="btn btn-ghost btn-icon" aria-label="Close" @click=${() => this.closeMore()}>${icon('x')}</button>
          </header>
          ${MENU.map(
            (g) => html`${g.label ? html`<div class="eyebrow more-g">${g.label}</div>` : nothing}
              <nav class="more-grid" aria-label=${g.label ?? 'Chat'}>${g.items.map((it) => this.link(it, 'more-item', section))}</nav>`,
          )}
          <footer class="more-foot num">
            ${meta}
            <button type="button" class="btn btn-ghost btn-sm more-theme" aria-label=${this.theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'} @click=${() => (this.theme = toggleTheme())}>${icon(this.theme === 'dark' ? 'sun' : 'moon')} ${this.theme === 'dark' ? 'Light' : 'Dark'} theme</button>
          </footer>
        </section>
      </div>
    `;
  }

  /** the fleet readout: a swatch per Spark, the fleet's rate, the alerts; it opens Events */
  private readout(): TemplateResult | typeof nothing {
    if (!this.fleet?.nodes.length || this.setupMode) return nothing;
    const r = readout(this.fleet);
    return html`<a class="fleet-pill ${r.alerts ? 'has-alert' : ''}" href="#/events" aria-label=${r.label} data-fleet-readout>
      <span class="fdots" aria-hidden="true">${r.swatches.map((s) => html`<i class="fdot ${s.alert ? 'is-alert' : ''} ${s.drain ? 'is-drain' : ''} ${isHollow(s.colour) ? 'is-hollow' : ''}" style="--c:${nodeColour(s.colour)}" title=${s.title}></i>`)}</span>
      <span class="ftps" aria-hidden="true"><b class="num">${r.tps == null ? '—' : Math.round(r.tps)}</b><span class="ftps-u">tok/s</span></span>
      ${r.alerts ? html`<span class="warn-ink fl-alerts" aria-hidden="true">⚠ ${r.alerts}</span>` : html`<span class="fl-ok" aria-hidden="true">no alerts</span>`}
    </a>`;
  }

  /** a section's link: in the strip (icon), the panel (icon and label) or the phone's sheet */
  private link(it: MenuItem, cls: 'strip-i' | 'nav-i' | 'more-item' | 'bb-i', section: Section): TemplateResult {
    const off = this.off(it.id);
    const on = section === it.id;
    const alerts = it.id === 'events' && this.fleet ? readout(this.fleet).alerts : 0;
    const extra =
      cls === 'strip-i'
        ? html`${alerts ? html`<i class="strip-dot" aria-hidden="true"></i>` : nothing}${it.id === 'sparks' && this.fleet ? html`<i class="strip-nodes" aria-hidden="true">${this.fleet.nodes.map((n) => html`<b style="--c:${nodeColour(n.colour)}"></b>`)}</i>` : nothing}`
        : html`<span class="nav-label">${it.label}</span>${it.id === 'chat' ? BETA : nothing}${alerts && cls === 'nav-i' ? html`<span class="nav-badge warn-ink num">${alerts}</span>` : nothing}`;
    const href = this.href(it.id);
    return html`<a class="${cls} ${on ? 'is-on' : ''} ${off ? 'is-off' : ''}" href=${href} data-section=${it.id} aria-disabled=${off ? 'true' : nothing} title=${off ? S.setupModeTab : cls === 'strip-i' ? it.label : nothing} aria-label=${cls === 'strip-i' ? it.label : nothing} aria-current=${on ? 'page' : nothing} @click=${(e: Event) => cls === 'more-item' && !off && this.goMore(e, href as string)}>${icon(it.id)}${extra}</a>`;
  }

  private renderNav(section: Section, meta: TemplateResult): TemplateResult {
    const own = panelFor(this.route.view);
    const subs = subPages(this.route, this.fleet?.nodes);
    const leftInert = this.wclass === 'phone' && this.drawer !== 'left';
    const panelInert = this.wclass !== 'desk' ? this.drawer !== 'left' : !shownLayout().rail.open;
    return html`<nav class="lnav" aria-label="Main" ?inert=${leftInert}>
      <div class="istrip">
        <i class="ind" aria-hidden="true"></i>
        ${MENU.map((g, i) => html`${i ? html`<span class="gsep" aria-hidden="true"></span>` : nothing}${g.items.map((it) => this.link(it, 'strip-i', section))}`)}
      </div>
      <div class="lpanel" ?inert=${panelInert}>
        <div class="lpanel-in">
          <div class="lp-scroll">
            ${own ??
            MENU.map(
              (g) => html`${g.label ? html`<div class="nav-g eyebrow">${g.label}</div>` : nothing}
                ${g.items.map(
                  (it) => html`${this.link(it, 'nav-i', section)}${it.id === section && subs.length && !this.setupMode
                    ? html`<div class="nav-sub">${subs.map((s) => html`<a href=${s.href} class=${s.on ? 'is-on' : ''} aria-current=${s.on ? 'page' : nothing}>${s.label}</a>`)}</div>`
                    : nothing}`,
                )}`,
            )}
          </div>
          <div class="lp-foot num">${meta}</div>
        </div>
      </div>
      <div class="rz" role="separator" aria-orientation="vertical" aria-label="Resize the menu" aria-valuemin=${RAIL_W.min} aria-valuemax=${RAIL_W.max} aria-valuenow=${shownLayout().rail.w} tabindex="0" @pointerdown=${(e: PointerEvent) => this.onDragStart('rail', e)} @keydown=${(e: KeyboardEvent) => this.onSepKey('rail', e)}></div>
    </nav>`;
  }

  private renderCtx(open: boolean): TemplateResult {
    void this.ctxTick;
    const c = contextFor(this.route.view);
    const sel = currentSelection();
    const inert = this.wclass !== 'desk' && this.drawer !== 'right';
    return html`<aside class="ctx" aria-label="Context" ?inert=${inert}>
      <div class="rz" role="separator" aria-orientation="vertical" aria-label="Resize the context sidebar" aria-valuemin=${CTX_W.min} aria-valuemax=${CTX_W.max} aria-valuenow=${shownLayout().ctx.w} tabindex="0" @pointerdown=${(e: PointerEvent) => this.onDragStart('ctx', e)} @keydown=${(e: KeyboardEvent) => this.onSepKey('ctx', e)}></div>
      <div class="ctx-rail">
        <button type="button" class="icon-btn" aria-label="Open the context sidebar" @click=${(e: Event) => this.toggleSide('ctx', e)}>${icon('left')}</button>
        <button type="button" class="ctx-vt" aria-label=${`Open ${c.title}`} @click=${(e: Event) => this.toggleSide('ctx', e)}>${c.title}</button>
      </div>
      <div class="ctx-in" ?inert=${this.wclass === 'desk' && !open}>
        <div class="ctx-h">
          <span class="ctx-t">${c.title}</span>${c.tools ?? nothing}
          <button type="button" class="icon-btn" aria-label="Close the context sidebar" @click=${(e: Event) => this.toggleSide('ctx', e)}>${icon(this.wclass === 'desk' ? 'right' : 'x')}</button>
        </div>
        <div class="ctx-b">
          ${sel
            ? html`<section class="csel" data-selection>
                <div class="csel-h"><b>${sel.title}</b><button type="button" class="icon-btn" aria-label="Close the selection" @click=${() => clearSelection()}>${icon('x')}</button></div>
                ${sel.body}
              </section>`
            : nothing}
          ${c.body}
        </div>
      </div>
    </aside>`;
  }

  private renderBottomBar(section: Section): TemplateResult {
    const split = splitTabs(MENU_ITEMS, PHONE_BAR, section);
    const wins = shownLayout().windows.filter((w) => w.at !== 'min').length;
    return html`<nav class="bottombar" aria-label="Main">
      ${split.bar.map((it) => this.link(it, 'bb-i', section))}
      <button type="button" class="bb-i bb-win ${trayOpen() ? 'is-open' : ''}" aria-expanded=${trayOpen()} aria-label=${`Windows, ${wins} open`} @click=${() => this.toggleTray()}>${icon('window')}<span class="nav-label" aria-hidden="true">Windows</span>${wins ? html`<span class="bb-cnt num" aria-hidden="true">${wins}</span>` : nothing}</button>
      <button type="button" class="bb-i bb-more ${split.moreShows ? 'is-on' : ''} ${this.more ? 'is-open' : ''}" aria-haspopup="dialog" aria-expanded=${this.more} aria-label=${split.moreShows ? `Menu, on ${split.moreShows.label}` : 'Menu'} @click=${() => (this.more ? this.closeMore() : this.openMore())}>${icon(split.moreShows ? split.moreShows.id : 'more')}<span class="nav-label" aria-hidden="true">${split.moreShows ? split.moreShows.label : 'More'}</span></button>
    </nav>`;
  }

  private renderView(): TemplateResult {
    if (!this.booted || (this.setupMode && this.route.view !== 'setup')) return html``;
    switch (this.route.view) {
      case 'chat':
        void loadChat();
        return html`<qse-chat .params=${this.route.params} .path=${this.route.path}></qse-chat>`;
      case 'usage':
        return html`<qse-usage .summary=${this.summary} .params=${this.route.params}></qse-usage>`;
      case 'performance':
        if (this.route.params.get('tab') === 'requests') {
          void loadParallel();
          return html`${perfTabs('requests')}<qse-parallel></qse-parallel>`;
        }
        this.askParallel();
        return html`${perfTabs('live', this.engineParallel)}<qse-performance .params=${this.route.params}></qse-performance>`;
      case 'dev':
        return html`<qse-dev .params=${this.route.params}></qse-dev>`;
      case 'playground':
        void loadPlayground();
        return html`<qse-playground .params=${this.route.params}></qse-playground>`;
      case 'conductor':
        void loadConductor();
        return html`<qse-conductor .params=${this.route.params}></qse-conductor>`;
      case 'backends':
        void loadBackends();
        return html`<qse-backends .params=${this.route.params}></qse-backends>`;
      case 'system':
        return html`<qse-system></qse-system>`;
      case 'setup':
        void loadSetup();
        return html`<qse-setup .path=${this.route.path}></qse-setup>`;
      default:
        void loadFleetPages();
        return html`<qse-fleet-page .view=${this.route.view} .path=${this.route.path}></qse-fleet-page>`;
    }
  }
}
