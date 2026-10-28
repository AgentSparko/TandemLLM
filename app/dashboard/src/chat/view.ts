// <qse-chat>: the Chat tab. Picks the backend (the server's /v1/chat-app; the in-browser mock with
// a badge for ?chat=mock and the dev server; without the chat API the panel that names why, VIS-179), loads the capabilities and the conversation list, opens the chat in the route and
// tails its event stream (a catch-up from the last seen event id, then live), and lays out the
// thread with its composer. The shell holds the rest (VIS-256): the list of chats is the left panel
// (lib/context.ts registerPanel), the Context panel with its eight sections the right sidebar
// (registerContext), and the chat's panes are windows of the page (lib/workspace.ts registerWindows):
// docked below the thread or beside it, floating, minimised, or a tray of sheets on a phone. Which
// panes a chat has open is server state per chat (Conversation.ui); where they sit is the page's
// layout per device. Both sidebars and the panes keep elements this view owns and updates, so a
// terminal keeps its session as its window moves. A server that starts listening opens in a Preview
// pane (preview.ts rule 2) while the panes show.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, query, state } from 'lit/decorators.js';
import { LightElement } from '../ui/base';
import { icon } from '../ui/bits';
import { navigate } from '../lib/router';
import { poll, type Poller } from '../lib/poll';
import '../setup/figure';
import '../setup/signin';
import '../styles/setup.css';
import '../styles/chat.css';
import './rail';
import './thread';
import './composer';
import './context-panel';
import './panes';
import './settings';
import type { QseChatRail } from './rail';
import type { QseChatContext } from './context-panel';
import { registerContext, registerPanel, onSidebars, sidebarShown, toggleSidebar } from '../lib/context';
import { currentPage, registerWindows, setTray, showInTray, trayOpen, windowsChanged, type WindowSource } from '../lib/workspace';
import { CHAT_DEFAULTS } from '../lib/wm';
import type { WindowKind } from '../api/fleet';
import { paneIcon, paneWord } from './thread';
import { latestPage, filePane } from './file-panes';
import { PORT_POLL_MS, freshServers, newestServer, portPane } from './preview';
import { isChatDown, pickBackend, probeChat, VersionConflict, type ChatBackend, type ChatDown, type Subscription } from './backend';
import { downBanner, Reprobe } from './chat-down';
import { attachLabel, initialAttachment, onEvent as attachEvent, onStreamState, onTick, type Attachment as Attach } from './connection';
import { adopt, adoptTask, applyEvent, mergeTasks, messageText } from './store';
import { agentsDone } from './tasks';
import { badge, notify, notifyKind } from './notify';
import type { QseChatComposer } from './composer';
import { PLUS_KINDS, defaultPane, type OpenPane, type QseChatPane } from './panes';
import type { SettingsSection } from './settings';
import { SECTIONS } from './settings';
import { clientMsgId, localId, type Attachment, type Capabilities, type ChatSettings, type Conversation, type ConversationSummary, type ConversationUi, type Folder, type PaneKind, type PaneRef, type Preset, type PackageInfo, type Prompt, type SandboxFile, type StampedEvent, type ToolInfo, type WebSearchProvider } from './types';
import { RunToken } from '../lib/run-token';

const LS = {
  /** where the panes docked before they were windows: a new pane goes there */
  dock: 'qse.chat.dock',
  last: 'qse.chat.last',
  draft: (id: string) => `qse.chat.draft.${id}`,
};

function lsGet(k: string): string | null {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
}
function lsSet(k: string, v: string | null): void {
  try {
    if (v == null) localStorage.removeItem(k);
    else localStorage.setItem(k, v);
  } catch {
    /* private mode */
  }
}

const STARTERS = ['Fix the flaky test in tests/test_worker.py and run it 200 times', 'Make a one-page site for a bike repair shop with prices and hours', 'How does tokens per round set the decode speed? One code example.'];

@customElement('qse-chat')
export class QseChat extends LightElement {
  @property({ attribute: false }) params: URLSearchParams = new URLSearchParams();
  @property({ attribute: false }) path: string[] = [];

  @state() private backend: ChatBackend | null = null;
  @state() private backendReason: 'forced' | 'probed' | 'dev' | null = null;
  /** no chat API at boot (the panel), or a live tab that lost chatd (the banner) */
  @state() private down: ChatDown | null = null;
  @state() private lost = false;
  @state() private probeTick = 0;
  @state() private caps: Capabilities | null = null;
  @state() private needSignIn = false;
  @state() private list: ConversationSummary[] = [];
  @state() private folders: Folder[] = [];
  @state() private conv: Conversation | null = null;
  @state() private convError: string | null = null;
  @state() private loading = false;
  @state() private attach: Attach = initialAttachment();
  @state() private now = Date.now();
  @state() private tools: ToolInfo[] = [];
  @state() private packages: PackageInfo[] = [];
  @state() private resources: { server: string; serverName: string; uri: string; name: string }[] = [];
  @state() private prompts: Prompt[] = [];
  @state() private unread = new Set<string>();
  // chrome
  @state() private more = false;
  /** the shell's sidebars opened or closed: the header's toggles show it */
  @state() private sidesTick = 0;
  @state() private notice: { kind: 'ok' | 'warn'; text: string } | null = null;
  /** VIS-219: what the Agents switch did, under the composer until the next send */
  @state() private agentsNote: string | null = null;
  @query('qse-chat-composer') private composer!: QseChatComposer | null;

  /** the list of chats in the shell's left panel, the Context panel in its right sidebar, and each
   *  open pane's body: elements this view keeps and updates (the shell does not draw them again) */
  private railEl: QseChatRail | null = null;
  private railHost: HTMLElement | null = null;
  private ctxEl: QseChatContext | null = null;
  private ctxHost: HTMLElement | null = null;
  private paneEls = new Map<string, { host: HTMLElement; el: QseChatPane }>();
  private unhook: (() => void)[] = [];
  /** what the page's windows were at the last draw: a change is told to the window manager */
  private shownPanes = '';
  /** the ports that listened at the last look (null: not looked yet), for the chat `portConv` */
  private portSeen: Set<number> | null = null;
  private portConv: string | null = null;
  private portTimer: ReturnType<typeof setInterval> | null = null;

  private sub: Subscription | null = null;
  private tick: ReturnType<typeof setInterval> | null = null;
  private listPoll: Poller | null = null;
  private globalSub: Subscription | null = null;
  private listSoon: ReturnType<typeof setTimeout> | null = null;
  private noticeTimer: ReturnType<typeof setTimeout> | null = null;
  private resuming = false;
  private wantId: string | null = null;
  /** the last seq the conversation in hand includes: an event at or below it is a duplicate */
  private floor = 0;
  /** the events applied lately, for a snapshot that arrives after some of them (store.adopt) */
  private recent: StampedEvent[] = [];
  private reprobe: Reprobe | null = null;
  private downClock: ReturnType<typeof setInterval> | null = null;
  /** mount generations: a boot that finishes after a disconnect owns nothing (deep audit 0.11.3) */
  private boots = new RunToken();

  // ---- lifecycle ------------------------------------------------------------------------------

  connectedCallback(): void {
    super.connectedCallback();
    this.boots.start();
    this.makeSides();
    this.unhook.push(
      registerPanel('chat', () => this.railHost!),
      registerContext('chat', () => ({ title: 'Context', body: this.ctxHost! })),
      registerWindows('chat', this.source),
      onSidebars(() => this.sidesTick++),
    );
    this.portTimer = setInterval(() => void this.watchPorts(), PORT_POLL_MS);
    window.addEventListener('keydown', this.onKey);
    document.addEventListener('click', this.onDocClick);
    window.addEventListener('qse-signed-in', this.onSignedIn);
    void this.boot();
  }
  disconnectedCallback(): void {
    this.boots.stop();
    this.unhook.forEach((u) => u());
    this.unhook = [];
    if (this.portTimer) clearInterval(this.portTimer);
    this.portTimer = null;
    window.removeEventListener('keydown', this.onKey);
    document.removeEventListener('click', this.onDocClick);
    window.removeEventListener('qse-signed-in', this.onSignedIn);
    this.sub?.close();
    this.globalSub?.close();
    this.listPoll?.stop();
    if (this.tick) clearInterval(this.tick);
    this.stopReprobe();
    super.disconnectedCallback();
  }

  updated(changed: Map<string, unknown>): void {
    if ((changed.has('path') || changed.has('params')) && this.backend) void this.openFromRoute();
    if (changed.has('list') || changed.has('unread')) badge(this.list.filter((x) => x.status === 'waiting' || x.unread || this.unread.has(x.id)).length);
    this.updateSides();
  }

  // ---- the shell's sidebars and windows ---------------------------------------------------------

  /** the list and the Context panel, made once; the shell shows them, this view feeds them */
  private makeSides(): void {
    if (this.railHost) return;
    this.railHost = Object.assign(document.createElement('div'), { className: 'ch-side ch-side-rail' });
    this.railEl = document.createElement('qse-chat-rail') as QseChatRail;
    this.railHost.append(this.railEl);
    const on = <T>(el: HTMLElement, name: string, fn: (d: T) => void) => el.addEventListener(name, (e) => fn((e as CustomEvent<T>).detail));
    on<{ id: string }>(this.railEl, 'ch-open', (d) => this.goto(d.id));
    on(this.railEl, 'ch-new', () => void this.newChat());
    on<{ id: string; title: string }>(this.railEl, 'ch-rename', (d) => void this.rename(d.id, d.title));
    on<{ id: string; pinned: boolean }>(this.railEl, 'ch-pin', (d) => void this.pin(d.id, d.pinned));
    on<{ id: string; folder_id: string | null }>(this.railEl, 'ch-move', (d) => void this.move(d.id, d.folder_id));
    on<{ id: string }>(this.railEl, 'ch-delete', (d) => void this.removeChat(d.id));
    on<{ name: string }>(this.railEl, 'ch-folder-new', (d) => void this.guard(() => this.backend!.createFolder(d.name).then((f) => (this.folders = [...this.folders, f])), 'New folder'));
    on<{ section: SettingsSection }>(this.railEl, 'ch-open-settings', (d) => this.openSettings(d.section));
    on(this.railEl, 'ch-close-rail', () => this.closeDrawer());
    this.ctxHost = Object.assign(document.createElement('div'), { className: 'ch-side ch-side-ctx' });
    this.ctxEl = document.createElement('qse-chat-context') as QseChatContext;
    this.ctxHost.append(this.ctxEl);
    this.ctxEl.addEventListener('ch-compact', () => this.conv && void this.guard(() => this.backend!.compact(this.conv!.id), 'Compact'));
    on<{ section: SettingsSection }>(this.ctxEl, 'ch-open-settings', (d) => this.openSettings(d.section));
  }

  /** the shell's sidebars and the panes' bodies follow this view's state */
  private updateSides(): void {
    const c = this.conv;
    if (this.railEl) Object.assign(this.railEl, { list: this.list, folders: this.folders, active: c?.id ?? null, mock: this.backend?.kind === 'mock' ? this.mockNote : '', unread: this.unread });
    if (this.ctxEl) Object.assign(this.ctxEl, { conv: c, attach: this.attach, now: this.now });
    const sheet = currentPage().device === 'phone';
    const panes = this.openPanes;
    for (const [id, p] of this.paneEls) {
      const pane = panes.find((x) => x.id === id);
      if (!pane) {
        this.paneEls.delete(id);
        continue;
      }
      Object.assign(p.el, { pane: { ...pane, inline: this.inline.get(pane.id) ?? null }, backend: this.backend, convId: c?.id ?? null, conv: c, sheet });
    }
    const shown = JSON.stringify([c?.id, this.section, c?.ui.active, panes.map((p) => [p.id, p.kind, p.title])]);
    if (shown !== this.shownPanes) {
      this.shownPanes = shown;
      windowsChanged();
    }
  }

  /** a phone's drawer closes after a pick in it */
  private closeDrawer(): void {
    if (currentPage().device !== 'desk' && sidebarShown('rail')) toggleSidebar('rail');
  }

  /** The chat's panes as the page's windows: shown while the chat shows them (ui.active), each one's
   *  body an element kept per pane. */
  private source: WindowSource = {
    windows: () => {
      const c = this.conv;
      if (!c || this.section || !c.ui.active) return [];
      return this.openPanes.map((p) => ({ id: `chat:${p.id}`, kind: p.kind as WindowKind, title: p.title ?? paneWord(p.kind), icon: paneIcon(p.kind) }));
    },
    body: (id, sheet) => {
      const pid = id.replace(/^chat:/, '');
      let p = this.paneEls.get(pid);
      if (!p) {
        const host = Object.assign(document.createElement('div'), { className: 'ch-side ch-pane-host' });
        const el = document.createElement('qse-chat-pane') as QseChatPane;
        el.addEventListener('ch-pane-open', (e) => void this.openPane((e as CustomEvent<{ pane: PaneRef }>).detail.pane));
        host.append(el);
        p = { host, el };
        this.paneEls.set(pid, p);
      }
      const pane = this.openPanes.find((x) => x.id === pid);
      Object.assign(p.el, { pane: pane ? { ...pane, inline: this.inline.get(pid) ?? null } : null, backend: this.backend, convId: this.conv?.id ?? null, conv: this.conv, sheet });
      return p.host;
    },
    dock: () => (lsGet(LS.dock) === 'right' ? 'right' : 'bottom'),
    activate: (id) => this.conv && void this.saveUi({ ...this.conv.ui, active: id.replace(/^chat:/, '') }),
    close: (id) => void this.closePane(id.replace(/^chat:/, '')),
    hide: () => this.conv && void this.saveUi({ ...this.conv.ui, active: null }),
    kinds: PLUS_KINDS as WindowKind[],
    open: (kind) => void this.pick(kind as PaneKind),
    reset: () => void this.openDefaults(true),
    active: () => (this.conv?.ui.active ? `chat:${this.conv.ui.active}` : null),
  };

  /** the list-level stream: a run ended or asks somewhere; notify this device, refresh the list */
  private async onGlobal(ev: { type: string; conversation_id?: string; status?: string }): Promise<void> {
    const id = ev.conversation_id;
    const kind = notifyKind(ev);
    if (id && kind) {
      if (!this.list.some((x) => x.id === id)) await this.loadList();
      void notify(kind, { id, title: this.list.find((x) => x.id === id)?.title ?? 'A chat' }, id === this.conv?.id);
    }
    if (!this.listSoon && (kind || ev.type.startsWith('conversation.')))
      this.listSoon = setTimeout(() => {
        this.listSoon = null;
        void this.loadList();
      }, 400);
  }

  private onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && this.more) this.more = false;
  };
  private onDocClick = (e: Event) => {
    if (this.more && !(e.target as HTMLElement).closest('.ch-more')) this.more = false;
  };

  /** the route's query (#/chat?chat=mock) and the page's (?chat=mock#/chat) both count */
  private get query(): URLSearchParams {
    const q = new URLSearchParams(location.search);
    for (const [k, v] of this.params) q.set(k, v);
    return q;
  }

  private async boot(): Promise<void> {
    const run = this.boots.current();
    const q = this.query;
    const scenario = q.get('scenario');
    const choice = await pickBackend(q, {
      mock: async () => new (await import('./mock')).MockChatBackend({ scenario }),
      remote: async () => new (await import('./remote')).RemoteChatBackend(),
    });
    if (!this.boots.live(run)) return;   // left while picking a backend: nothing below may own a resource
    if (!choice.backend) {
      this.down = choice.down;
      this.startReprobe(async () => {
        const d = await probeChat();
        if (!this.boots.live(run)) return true;   // detached mid-probe: stop, revive nothing
        if (d) {
          this.down = d;
          return false;
        }
        this.down = null;
        this.backend = new (await import('./remote')).RemoteChatBackend();
        this.backendReason = 'probed';
        await this.loadCaps(run);
        if (!this.boots.live(run)) return true;
        if (!this.needSignIn) await this.afterSignIn(run);
        return true;
      }, true);
      return;
    }
    this.backend = choice.backend;
    this.backendReason = choice.reason;
    await this.loadCaps(run);
    if (!this.boots.live(run)) return;
    if (this.needSignIn) return;
    await this.afterSignIn(run);
  }

  private async loadCaps(run: number): Promise<void> {
    try {
      this.caps = await this.backend!.capabilities();
      this.needSignIn = !!this.caps.session && this.caps.session.password_set && !this.caps.session.signed_in;
    } catch (e) {
      if (!this.boots.live(run)) return;   // left mid-call: no reprobe, no toast
      if (isChatDown(e)) this.onLost();
      else this.say('warn', `The chat server did not answer: ${(e as Error).message}`);
    }
  }

  /** run: the caller's generation, taken before its first await (a late caller's own read of
   *  current() would be the detach's new value and pass) */
  private async afterSignIn(run: number): Promise<void> {
    if (!this.boots.live(run)) return;
    const b = this.backend!;
    void b.tools().then((t) => (this.tools = t)).catch(() => undefined);
    void b.packages().then((p) => (this.packages = p)).catch(() => undefined);
    void b.mcpServers().then((l) => (this.resources = l.flatMap((m) => (m.resources ?? []).map((r) => ({ server: m.id, serverName: m.name, uri: r.uri, name: r.name }))))).catch(() => undefined);
    void b.prompts().then((p) => (this.prompts = p)).catch(() => undefined);
    this.listPoll = poll(() => this.loadList(), 10_000);
    this.listPoll.start();
    this.globalSub?.close();
    this.globalSub = b.subscribeGlobal((ev) => void this.onGlobal(ev));
    await this.loadList();
    if (!this.boots.live(run)) return;   // left while the list loaded: open no chat stream
    await this.openFromRoute();
  }

  private async loadList(): Promise<void> {
    try {
      const r = await this.backend!.listConversations();
      this.list = r.conversations;
      this.folders = r.folders;
    } catch (e) {
      if (isChatDown(e)) this.onLost();
      else if (!this.lost) this.say('warn', `Could not load the chats: ${(e as Error).message}`);
    }
  }

  // ---- no chat API (VIS-179) --------------------------------------------------------------------

  private startReprobe(check: () => Promise<boolean>, clock = false): void {
    this.stopReprobe();
    this.reprobe = new Reprobe(check, () => (this.probeTick += 1));
    this.reprobe.start();
    // the panel's "next in N s"
    if (clock) this.downClock = setInterval(() => (this.now = Date.now()), 1000);
  }

  private stopReprobe(): void {
    this.reprobe?.stop();
    this.reprobe = null;
    if (this.downClock) clearInterval(this.downClock);
    this.downClock = null;
  }

  /** a live tab's call answered 502 chat_unavailable: one banner, probes until chatd is back */
  private onLost(): void {
    if (this.lost) return;
    this.lost = true;
    this.startReprobe(async () => {
      if (await probeChat()) return false;
      this.lost = false;
      this.stopReprobe();
      await this.loadList();
      return true;
    });
  }

  // ---- route ------------------------------------------------------------------------------------

  private get section(): SettingsSection | null {
    if (this.path[0] !== 'settings') return null;
    const s = this.path[1] as SettingsSection | undefined;
    return SECTIONS.some((x) => x.id === s) ? s! : 'tools';
  }

  private async openFromRoute(): Promise<void> {
    if (this.section) return;
    const id = this.path[0] ?? lsGet(LS.last) ?? null;
    if (!id) {
      this.detach();
      this.conv = null;
      return;
    }
    if (this.conv?.id === id) return;
    await this.open(id);
  }

  private async open(id: string): Promise<void> {
    const run = this.boots.current();
    this.detach();
    this.wantId = id;
    this.loading = true;
    this.convError = null;
    try {
      const c = await this.backend!.getConversation(id);
      if (!this.boots.live(run) || this.wantId !== id) return;   // left mid-load: no navigate, no stream
      this.conv = c;
      this.agentsNote = null;
      lsSet(LS.last, id);
      if (this.path[0] !== id && !this.section) navigate('chat' as never, this.params);
      this.unread.delete(id);
      this.attachTo(c);
      void this.loadTasks(id);
    } catch (e) {
      if (this.wantId !== id) return;
      this.conv = null;
      this.convError = (e as Error).message;
      lsSet(LS.last, null);
    } finally {
      if (this.wantId === id) this.loading = false;
    }
  }

  // ---- the stream -----------------------------------------------------------------------------

  private attachTo(c: Conversation): void {
    // the snapshot holds every event up to its seq, so the stream starts there; an older id would
    // replay events the snapshot already has and double their text. A dropped stream resumes
    // with Last-Event-ID (lib/sse.ts) and catches up from where it was.
    this.floor = Number(c.seq) || 0;
    this.recent = [];
    this.resuming = false;
    this.attach = initialAttachment();
    this.sub = this.backend!.subscribe(c.id, c.seq, {
      onEvent: (ev: StampedEvent, replay) => this.onStreamEvent(c.id, ev, replay),
      onCurrent: () => {
        this.attach = attachEvent(this.attach, true);
        this.resuming = false;
      },
      onState: (s, detail) => {
        if (detail === 'reset') {
          void this.open(c.id);
          return;
        }
        this.attach = onStreamState(this.attach, s, this.resuming || s === 'open' ? !!this.sub && this.attach.state !== 'connecting' : false);
      },
    });
    this.startTick();
  }

  private detach(): void {
    this.sub?.close();
    this.sub = null;
    this.attach = initialAttachment();
    if (this.tick) clearInterval(this.tick);
    this.tick = null;
  }

  private startTick(): void {
    if (this.tick) return;
    this.tick = setInterval(() => {
      this.now = Date.now();
      this.attach = onTick(this.attach, this.now);
    }, 1000);
  }

  private onStreamEvent(id: string, ev: StampedEvent, replay: boolean): void {
    if (!this.conv || this.conv.id !== id) return;
    if (ev.id && Number(ev.id) <= this.floor) return;
    if (ev.id) {
      this.floor = Number(ev.id);
      this.recent.push(ev);
      if (this.recent.length > 2_000) this.recent.splice(0, this.recent.length - 2_000);
    }
    const e = ev.event;
    this.attach = attachEvent(this.attach, !replay);
    if (e.type === 'branch.changed' && !replay && e.head_id !== this.conv.head_id) {
      // this device or another switched or made a branch: re-read the conversation
      void this.backend!.getConversation(id).then((c) => this.adoptSnapshot(c));
      return;
    }
    if (e.type === 'pane.open' && !replay) {
      // a preview the model presents again resolves and loads anew: `present` waits for its report
      void this.openPane(e.pane).then(() => {
        if (e.pane.kind === 'preview') for (const p of this.paneEls.values()) if (p.el.pane?.ref === e.pane.ref) p.el.refresh(e.pane.ref);
      });
      return;
    }
    const next = applyEvent(this.conv, e);
    this.conv = next;
    if (e.type === 'run.done' || e.type === 'run.started' || e.type === 'run.phase' || e.type === 'conversation.updated' || e.type === 'tool.asking') this.patchList(next);
  }

  /** a fresh snapshot of the open chat, with the events that came while it was on its way; the
   *  tasks are not in a snapshot and stay */
  private adoptSnapshot(c: Conversation): void {
    if (this.conv?.id !== c.id) return;
    const { conv, floor } = adopt({ ...c, tasks: c.tasks ?? this.conv.tasks, last_worker: this.conv.last_worker }, this.recent);
    this.conv = conv;
    this.floor = floor;
  }

  /** VIS-217: the chat's tasks after a load (a reload, another device), then the transcript of a
   *  running one, so its card follows the worker from its next event on */
  private async loadTasks(id: string): Promise<void> {
    const list = await this.backend!.tasks(id).catch(() => null);
    if (!list || this.conv?.id !== id) return;
    this.conv = { ...this.conv, tasks: mergeTasks(this.conv.tasks, list) };
    for (const t of this.conv.tasks ?? []) if (t.status === 'running' && !t.messages) void this.loadTask(id, t.id);
  }

  private async loadTask(id: string, taskId: string): Promise<void> {
    const full = await this.guard(() => this.backend!.task(id, taskId), 'Task');
    if (full && this.conv?.id === id) this.conv = adoptTask(this.conv, full, this.recent);
  }

  private patchList(c: Conversation): void {
    const i = this.list.findIndex((x) => x.id === c.id);
    const sum: ConversationSummary = { id: c.id, title: c.title, created_at: c.created_at, updated_at: c.updated_at, pinned: c.pinned, archived: c.archived, folder_id: c.folder_id, status: c.status, preview: c.preview, model: c.model, profile: c.profile, turns: c.turns, now: c.now, version: c.version, unread: false };
    this.list = i < 0 ? [sum, ...this.list] : this.list.map((x, k) => (k === i ? sum : x));
  }

  // ---- notices --------------------------------------------------------------------------------

  private say(kind: 'ok' | 'warn', text: string): void {
    this.notice = { kind, text };
    if (this.noticeTimer) clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => (this.notice = null), kind === 'ok' ? 3500 : 8000);
  }

  private async guard<T>(f: () => Promise<T>, what: string): Promise<T | undefined> {
    try {
      return await f();
    } catch (e) {
      this.say('warn', `${what} failed: ${(e as Error).message}`);
      return undefined;
    }
  }

  // ---- conversations --------------------------------------------------------------------------

  private goto(id: string | null): void {
    const hash = id ? `#/chat/${encodeURIComponent(id)}` : '#/chat';
    if (location.hash !== hash) location.hash = hash;
    else void this.openFromRoute();
    this.closeDrawer();
  }

  private async newChat(settings?: Partial<ChatSettings>): Promise<Conversation | undefined> {
    const c = await this.guard(() => this.backend!.createConversation(settings), 'New chat');
    if (!c) return undefined;
    this.list = [{ ...c }, ...this.list];
    this.conv = c;
    this.detach();
    this.attachTo(c);
    lsSet(LS.last, c.id);
    location.hash = `#/chat/${encodeURIComponent(c.id)}`;
    this.closeDrawer();
    this.updateComplete.then(() => this.composer?.focusArea());
    return c;
  }

  /** PATCH with the version; a 409 takes the server's summary and tries once more */
  private async patchConv(id: string, p: { title?: string; pinned?: boolean; archived?: boolean; folder_id?: string | null; ui?: ConversationUi }): Promise<ConversationSummary | undefined> {
    const b = this.backend!;
    const version = this.conv?.id === id ? this.conv.version : this.list.find((x) => x.id === id)?.version;
    try {
      return await b.updateConversation(id, { version, ...p });
    } catch (e) {
      if (e instanceof VersionConflict) {
        this.applySummary(e.current);
        try {
          return await b.updateConversation(id, { version: e.current.version, ...p });
        } catch (e2) {
          this.say('warn', `Could not save: ${(e2 as Error).message}`);
          return undefined;
        }
      }
      this.say('warn', `Could not save: ${(e as Error).message}`);
      return undefined;
    }
  }

  private applySummary(s: ConversationSummary): void {
    this.list = this.list.map((x) => (x.id === s.id ? { ...x, ...s } : x));
    if (this.conv?.id === s.id) this.conv = { ...this.conv, ...s };
  }

  private async rename(id: string, title: string): Promise<void> {
    const s = await this.patchConv(id, { title });
    if (s) this.applySummary(s);
  }
  private async pin(id: string, pinned: boolean): Promise<void> {
    const s = await this.patchConv(id, { pinned });
    if (s) this.applySummary(s);
  }
  private async move(id: string, folder_id: string | null): Promise<void> {
    const s = await this.patchConv(id, { folder_id });
    if (s) this.applySummary(s);
  }
  private async removeChat(id: string): Promise<void> {
    const ok = await this.guard(() => this.backend!.deleteConversation(id).then(() => true), 'Delete');
    if (!ok) return;
    this.list = this.list.filter((x) => x.id !== id);
    if (this.conv?.id === id) {
      this.detach();
      this.conv = null;
      lsSet(LS.last, null);
      location.hash = '#/chat';
    }
    this.say('ok', 'Deleted. It stays in the trash for 7 days.');
  }

  // ---- the turn -------------------------------------------------------------------------------

  private async send(text: string, attachments: Attachment[]): Promise<void> {
    let c = this.conv;
    if (!c) c = (await this.newChat()) ?? null;
    if (!c) return;
    const id = c.id;
    lsSet(LS.draft(id), null);
    this.agentsNote = null;
    let atts = attachments;
    if (this.backend!.kind === 'remote' && attachments.length) {
      // the remote wants uploads first (CONTRACT section 8, request 9); a file that fails keeps
      // the message in the composer. The thumbnail stays the device's own until `url` answers.
      atts = [];
      for (const a of attachments) {
        try {
          const f = this.composer?.fileOf(a.id);
          if (!f && a.url) {
            atts.push(a);                       // the server has it already (an MCP resource)
            continue;
          }
          if (!f) throw new Error('the file is no longer there');
          const up = await this.backend!.uploadAttachment(f);
          atts.push({ ...up, preview: a.preview ?? null });
        } catch (e) {
          this.composer?.restore(text, attachments);
          this.say('warn', `${a.name} did not upload (${(e as Error).message}); the message stays in the composer.`);
          return;
        }
      }
    }
    if (c.run) {
      await this.guard(() => this.backend!.queue(id, text, clientMsgId()), 'Queue');
      return;
    }
    await this.guard(() => this.backend!.send(id, { client_msg_id: clientMsgId(), text, attachments: atts }), 'Send');
  }

  private async command(command: string, args: string): Promise<void> {
    switch (command) {
      case 'clear':
        await this.newChat();
        return;
      case 'compact':
        if (this.conv) await this.guard(() => this.backend!.compact(this.conv!.id), 'Compact');
        return;
      case 'mcp':
        this.openSettings('mcp');
        return;
      case 'pane': {
        const k = (args.trim() || 'terminal') as PaneRef['kind'];
        await this.openPane(defaultPane(k));
        return;
      }
      default:
        this.say('warn', `/${command} is not a command here.`);
    }
  }

  private async patchSettings(patch: Partial<ChatSettings>): Promise<void> {
    if (!this.conv) return;
    const id = this.conv.id;
    this.conv = { ...this.conv, settings: { ...this.conv.settings, ...patch } };
    const s = await this.guard(() => this.backend!.updateSettings(id, patch), 'Settings');
    if (!s || this.conv?.id !== id) return;
    const { reread_tokens, ...settings } = s;
    this.conv = { ...this.conv, settings };
    // VIS-219: the switch says what the next message costs, in chatd's own figure
    if (patch.agents !== undefined && this.conv.messages.length) this.agentsNote = agentsDone(patch.agents, reread_tokens);
  }

  /** the settings page's defaults for new chats (the permission mode) */
  private async patchDefaults(patch: Partial<ChatSettings>): Promise<void> {
    const d = await this.guard(() => this.backend!.updateDefaults(patch), 'Defaults');
    if (d && this.caps) this.caps = { ...this.caps, defaults: d };
  }

  private async applyPreset(p: Preset): Promise<void> {
    await this.patchSettings({ model: p.model, profile: p.profile, effort: p.effort, thinking: p.thinking, tools: p.tools, prompt_id: p.prompt_id, ...(p.packages ? { packages: p.packages } : {}), ...(p.mcp ? { mcp: p.mcp } : {}) });
    this.say('ok', `Preset "${p.name}" applied to this chat.`);
    this.goto(this.conv?.id ?? null);
  }

  // ---- panes ------------------------------------------------------------------------------------

  private get openPanes(): OpenPane[] {
    return (this.conv?.ui.panes ?? []).map((p) => ({ ...p, id: p.id ?? `${p.kind}:${p.ref}` }));
  }

  private async saveUi(ui: ConversationUi): Promise<void> {
    if (!this.conv) return;
    const id = this.conv.id;
    this.conv = { ...this.conv, ui };
    const s = await this.patchConv(id, { ui });
    if (s) this.applySummary(s);
  }

  private async openPane(pane: PaneRef, inline?: { content: string; lang: string } | null): Promise<void> {
    if (!this.conv) return;
    const ui = this.conv.ui;
    const key = `${pane.kind}:${pane.ref}`;
    const existing = ui.panes.find((p) => p.id === key || (p.kind === pane.kind && p.ref === pane.ref));
    if (inline) this.inline.set(key, inline);
    const id = existing?.id ?? key;
    const panes = existing ? ui.panes : [...ui.panes, { ...pane, id }];
    await this.saveUi({ ...ui, panes, active: id });
    showInTray(null);
    if (currentPage().device === 'phone') setTray(true);
  }
  private inline = new Map<string, { content: string; lang: string }>();

  private async closePane(id: string): Promise<void> {
    if (!this.conv) return;
    const ui = this.conv.ui;
    const panes = ui.panes.filter((p) => (p.id ?? `${p.kind}:${p.ref}`) !== id);
    const active = ui.active === id ? (panes[panes.length - 1]?.id ?? null) : ui.active;
    await this.saveUi({ ...ui, panes, active });
    if (!panes.length && trayOpen()) setTray(false);
  }

  /** a kind from the add menu; Preview opens the newest running server, else the page the chat
   *  made last, else the Files pane */
  private async pick(k: PaneKind): Promise<void> {
    if (k !== 'preview') return this.openPane(defaultPane(k));
    return this.openPane((await this.previewPane()) ?? defaultPane('files'));
  }

  /** the Preview the add menu opens: the newest server that listens, else the newest page */
  private async previewPane(): Promise<PaneRef | null> {
    const b = this.backend;
    const c = this.conv;
    if (!b || !c) return null;
    const server = newestServer(await b.sandboxPorts(c.id).catch(() => []));
    if (server) return portPane(server);
    const page = latestPage(c.messages) ?? (await this.newestPage());
    return page ? filePane(page) : null;
  }

  /** the newest HTML or SVG file at the top of the sandbox's working directory */
  private async newestPage(): Promise<string | null> {
    const c = this.conv;
    if (!this.backend || !c) return null;
    const list = await this.backend.sandboxFiles(c.id, c.sandbox?.cwd || '/work/sync').catch(() => [] as SandboxFile[]);
    const pages = list.filter((f) => f.kind === 'file' && filePane(f.path)?.kind === 'preview');
    pages.sort((a, b) => (b.modified_at ?? '').localeCompare(a.modified_at ?? ''));
    return pages[0]?.path ?? null;
  }

  /** Chat's default windows (lib/wm.ts CHAT_DEFAULTS): the shell, the files and the page the chat
   *  made (when it made one or runs a server), the shell in front. Opened when the Panes button finds
   *  none, and by Reset layout, which puts them in place of the chat's panes. A chat never opens
   *  them by itself: a shell starts the chat's sandbox. */
  private async openDefaults(replace: boolean): Promise<void> {
    const c = this.conv;
    if (!c) return;
    const want: PaneRef[] = [];
    for (const k of CHAT_DEFAULTS) {
      const p = k === 'preview' ? await this.previewPane() : defaultPane(k as PaneKind);
      if (p) want.push({ ...p, id: `${p.kind}:${p.ref}` });
    }
    if (this.conv?.id !== c.id) return;
    const ui = this.conv.ui;
    const keep = replace ? [] : ui.panes;
    const panes = [...keep, ...want.filter((w) => !keep.some((p) => p.kind === w.kind && p.ref === w.ref))];
    await this.saveUi({ ...ui, panes, active: want[0]?.id ?? panes[0]?.id ?? null });
  }

  /** A server that started listening opens in a Preview pane (preview.ts rule 2): only while the
   *  panes show, not when a preview already shows that port; a waiting preview takes it itself. */
  private async watchPorts(): Promise<void> {
    const c = this.conv;
    if (!this.backend || !c || !c.ui.active || document.hidden) return;
    const id = c.id;
    const ports = await this.backend.sandboxPorts(id).catch(() => null);
    if (!ports || id !== this.conv?.id) return;
    if (this.portConv !== id) {
      this.portConv = id;
      this.portSeen = null;
    }
    const { seen, fresh } = freshServers(this.portSeen, ports);
    this.portSeen = seen;
    const server = newestServer(fresh);
    if (!server) return;
    const waiting = [...this.paneEls.values()].find((p) => p.el.pane?.kind === 'preview' && p.el.waiting);
    if (waiting) return waiting.el.refresh(waiting.el.pane!.ref);
    const shown = this.openPanes.some((x) => x.kind === 'preview' && (x.ref === `port:${server.port}` || [...this.paneEls.values()].some((p) => p.el.pane?.id === x.id && p.el.port === server.port)));
    if (!shown) void this.openPane(portPane(server));
  }

  // ---- chrome ---------------------------------------------------------------------------------

  /** the Panes button: the chat's windows shown or put away; with none, the defaults. On a phone,
   *  the tray. */
  private togglePanes(): void {
    const c = this.conv;
    if (!c) return;
    const phone = currentPage().device === 'phone';
    if (phone && trayOpen() && c.ui.active) return setTray(false);
    if (!this.openPanes.length) void this.openDefaults(false);
    else if (!c.ui.active || phone) void this.saveUi({ ...c.ui, active: c.ui.active ?? this.openPanes[0]?.id ?? null });
    else void this.saveUi({ ...c.ui, active: null });
    if (phone) setTray(true);
  }
  private openSettings(section: SettingsSection): void {
    location.hash = `#/chat/settings/${section}`;
    this.closeDrawer();
  }

  private exportMarkdown(): void {
    const c = this.conv;
    if (!c) return;
    const lines = [`# ${c.title}`, '', `${c.model}${c.profile ? `, ${c.profile}` : ''}; exported ${new Date().toISOString().slice(0, 16).replace('T', ' ')}`, ''];
    for (const m of c.messages) {
      lines.push(`## ${m.role}`, '');
      for (const p of m.parts) {
        if (p.type === 'text') lines.push(p.text, '');
        else if (p.type === 'thinking') lines.push('<details><summary>thinking</summary>', '', p.text, '', '</details>', '');
        else if (p.type === 'tool_call') lines.push(`**${p.name}** (${p.status})`, '', '```json', p.input, '```', ...(p.output ? ['', '```', p.output.content, '```'] : []), '');
      }
    }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/markdown' }));
    a.download = `${c.title.replace(/[^\w-]+/g, '-').slice(0, 60) || 'chat'}.md`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    this.more = false;
  }

  // ---- render ---------------------------------------------------------------------------------

  render() {
    if (this.down) return html`<div class="view ch ch-down-view"><qse-chat-down .down=${this.down} .probe=${this.reprobe} .now=${this.now} data-tick=${this.probeTick}></qse-chat-down></div>`;
    if (this.needSignIn) return this.renderSignIn();
    const c = this.conv;
    const section = this.section;
    const panes = this.openPanes;
    const panesOpen = !!c && panes.length > 0 && !!c.ui.active && !section;
    void this.sidesTick;
    return html`<div class="view ch ${panesOpen ? 'is-panes' : ''}" data-backend=${this.backend?.kind ?? ''} @dragover=${(e: DragEvent) => e.preventDefault()} @drop=${(e: DragEvent) => {
      e.preventDefault();
      if (e.dataTransfer?.files.length) this.composer?.acceptFiles(e.dataTransfer.files);
    }}>
      <section class="ch-main">
        ${section ? this.renderSettings(section) : this.renderChat(c, panes, panesOpen)}
      </section>
      ${this.notice ? html`<div class="ch-toast ch-toast-${this.notice.kind}" role="status">${this.notice.text}</div>` : nothing}
    </div>`;
  }

  private renderChat(c: Conversation | null, panes: OpenPane[], panesOpen: boolean): TemplateResult {
    const rail = sidebarShown('rail');
    const ctx = sidebarShown('ctx');
    const panesOn = panesOpen && (currentPage().device !== 'phone' || trayOpen());
    return html`
      <header class="ch-head">
        <button type="button" class="btn btn-ghost btn-icon ch-head-rail" aria-label=${rail ? 'Hide the chats' : 'Show the chats'} title="Chats" aria-pressed=${rail} @click=${() => toggleSidebar('rail')}>${icon('menu')}</button>
        <div class="ch-head-title">
          ${c
            ? html`<h1 class="ch-title" title=${c.title}>${c.title}</h1>
                <span class="ch-head-sub"><span class="beta-pill" title="The Chat tab is in beta">Beta</span>${c.status !== 'idle' ? html`<span class="ch-mark ch-mark-${c.status}"><span class="ch-mark-dot" aria-hidden="true"></span>${c.status === 'running' ? 'running' : c.status === 'waiting' ? 'waiting for you' : 'error'}</span>` : nothing}<span class="ch-head-model">${c.model}${c.profile ? `, ${c.profile}` : ''}</span>${this.attach.state !== 'idle' && this.attach.state !== 'live' && c.run ? html`<span class="ch-attach is-${attachLabel(this.attach, this.now).tone}">${attachLabel(this.attach, this.now).text}</span>` : nothing}</span>`
            : html`<h1 class="ch-title">Chat</h1><span class="ch-head-sub"><span class="beta-pill" title="The Chat tab is in beta">Beta</span></span>`}
        </div>
        <div class="ch-head-tools">
          ${this.backend?.kind === 'mock' ? html`<span class="ch-mock-badge ch-mock-head" title=${this.mockNote}>mock data</span>` : nothing}
          <button type="button" class="btn btn-ghost btn-icon ${panesOn ? 'is-on' : ''}" aria-label="Panes" title="Panes: terminal, files, preview" ?disabled=${!c} aria-pressed=${panesOn} @click=${() => this.togglePanes()}>${icon('terminal')}${panes.length ? html`<span class="ch-count">${panes.length}</span>` : nothing}</button>
          <button type="button" class="btn btn-ghost btn-icon ${ctx ? 'is-on' : ''}" aria-label="Context panel" title="Context" aria-pressed=${ctx} @click=${() => toggleSidebar('ctx')}>${icon('panel')}</button>
          <span class="ch-more">
            <button type="button" class="btn btn-ghost btn-icon" aria-label="More" aria-haspopup="menu" aria-expanded=${this.more} ?disabled=${!c} @click=${() => (this.more = !this.more)}>${icon('more')}</button>
            ${this.more && c
              ? html`<div class="ch-pop ch-more-pop" role="menu">
                  <button type="button" role="menuitem" class="ch-pop-item" @click=${() => (void this.pin(c.id, !c.pinned), (this.more = false))}>${icon('pin')}<span class="ch-pop-name">${c.pinned ? 'Unpin' : 'Pin'}</span></button>
                  <button type="button" role="menuitem" class="ch-pop-item" @click=${() => {
                    this.more = false;
                    const t = prompt('Title', c.title);
                    if (t != null && t.trim()) void this.rename(c.id, t.trim());
                  }}>${icon('edit')}<span class="ch-pop-name">Rename</span></button>
                  <button type="button" role="menuitem" class="ch-pop-item" @click=${() => this.exportMarkdown()}>${icon('down')}<span class="ch-pop-name">Export as Markdown</span></button>
                  <button type="button" role="menuitem" class="ch-pop-item" @click=${() => (this.openSettings('sandbox'), (this.more = false))}>${icon('system')}<span class="ch-pop-name">Sandbox</span></button>
                  <button type="button" role="menuitem" class="ch-pop-item ch-danger" @click=${() => {
                    this.more = false;
                    if (confirm(`Delete "${c.title}"? The chat and its sandbox go away.`)) void this.removeChat(c.id);
                  }}>${icon('trash')}<span class="ch-pop-name">Delete</span></button>
                </div>`
              : nothing}
          </span>
        </div>
      </header>
      <div class="ch-body">
        <div class="ch-threadwrap">
          ${this.lost ? downBanner() : nothing}
          ${c ? this.renderThread(c) : this.renderEmpty()}
          ${this.renderComposer(c)}
        </div>
      </div>`;
  }

  private renderThread(c: Conversation): TemplateResult {
    return html`<qse-chat-thread .conv=${c} .attach=${this.attach} .now=${this.now}
      @ch-permission=${(e: CustomEvent<{ callId: string; decision: 'once' | 'always' | 'deny' }>) => void this.guard(() => this.backend!.permission(c.id, e.detail.callId, e.detail.decision), 'Permission')}
      @ch-edit=${(e: CustomEvent<{ messageId: string; text: string }>) => void this.guard(() => this.backend!.edit(c.id, e.detail.messageId, e.detail.text, clientMsgId()), 'Edit')}
      @ch-regenerate=${(e: CustomEvent<{ messageId: string }>) => void this.guard(() => this.backend!.regenerate(c.id, e.detail.messageId), 'Regenerate')}
      @ch-continue=${() => void this.guard(() => this.backend!.continueRun(c.id), 'Continue')}
      @ch-stop=${() => void this.guard(() => this.backend!.stop(c.id), 'Stop')}
      @ch-task-load=${(e: CustomEvent<{ taskId: string }>) => void this.loadTask(c.id, e.detail.taskId)}
      @ch-branch=${(e: CustomEvent<{ messageId: string; index: number }>) => void this.guard(() => this.backend!.switchBranch(c.id, e.detail.messageId, e.detail.index).then((n) => this.adoptSnapshot(n)), 'Branch')}
      @ch-approve-plan=${(e: CustomEvent<{ items?: string[] }>) => void this.guard(() => this.backend!.approvePlan(c.id, e.detail.items), 'Approve')}
      @ch-rewind=${(e: CustomEvent<{ messageId: string }>) =>
        void this.guard(
          () =>
            this.backend!.rewind(c.id, e.detail.messageId).then((r) => {
              this.adoptSnapshot(r.conversation);
              this.composer?.setText(r.text);
            }),
          'Rewind',
        )}
      @ch-open-pane=${(e: CustomEvent<{ pane: PaneRef; inline?: { content: string; lang: string } | null }>) => void this.openPane(e.detail.pane, e.detail.inline)}
      @ch-unqueue=${(e: CustomEvent<{ id: string }>) => void this.guard(() => this.backend!.unqueue(c.id, e.detail.id), 'Queue')}
      @ch-send-now=${(e: CustomEvent<{ id: string }>) => void this.guard(() => this.backend!.sendNow(c.id, e.detail.id), 'Send now')}
      @ch-refresh=${() => void this.open(c.id)}
    ></qse-chat-thread>
    ${this.loading ? html`<div class="ch-loading muted small"><span class="pulse"></span> loading</div>` : nothing}`;
  }

  private renderEmpty(): TemplateResult {
    if (this.convError) return html`<div class="ch-empty"><p class="ch-empty-title">That chat is not here.</p><p class="muted">${this.convError}</p><button type="button" class="btn" @click=${() => void this.newChat()}>${icon('plus')} New chat</button></div>`;
    return html`<div class="ch-empty">
      <qse-wren-figure class="ch-empty-fig" state="idle" .size=${96}></qse-wren-figure>
      <p class="ch-empty-title">Talk to the box.</p>
      <p class="ch-empty-lead muted">One model on this machine, with a sandbox, tools and the engine's own numbers beside every answer. A chat keeps running when you close the tab; open it on the phone and watch.</p>
      <div class="ch-starters">${STARTERS.map((s) => html`<button type="button" class="ch-starter" @click=${() => void this.send(s, [])}>${s}</button>`)}</div>
    </div>`;
  }

  /** the mock's badge: why this tab runs on it */
  private get mockNote(): string {
    return this.backendReason === 'dev' ? 'dev server: no chat routes' : 'opened with ?chat=mock';
  }

  private renderComposer(c: Conversation | null): TemplateResult {
    return html`<qse-chat-composer .settings=${c?.settings ?? this.caps?.defaults ?? null} .caps=${this.caps} .tools=${this.tools} .packages=${this.packages} .resources=${this.resources} .prompts=${this.prompts} .running=${!!c?.run} .draft=${c ? (lsGet(LS.draft(c.id)) ?? '') : ''}
      .rereadTokens=${c?.messages.length ? c.context.used : null} .agentsNote=${this.agentsNote}
      @ch-agents-note-close=${() => (this.agentsNote = null)}
      .upload=${this.backend?.kind === 'remote' ? (f: File) => this.backend!.uploadAttachment(f) : null}
      @ch-send=${(e: CustomEvent<{ text: string; attachments: Attachment[] }>) => void this.send(e.detail.text, e.detail.attachments)}
      @ch-stop=${() => c && void this.guard(() => this.backend!.stop(c.id), 'Stop')}
      @ch-draft=${(e: CustomEvent<{ text: string }>) => c && lsSet(LS.draft(c.id), e.detail.text || null)}
      @ch-mcp-prompt=${(e: CustomEvent<{ serverId: string; name: string; args: Record<string, string> }>) => void this.guard(() => this.backend!.mcpPrompt(e.detail.serverId, e.detail.name, e.detail.args, c?.id ?? null).then((t) => this.composer?.setText(t)), 'MCP prompt')}
      @ch-mcp-resource=${(e: CustomEvent<{ serverId: string; uri: string }>) => void this.guard(() => this.backend!.mcpResource(e.detail.serverId, e.detail.uri, c?.id ?? null).then((a) => this.composer?.attach(a)), 'MCP resource')}
      @ch-command=${(e: CustomEvent<{ command: string; args: string }>) => void this.command(e.detail.command, e.detail.args)}
      @ch-settings-patch=${(e: CustomEvent<{ patch: Partial<ChatSettings> }>) => (c ? void this.patchSettings(e.detail.patch) : void this.newChat(e.detail.patch))}
      @ch-open-settings=${(e: CustomEvent<{ section: SettingsSection }>) => this.openSettings(e.detail.section)}
    ></qse-chat-composer>`;
  }

  private renderSettings(section: SettingsSection): TemplateResult {
    return html`<qse-chat-settings .backend=${this.backend} .caps=${this.caps} .conv=${this.conv} .section=${section}
      @ch-settings-nav=${(e: CustomEvent<{ section: SettingsSection }>) => this.openSettings(e.detail.section)}
      @ch-settings-back=${() => this.goto(this.conv?.id ?? null)}
      @ch-apply-preset=${(e: CustomEvent<{ preset: Preset }>) => void this.applyPreset(e.detail.preset)}
      @ch-open-pane=${(e: CustomEvent<{ pane: PaneRef }>) => {
        this.goto(this.conv?.id ?? null);
        void this.openPane(e.detail.pane);
      }}
      @ch-reset-sandbox=${() => this.conv && void this.guard(() => this.backend!.resetSandbox(this.conv!.id).then((s) => (this.conv = this.conv ? { ...this.conv, sandbox: s } : this.conv)), 'Reset sandbox')}
      @ch-settings-patch=${(e: CustomEvent<{ patch: Partial<ChatSettings> }>) => void this.patchSettings(e.detail.patch)}
      @ch-defaults-patch=${(e: CustomEvent<{ patch: Partial<ChatSettings> }>) => void this.patchDefaults(e.detail.patch)}
      @ch-web-search-saved=${(e: CustomEvent<{ provider: WebSearchProvider }>) => this.caps && (this.caps = { ...this.caps, web_search: { providers: ['off', 'duckduckgo', 'searxng'], ...this.caps.web_search, provider: e.detail.provider } })}
    ></qse-chat-settings>`;
  }

  /** Only when the server has a password set and this device is not signed in (contract 2, 7.8):
   *  the setup guide's own sign-in card, which sets the dashboard's session cookie once. */
  private renderSignIn(): TemplateResult {
    return html`<div class="view ch ch-signin">
      <div class="ch-signin-card">
        <qse-wren-figure state="listening" .size=${72}></qse-wren-figure>
        <h1>Sign in to chat</h1>
        <p class="muted">This server has an admin password. The chat keeps your conversations and runs tools on the box, so it asks once per device.</p>
        <qse-setup-signin></qse-setup-signin>
      </div>
    </div>`;
  }

  private onSignedIn = async (): Promise<void> => {
    const run = this.boots.current();
    if (!this.needSignIn || !this.backend) return;
    if (this.backend.kind === 'mock') await this.backend.signIn('');
    if (!this.boots.live(run)) return;   // left during the sign-in: start nothing
    this.needSignIn = false;
    await this.loadCaps(run);
    if (!this.boots.live(run)) return;   // left while the capabilities loaded
    if (!this.needSignIn) await this.afterSignIn(run);
  };
}

export { messageText, localId };
