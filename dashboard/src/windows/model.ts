// The model's windows (VIS-256), one chunk: a quick Chat and a one-shot Playground beside any page.
// Both talk to the one API (/v1/chat/completions, streamed), to a deployment of the fleet when there is
// one (its API id) or to the model this box serves. The Chat keeps its last turns in its ref, so they
// come back with the layout; the Playground keeps its prompt and shows the run's answer, its tokens and
// its rate. A full conversation, with tools and a sandbox, is the Chat page; every parameter, the
// Playground page.

import { html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { api } from '../api/client';
import type { ChatUsage } from '../api/types';
import { ChatStreamReducer, readChatStream } from '../playground/stream';
import { onFleet, type FleetState } from '../lib/fleet-state';
import { icon } from '../ui/bits';
import { WindowBody } from './base';

/** the model the box serves when there is no fleet (the Playground page's) */
const BOX_MODEL = 'TandemLLM';
/** what the Chat window keeps of its talk */
export const KEEP_TURNS = 12;

type Turn = { role: 'user' | 'assistant'; text: string };

/** One streamed answer; `onText` hears the text so far. */
async function ask(model: string, messages: Turn[], signal: AbortSignal, onText: (t: string) => void): Promise<{ text: string; usage?: ChatUsage; ms: number }> {
  const t0 = performance.now();
  const res = await fetch(api.chatUrl(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'qse-dashboard' },
    body: JSON.stringify({ model, messages: messages.map((m) => ({ role: m.role, content: m.text })), stream: true, stream_options: { include_usage: true } }),
    signal,
    credentials: 'same-origin',
  });
  if (!res.ok || !res.body) {
    let msg = `HTTP ${res.status}`;
    try {
      msg = (await res.json())?.error?.message ?? msg;
    } catch {
      /* no body */
    }
    throw new Error(msg);
  }
  const r = new ChatStreamReducer();
  for await (const c of readChatStream(res.body, { signal })) {
    r.feed(c);
    onText(r.state.content);
  }
  const st = r.end();
  if (st.error) throw new Error(st.error.message ?? 'the stream ended with an error');
  return { text: st.content, usage: st.usage, ms: performance.now() - t0 };
}

class ModelBody extends WindowBody {
  @state() protected fleetState: FleetState | null = null;
  @state() protected busy = false;
  protected ctrl: AbortController | null = null;
  private offFleet: (() => void) | null = null;
  connectedCallback(): void {
    super.connectedCallback();
    this.offFleet = onFleet((f) => (this.fleetState = f));
  }
  disconnectedCallback(): void {
    this.offFleet?.();
    this.ctrl?.abort();
    super.disconnectedCallback();
  }
  /** the deployments by their API id, or the box's model */
  protected get targets(): { id: string; label: string }[] {
    const d = this.fleetState?.deployments ?? [];
    return d.length ? d.map((x) => ({ id: x.api_id, label: x.name })) : [{ id: BOX_MODEL, label: `${BOX_MODEL} (this box)` }];
  }
  protected get model(): string {
    const m = typeof this.ref.model === 'string' ? this.ref.model : '';
    return this.targets.some((t) => t.id === m) ? m : this.targets[0].id;
  }
  protected modelSelect() {
    return html`<select class="wm-in" aria-label="Deployment" ?disabled=${this.busy} @change=${(e: Event) => this.setRef({ model: (e.target as HTMLSelectElement).value })}>
      ${this.targets.map((t) => html`<option value=${t.id} ?selected=${t.id === this.model}>${t.label}</option>`)}
    </select>`;
  }
}

@customElement('qse-win-minichat')
export class QseWinMiniChat extends ModelBody {
  @state() private live = '';
  @state() private error: string | null = null;

  private get turns(): Turn[] {
    return Array.isArray(this.ref.msgs) ? (this.ref.msgs as Turn[]).filter((m) => m && (m.role === 'user' || m.role === 'assistant') && typeof m.text === 'string') : [];
  }

  private async send(text: string): Promise<void> {
    if (!text.trim() || this.busy) return;
    const turns = [...this.turns, { role: 'user' as const, text: text.trim() }];
    this.setRef({ msgs: turns.slice(-KEEP_TURNS) });
    this.busy = true;
    this.error = null;
    this.live = '';
    this.ctrl = new AbortController();
    try {
      const a = await ask(this.model, turns, this.ctrl.signal, (t) => (this.live = t));
      this.setRef({ msgs: [...turns, { role: 'assistant', text: a.text }].slice(-KEEP_TURNS) });
    } catch (e) {
      if ((e as Error).name !== 'AbortError') this.error = (e as Error).message;
    } finally {
      this.busy = false;
      this.live = '';
      this.ctrl = null;
    }
  }

  updated(): void {
    const t = this.querySelector<HTMLElement>('.wm-mc-thread');
    if (t) t.scrollTop = t.scrollHeight;
  }

  render() {
    const turns = this.turns;
    return html`<div class="wm-pane wm-mc">
      <div class="wm-bar">${this.modelSelect()}<span class="grow"></span>${turns.length ? html`<button type="button" class="btn btn-ghost btn-sm" ?disabled=${this.busy} @click=${() => this.setRef({ msgs: [] })}>Clear</button>` : nothing}</div>
      <div class="wm-mc-thread" role="log">
        ${!turns.length && !this.busy ? html`<p class="muted small wm-empty">Ask the model something quick. The Chat page has the tools, the sandbox and the history.</p>` : nothing}
        ${turns.map((m) => html`<div class="wm-mc-m is-${m.role}">${m.text}</div>`)}
        ${this.busy ? html`<div class="wm-mc-m is-assistant is-live">${this.live}<span class="wm-cursor" aria-hidden="true"></span></div>` : nothing}
        ${this.error ? html`<p class="err-ink small">${this.error}</p>` : nothing}
      </div>
      <form class="wm-mc-in" @submit=${(e: Event) => {
        e.preventDefault();
        const i = (e.currentTarget as HTMLFormElement).querySelector('input')!;
        void this.send(i.value);
        i.value = '';
      }}>
        <input class="wm-in" aria-label="Message" placeholder="Message" ?disabled=${this.busy} />
        ${this.busy ? html`<button type="button" class="btn btn-sm" @click=${() => this.ctrl?.abort()}>${icon('stop')} Stop</button>` : html`<button type="submit" class="btn btn-sm btn-primary">${icon('send')} Send</button>`}
      </form>
    </div>`;
  }
}

@customElement('qse-win-miniplay')
export class QseWinMiniPlay extends ModelBody {
  @state() private out = '';
  @state() private stats: string | null = null;
  @state() private error: string | null = null;

  private async run(): Promise<void> {
    const prompt = typeof this.ref.prompt === 'string' ? this.ref.prompt.trim() : '';
    if (!prompt || this.busy) return;
    this.busy = true;
    this.out = '';
    this.stats = null;
    this.error = null;
    this.ctrl = new AbortController();
    try {
      const a = await ask(this.model, [{ role: 'user', text: prompt }], this.ctrl.signal, (t) => (this.out = t));
      const tok = a.usage?.completion_tokens;
      this.stats = tok != null ? `${tok} tokens in ${(a.ms / 1000).toFixed(1)} s · ${(tok / (a.ms / 1000)).toFixed(1)} tok/s` : `${(a.ms / 1000).toFixed(1)} s`;
    } catch (e) {
      if ((e as Error).name !== 'AbortError') this.error = (e as Error).message;
    } finally {
      this.busy = false;
      this.ctrl = null;
    }
  }

  render() {
    const prompt = typeof this.ref.prompt === 'string' ? this.ref.prompt : '';
    return html`<div class="wm-pane wm-mp">
      <div class="wm-bar">${this.modelSelect()}<span class="grow"></span>${this.busy ? html`<button type="button" class="btn btn-sm" @click=${() => this.ctrl?.abort()}>${icon('stop')} Stop</button>` : html`<button type="button" class="btn btn-sm btn-primary" ?disabled=${!prompt.trim()} @click=${() => void this.run()}>${icon('play')} Run</button>`}</div>
      <div class="wm-mp-b">
        <textarea class="wm-in" rows="3" aria-label="Prompt" placeholder="A prompt" .value=${prompt} @change=${(e: Event) => this.setRef({ prompt: (e.target as HTMLTextAreaElement).value })} @keydown=${(e: KeyboardEvent) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            this.setRef({ prompt: (e.target as HTMLTextAreaElement).value });
            void this.updateComplete.then(() => this.run());
          }
        }}></textarea>
        ${this.out || this.busy ? html`<div class="wm-mp-out">${this.out}${this.busy ? html`<span class="wm-cursor" aria-hidden="true"></span>` : nothing}</div>` : nothing}
        ${this.stats ? html`<p class="muted small num">${this.stats}</p>` : nothing}
        ${this.error ? html`<p class="err-ink small">${this.error}</p>` : nothing}
        <a class="small" href="#/playground">Every parameter: the Playground page</a>
      </div>
    </div>`;
  }
}
