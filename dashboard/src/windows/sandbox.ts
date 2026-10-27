// The windows on a chat's sandbox, on any page (VIS-256), one chunk: Terminal, Files, Editor, Diff,
// Markdown, Plan, Preview, Image, PDF, Video, Audio, File and Context. Each draws <qse-chat-pane>,
// the Chat tab's own pane, for the chat its bar names (the last one opened on this device, until
// another is picked): the sandbox is the one shell and file tree a box offers today. Terminal and
// Files may name a Spark instead; that path runs through the Spark's TandemNode agent (SRV-725),
// which has no shell or file channel yet, so the window says so and keeps the Spark in its ref for
// when it does.
// On Chat itself these kinds are the open chat's own panes (chat/view.ts); here they are windows
// of the page.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { isChatDown, pickBackend, type ChatBackend, type ChatDown } from '../chat/backend';
import { downText } from '../chat/chat-down';
import '../chat/panes';
import type { OpenPane } from '../chat/panes';
import type { Conversation, ConversationSummary, PaneRef } from '../chat/types';
import '../styles/chat.css';
import { onFleet, type FleetState } from '../lib/fleet-state';
import { icon } from '../ui/bits';
import { Feed, WindowBody } from './base';
import { onNode } from './kinds';
import { paneFor } from './sandbox-pane';

/** One backend for every sandbox window of a page load: the chat's rule (the mock with ?chat=mock
 *  or on the dev server, else the server's chat API when it answers). */
let choice: Promise<{ backend: ChatBackend | null; down: ChatDown | null }> | null = null;
function chatBackend(): Promise<{ backend: ChatBackend | null; down: ChatDown | null }> {
  choice ??= pickBackend(new URLSearchParams(location.search), {
    mock: async () => new (await import('../chat/mock')).MockChatBackend({ scenario: new URLSearchParams(location.search).get('scenario') }),
    remote: async () => new (await import('../chat/remote')).RemoteChatBackend(),
  }).then((c) => ({ backend: c.backend, down: c.backend ? null : c.down }));
  return choice;
}

function lastChat(): string | null {
  try {
    return localStorage.getItem('qse.chat.last');
  } catch {
    return null;
  }
}

const str = (v: unknown) => (typeof v === 'string' ? v : '');

@customElement('qse-win-sandbox')
export class QseWinSandbox extends WindowBody {
  @state() private backend: ChatBackend | null = null;
  @state() private down: ChatDown | null = null;
  @state() private list: ConversationSummary[] = [];
  @state() private conv: Conversation | null = null;
  @state() private fleetState: FleetState | null = null;
  private feed = new Feed((cur) => this.load(cur), 10_000);
  private offFleet: (() => void) | null = null;
  private asked: string | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    this.offFleet = onFleet((f) => (this.fleetState = f));
    void chatBackend().then(({ backend, down }) => {
      this.backend = backend;
      this.down = down;
      if (backend && this.isConnected && this.wantsChat) this.feed.start();
    });
  }
  disconnectedCallback(): void {
    this.offFleet?.();
    this.feed.stop();
    super.disconnectedCallback();
  }
  updated(): void {
    if (!this.backend) return;
    if (!this.wantsChat) return this.feed.stop();
    this.feed.start();
    if (this.chatId !== this.asked) {
      this.asked = this.chatId;
      this.feed.refresh();
    }
  }

  /** the Spark a Terminal or Files window is on (null: the chat sandbox); a Hardware window that
   *  names none is on the fleet's first */
  private get nodeTarget(): string | null {
    if (this.win?.kind !== 'terminal' && this.win?.kind !== 'files') return null;
    if (this.ref.node === null) return this.fleetState?.nodes[0]?.id ?? 'a Spark';
    return onNode(this.ref);
  }
  private get wantsChat(): boolean {
    return !this.nodeTarget;
  }
  private get chatId(): string | null {
    return str(this.ref.chat) || lastChat() || this.list[0]?.id || null;
  }

  private async load(isCurrent: () => boolean): Promise<void> {
    const b = this.backend!;
    try {
      const l = await b.listConversations();
      if (!isCurrent()) return;
      this.list = l.conversations;
      const id = this.chatId;
      if (!id) {
        this.conv = null;
        return;
      }
      const c = await b.getConversation(id);
      if (isCurrent() && id === this.chatId) this.conv = c;
    } catch (e) {
      if (isChatDown(e)) this.down = { cause: 'chatd_down', status: 502, message: '' };
    }
  }

  /** a pane inside asks for another (the Files' Edit, Open in Preview): another window */
  private onPaneOpen = (e: CustomEvent<{ pane: PaneRef }>) => {
    e.stopPropagation();
    const p = e.detail.pane;
    const chat = this.conv?.id ?? null;
    if (p.kind === 'editor') this.openWindow('editor', { chat, path: p.ref });
    else this.openWindow(p.kind, { chat, ref: p.ref, title: p.title ?? null });
  };

  private targetSelect(): TemplateResult | typeof nothing {
    const k = this.win?.kind;
    if (k !== 'terminal' && k !== 'files') return nothing;
    const on = this.nodeTarget;
    return html`<select class="wm-in" aria-label=${k === 'terminal' ? 'Terminal on' : 'Files of'} @change=${(e: Event) => this.setRef({ node: (e.target as HTMLSelectElement).value })}>
      <option value="sandbox" ?selected=${!on}>Chat sandbox</option>
      ${(this.fleetState?.nodes ?? []).map((n) => html`<option value=${n.id} ?selected=${on === n.id}>${n.alias}</option>`)}
    </select>`;
  }

  render() {
    const kind = this.win?.kind ?? 'terminal';
    const node = this.nodeTarget;
    if (node) {
      const alias = this.fleetState?.nodes.find((n) => n.id === node)?.alias ?? node;
      const what = kind === 'terminal' ? `A shell on ${alias}` : `${alias}’s files`;
      return html`<div class="wm-pane">
        <div class="wm-bar">${this.targetSelect()}<span class="muted small">through its TandemNode agent</span></div>
        <div class="wm-notice" data-unreachable=${node}>
          <p>${icon('info')} <span>${what} come${kind === 'terminal' ? 's' : ''} through its TandemNode agent, which has no shell or file channel yet. The window keeps ${alias} for when it does.</span></p>
          <p class="muted small">The chat sandbox has a ${kind === 'terminal' ? 'shell' : 'file tree'} on this box today.</p>
          <button type="button" class="btn btn-sm" @click=${() => this.setRef({ node: 'sandbox' })}>${icon(kind === 'terminal' ? 'terminal' : 'folder')} Use the chat sandbox</button>
        </div>
      </div>`;
    }
    if (this.down) {
      const t = downText(this.down);
      return html`<div class="wm-pane"><div class="wm-bar">${this.targetSelect()}</div><div class="wm-notice"><p>${icon('warn')} <span>${t.title}</span></p><p class="muted small">${t.lead}</p></div></div>`;
    }
    const id = this.chatId;
    const p = paneFor(kind, this.ref, this.conv);
    const bar = html`<div class="wm-bar">
      ${this.targetSelect()}
      <select class="wm-in wm-chat-pick" aria-label="Chat" @change=${(e: Event) => this.setRef({ chat: (e.target as HTMLSelectElement).value })}>
        ${!this.list.length ? html`<option value="">no chat yet</option>` : nothing}
        ${this.list.map((c) => html`<option value=${c.id} ?selected=${c.id === id}>${c.title}</option>`)}
      </select>
      <span class="grow"></span>
      ${id ? html`<a class="btn btn-ghost btn-sm" href=${`#/chat/${encodeURIComponent(id)}`} title="Open the chat">${icon('chat')}</a>` : nothing}
    </div>`;
    if (!this.backend) return html`<div class="wm-pane">${bar}<p class="muted small wm-empty">loading</p></div>`;
    if (!id) return html`<div class="wm-pane">${bar}<div class="wm-notice"><p>No chat yet. A chat’s sandbox holds the files, the shell and the pages it makes.</p><a class="btn btn-sm" href="#/chat">${icon('chat')} Start a chat</a></div></div>`;
    if ('none' in p) return html`<div class="wm-pane">${bar}<p class="wm-notice muted small">${p.none}</p></div>`;
    const pane: OpenPane = { ...p, id: this.win?.id ?? kind };
    return html`<div class="wm-pane ch-side">${bar}<qse-chat-pane .pane=${pane} .backend=${this.backend} .convId=${id} .conv=${this.conv} .sheet=${this.sheet} @ch-pane-open=${this.onPaneOpen}></qse-chat-pane></div>`;
  }
}
