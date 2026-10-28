// Playground — the chat bench: a multi-turn streaming conversation against the
// public chat endpoint with the reasoning shown, editable roles and a system prompt, a
// parameters column that sends only what was changed, the function-calling flow with mock
// results, a per-turn readout of the engine's figures, presets in the browser, and exports.
//
// VIS-254: on a fleet (two Sparks or more) the toolbar's Target picks what the turns go to: this
// server's own model, a deployment through TandemApp's gateway, or one member of it pinned
// (playground/targets.ts, `?target=`); each answer names the Spark that wrote it. Compare sends the
// next turn to two targets at once and shows both answers side by side with their Spark and their
// timings (time to first token, decode, total, tokens); either answer can be kept in the chat.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { repeat } from 'lit/directives/repeat.js';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import { api, describeError } from '../api/client';
import type { ServerSystem } from '../api/types';
import { exact } from '../lib/format';
import { LightElement } from '../ui/base';
import { copyButton, icon } from '../ui/bits';
import { exportFileName, readoutLine, readoutOf, toJsonExport, toMarkdownExport } from '../playground/export';
import { renderMarkdown } from '../playground/markdown';
import { defaultsFrom, validateParams, type Defaults } from '../playground/params';
import { clearDraft, deletePreset, exportPresets, importPresets, loadDraft, loadPresets, presetFrom, renamePreset, saveDraft, setupEquals, setupFrom, upsertPreset } from '../playground/presets';
import { asCurl, buildRequest, type ChatRequestBody } from '../playground/request';
import { ChatStreamReducer, readChatStream } from '../playground/stream';
import { echoResult, prettyArgs, templateFor, toolResultMessages, validateTools } from '../playground/tools';
import { emptySetup, ROLES, uid, type Message, type Role, type Setup, type TurnStats } from '../playground/types';
import type { PresetAction, QsePgSetup } from '../playground/setup-panel';
import '../playground/setup-panel';
import { onFleet, type FleetState } from '../lib/fleet-state';
import { isFleet } from '../lib/where';
import { setParams } from '../lib/router';
import { nodeChip } from '../ui/node-chip';
import { comparePair, sendTo, targetOf, targetsOf, type Target } from '../playground/targets';

/** The server serves one model (2026-10-01); the raw engine's old name is only a hidden alias. */
const MODEL = 'TandemLLM';

/** one side of Compare: a target's answer as it streams, and its figures */
interface CompareRun {
  key: string;
  label: string;
  content: string;
  reasoning: string;
  /** the Spark's alias, from the gateway's X-Tandem-Node */
  node: string | null;
  state: 'streaming' | 'done' | 'error' | 'aborted';
  error: string | null;
  /** browser-measured, from the request to the first content or reasoning */
  ttftMs: number | null;
  elapsedMs: number;
  stats: TurnStats | null;
}

interface LastResponse {
  message: { role: 'assistant'; content: string; reasoning_content?: string; tool_calls?: Message['tool_calls'] };
  finish_reason: string | null;
  usage?: TurnStats['usage'];
  timings?: TurnStats['timings'];
  metrics?: TurnStats['metrics'];
  error?: TurnStats['error'];
  chunks: number;
  bytes: number;
}

@customElement('qse-playground')
export class QsePlayground extends LightElement {
  @property({ attribute: false }) params: URLSearchParams = new URLSearchParams();

  @state() private setup: Setup = emptySetup();
  @state() private messages: Message[] = [];
  @state() private presets = loadPresets();
  @state() private presetName: string | null = null;
  @state() private storageOk = true;
  @state() private defaults: Defaults = {};
  @state() private defaultsReady = false;
  @state() private model: string | null = null;
  @state() private streaming = false;
  @state() private editing: { uid: string; text: string; role: Role } | null = null;
  @state() private composer = '';
  // a touch keyboard's Enter is a newline; a keyboard's Enter sends
  @state() private enterSends = !(typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches);
  @state() private drawer: 'none' | 'raw' | 'json' = 'none';
  @state() private setupOpen = false;
  @state() private exportOpen = false;
  @state() private readoutMore = false;
  @state() private notice: { kind: 'ok' | 'warn'; text: string } | null = null;
  @state() private lastRequest: ChatRequestBody | null = null;
  @state() private lastResponse: LastResponse | null = null;
  @state() private toolResults: Record<string, string> = {};
  /** VIS-254: the fleet (targets), Compare's two targets and their answers */
  @state() private fleet: FleetState | null = null;
  @state() private compare: { a: string; b: string } | null = null;
  @state() private compareRuns: CompareRun[] = [];
  @state() private compareUser: Message | null = null;
  private compareCtrl: AbortController | null = null;
  private offFleet: (() => void) | null = null;
  private ctrl: AbortController | null = null;
  private draftTimer: ReturnType<typeof setTimeout> | null = null;
  private noticeTimer: ReturnType<typeof setTimeout> | null = null;
  private stickToEnd = true;

  connectedCallback(): void {
    super.connectedCallback();
    const d = loadDraft();
    if (d) {
      this.setup = d.setup;
      this.messages = d.messages.map((m) => ({ ...m, uid: m.uid || uid() }));
      this.presetName = d.presetName;
    }
    // a reload or a tab switch inside the debounce window must not lose the draft
    window.addEventListener('pagehide', this.flushDraft);
    void this.loadSystem();
    this.offFleet = onFleet((f) => (this.fleet = f));
  }
  disconnectedCallback(): void {
    this.ctrl?.abort();
    this.compareCtrl?.abort();
    this.offFleet?.();
    window.removeEventListener('pagehide', this.flushDraft);
    this.flushDraft();
    super.disconnectedCallback();
  }

  private async loadSystem(): Promise<void> {
    try {
      const s: ServerSystem = await api.system();
      this.defaults = defaultsFrom(s);
      this.defaultsReady = true;
      this.model = s.engine.model;
    } catch (e) {
      const d = describeError(e);
      if (d.status !== 401) this.say('warn', `Server defaults are not available (${d.endpoint || 'the engine'}: ${d.message}); the fields still work, the engine's own defaults apply.`);
    }
  }

  // ---- state helpers ----------------------------------------------------------------------
  private say(kind: 'ok' | 'warn', text: string): void {
    this.notice = { kind, text };
    if (this.noticeTimer) clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => (this.notice = null), kind === 'ok' ? 4000 : 9000);
  }

  /** Save the draft: debounced while typing, at once for an explicit action (`now`). */
  private persist(now = false): void {
    if (this.draftTimer) clearTimeout(this.draftTimer);
    this.draftTimer = null;
    if (now) return this.writeDraft();
    this.draftTimer = setTimeout(() => {
      this.draftTimer = null;
      this.writeDraft();
    }, 300);
  }
  private writeDraft(): void {
    // the draft never keeps a turn that is still streaming
    const ok = saveDraft({ version: 1, setup: this.setup, messages: this.messages.filter((m) => !m.stats || m.stats.finish !== null || m.role !== 'assistant'), presetName: this.presetName });
    if (!ok && this.storageOk) this.storageOk = false;
  }
  private flushDraft = (): void => {
    if (!this.draftTimer) return;
    clearTimeout(this.draftTimer);
    this.draftTimer = null;
    this.writeDraft();
  };

  private setSetup(s: Setup): void {
    this.setup = s;
    this.persist();
  }
  private setMessages(m: Message[]): void {
    this.messages = m;
    this.persist();
  }

  private get toolsValidation() {
    return validateTools(this.setup.toolsText);
  }
  private get paramErrors() {
    return validateParams(this.setup.params, this.defaults);
  }
  private get blocked(): string | null {
    const pe = Object.values(this.paramErrors);
    if (pe.length) return pe[0];
    const te = this.toolsValidation.errors;
    if (te.length) return `tools: ${te[0]}`;
    return null;
  }
  private get presetDirty(): boolean {
    if (!this.presetName) return false;
    const p = this.presets.find((x) => x.name === this.presetName);
    return !!p && !setupEquals(setupFrom(p), this.setup);
  }

  /** `#/playground?profile=coding` (the Conductor's "Try in Playground"): the composite model under
   *  that profile, named in the X-Tandem-Profile header. */
  private get profile(): string | null {
    return this.params.get('profile');
  }

  private currentRequest(extra: Message[] = [], t: Target = this.target): ChatRequestBody {
    return buildRequest(this.setup, [...this.messages, ...extra], { model: sendTo(t, MODEL).model, tools: this.toolsValidation.tools });
  }

  // ---- VIS-254: the target ------------------------------------------------------------------
  private get targets(): Target[] {
    return isFleet(this.fleet) ? targetsOf(this.fleet) : [];
  }
  /** the turns' target: this server's own model unless a fleet's is picked (`?target=`) */
  private get target(): Target {
    return targetOf(this.targets, this.params.get('target'));
  }
  private nodeByAlias(alias: string | null | undefined) {
    return alias ? this.fleet?.nodes.find((n) => n.alias === alias) ?? null : null;
  }

  private scrollTranscript(): void {
    if (!this.stickToEnd) return;
    this.updateComplete.then(() => {
      const el = this.querySelector<HTMLElement>('.pg-transcript');
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

  // ---- the conversation -------------------------------------------------------------------
  private send(): void {
    if (this.compare) return void this.runCompare();
    if (this.streaming) return;
    const text = this.composer.trim();
    if (text) {
      this.setMessages([...this.messages, { uid: uid(), role: 'user', content: text }]);
      this.composer = '';
    } else if (!this.messages.length) return;
    void this.run();
  }

  private regenerate(): void {
    if (this.streaming) return;
    let msgs = this.messages.slice();
    while (msgs.length && msgs[msgs.length - 1].role === 'assistant') msgs.pop();
    if (!msgs.length) return;
    this.setMessages(msgs);
    void this.run();
  }

  private stop(): void {
    this.ctrl?.abort();
    this.compareCtrl?.abort();
  }
  /** a Compare turn streams on either side */
  private get comparing(): boolean {
    return this.compareRuns.some((r) => r.state === 'streaming');
  }

  private newChat(): void {
    this.ctrl?.abort();
    this.setMessages([]);
    this.lastRequest = null;
    this.lastResponse = null;
    this.toolResults = {};
    this.editing = null;
    clearDraft();
    this.persist(true);
  }

  private async run(): Promise<void> {
    const blocked = this.blocked;
    if (blocked) return this.say('warn', blocked);
    this.ctrl?.abort();
    const ctrl = new AbortController();
    this.ctrl = ctrl;
    const body = this.currentRequest();
    this.lastRequest = body;
    this.lastResponse = null;
    const turn: Message = { uid: uid(), role: 'assistant', content: '', reasoning: '', stats: { finish: null } };
    this.messages = [...this.messages, turn];
    this.streaming = true;
    this.stickToEnd = true;
    this.scrollTranscript();
    const started = performance.now();
    const reducer = new ChatStreamReducer();
    let bytes = 0;
    const apply = (final = false) => {
      const st = final ? reducer.end() : reducer.state;
      const idx = this.messages.findIndex((m) => m.uid === turn.uid);
      if (idx < 0) return;
      const next: Message = {
        ...turn,
        content: st.content,
        reasoning: st.reasoning,
        tool_calls: st.toolCalls.length ? st.toolCalls : undefined,
        stats: { ...(this.messages[idx].stats ?? { finish: null }), finish: final ? st.finish ?? (st.error ? 'error' : 'stop') : null, usage: st.usage, timings: st.timings, metrics: st.metrics, error: st.error ?? null, elapsedMs: performance.now() - started, chunks: st.chunks, bytes, node },
      };
      const msgs = this.messages.slice();
      msgs[idx] = next;
      this.messages = msgs;
      this.scrollTranscript();
      return next;
    };
    const to = sendTo(this.target, MODEL);
    let node: string | null = null;
    try {
      const res = await fetch(to.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'qse-dashboard', ...(this.profile ? { 'X-Tandem-Profile': this.profile } : {}), ...to.headers },
        body: JSON.stringify(body),
        signal: ctrl.signal,
        credentials: 'same-origin',
      });
      node = res.headers.get('X-Tandem-Node');
      if (!res.ok || !res.body) {
        let msg = `HTTP ${res.status}`;
        let type: string | undefined;
        try {
          const j = await res.json();
          msg = j?.error?.message ?? msg;
          type = j?.error?.type;
        } catch {
          /* no body */
        }
        const retry = res.headers.get('Retry-After');
        const text = `${res.status}: ${msg}${retry ? ` — retry after ${retry} s` : ''}`;
        this.finishTurn(turn.uid, { finish: 'error', error: { message: text, type }, elapsedMs: performance.now() - started });
        this.lastResponse = { message: { role: 'assistant', content: '' }, finish_reason: null, error: { message: text, type }, chunks: 0, bytes: 0 };
        return;
      }
      for await (const chunk of readChatStream(res.body, { signal: ctrl.signal, onBytes: (n) => (bytes += n) })) {
        reducer.feed(chunk);
        apply();
      }
      if (ctrl.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      const final = apply(true);
      const st = reducer.state;
      this.lastResponse = { message: { role: 'assistant', content: st.content, ...(st.reasoning ? { reasoning_content: st.reasoning } : {}), ...(st.toolCalls.length ? { tool_calls: st.toolCalls } : {}) }, finish_reason: st.finish, usage: st.usage, timings: st.timings, metrics: st.metrics, error: st.error ?? undefined, chunks: st.chunks, bytes };
      if (final?.tool_calls?.length) {
        // prefill the result boxes with the templates' examples
        const r = { ...this.toolResults };
        for (const c of final.tool_calls) if (r[c.id] === undefined) r[c.id] = templateFor(c.function.name)?.example ?? '';
        this.toolResults = r;
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        const st = reducer.end();
        const idx = this.messages.findIndex((m) => m.uid === turn.uid);
        if (idx >= 0) {
          const msgs = this.messages.slice();
          msgs[idx] = { ...msgs[idx], content: st.content, reasoning: st.reasoning, tool_calls: st.toolCalls.length ? st.toolCalls : undefined, stats: { finish: 'aborted', usage: st.usage, timings: st.timings, metrics: st.metrics, elapsedMs: performance.now() - started, chunks: st.chunks, bytes } };
          this.messages = msgs;
        }
        this.lastResponse = { message: { role: 'assistant', content: st.content }, finish_reason: 'aborted', chunks: st.chunks, bytes };
      } else {
        this.finishTurn(turn.uid, { finish: 'error', error: { message: (e as Error).message }, elapsedMs: performance.now() - started });
      }
    } finally {
      if (this.ctrl === ctrl) {
        this.streaming = false;
        this.ctrl = null;
      }
      this.persist();
    }
  }

  // ---- VIS-254: Compare -----------------------------------------------------------------------
  private toggleCompare(): void {
    if (this.compare) {
      this.compareCtrl?.abort();
      this.compare = null;
      this.compareRuns = [];
      this.compareUser = null;
      return;
    }
    const [a, b] = comparePair(this.targets);
    this.compare = { a, b };
  }

  /** the same next turn on both targets at once, each answer streaming in its column */
  private async runCompare(): Promise<void> {
    const c = this.compare;
    if (!c || this.compareRuns.some((r) => r.state === 'streaming')) return;
    const blocked = this.blocked;
    if (blocked) return this.say('warn', blocked);
    const text = this.composer.trim();
    if (!text && !this.messages.length) return;
    const user: Message | null = text ? { uid: uid(), role: 'user', content: text } : null;
    this.composer = '';
    this.compareUser = user;
    const msgs = user ? [...this.messages, user] : this.messages;
    const pair = [c.a, c.b].map((k) => targetOf(this.targets, k));
    this.compareCtrl?.abort();
    const ctrl = new AbortController();
    this.compareCtrl = ctrl;
    this.compareRuns = pair.map((t) => ({ key: t.key, label: this.targetLabel(t), content: '', reasoning: '', node: t.pin?.alias ?? null, state: 'streaming', error: null, ttftMs: null, elapsedMs: 0, stats: null }));
    await Promise.all(pair.map((t, i) => this.streamOne(t, msgs, ctrl, i)));
  }

  private async streamOne(t: Target, msgs: Message[], ctrl: AbortController, i: number): Promise<void> {
    const to = sendTo(t, MODEL);
    const body = buildRequest(this.setup, msgs, { model: to.model, tools: this.toolsValidation.tools });
    const t0 = performance.now();
    const reducer = new ChatStreamReducer();
    const set = (patch: Partial<CompareRun>) => {
      if (this.compareCtrl !== ctrl) return;
      const runs = this.compareRuns.slice();
      runs[i] = { ...runs[i], ...patch, elapsedMs: performance.now() - t0 };
      this.compareRuns = runs;
    };
    try {
      const res = await fetch(to.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'qse-dashboard', ...to.headers },
        body: JSON.stringify(body),
        signal: ctrl.signal,
        credentials: 'same-origin',
      });
      const node = res.headers.get('X-Tandem-Node') ?? t.pin?.alias ?? null;
      if (!res.ok || !res.body) {
        let msg = `HTTP ${res.status}`;
        try {
          msg = (await res.json())?.error?.message ?? msg;
        } catch {
          /* no body */
        }
        return set({ state: 'error', error: `${res.status}: ${msg}`, node });
      }
      let ttft: number | null = null;
      for await (const chunk of readChatStream(res.body, { signal: ctrl.signal })) {
        reducer.feed(chunk);
        const st = reducer.state;
        if (ttft == null && (st.content || st.reasoning)) ttft = performance.now() - t0;
        set({ content: st.content, reasoning: st.reasoning, node, ttftMs: ttft });
      }
      const st = reducer.end();
      set({ content: st.content, reasoning: st.reasoning, node, ttftMs: ttft, state: st.error ? 'error' : 'done', error: st.error?.message ?? null, stats: { finish: st.finish, usage: st.usage, timings: st.timings, metrics: st.metrics, error: st.error ?? null, chunks: st.chunks, node } });
    } catch (e) {
      set({ state: (e as Error).name === 'AbortError' ? 'aborted' : 'error', error: (e as Error).name === 'AbortError' ? null : (e as Error).message });
    }
  }

  /** keeps one side's answer in the chat: the compared turn and that answer */
  private keepCompare(r: CompareRun): void {
    const kept: Message = { uid: uid(), role: 'assistant', content: r.content, reasoning: r.reasoning, stats: { ...(r.stats ?? { finish: 'stop' }), elapsedMs: r.elapsedMs, node: r.node } };
    this.setMessages([...this.messages, ...(this.compareUser ? [this.compareUser] : []), kept]);
    this.compareRuns = [];
    this.compareUser = null;
    this.stickToEnd = true;
    this.scrollTranscript();
  }

  private targetLabel(t: Target): string {
    return t.deployment ? t.label : `This server · ${MODEL}`;
  }

  /** the client's own figures beside the engine's: time to first token, decode, total, tokens */
  private compareFigures(r: CompareRun): TemplateResult {
    const out = r.stats?.usage?.completion_tokens ?? null;
    const tps = out != null && r.ttftMs != null && r.elapsedMs > r.ttftMs ? (Math.max(0, out - 1) * 1000) / (r.elapsedMs - r.ttftMs) : null;
    return html`<dl class="pg-cmp-figs num">
      <div data-fig="ttft"><dt>first token</dt><dd>${r.ttftMs == null ? '—' : `${(r.ttftMs / 1000).toFixed(2)} s`}</dd></div>
      <div data-fig="decode"><dt>decode</dt><dd>${tps == null ? '—' : `${tps.toFixed(1)} tok/s`}</dd></div>
      <div data-fig="total"><dt>total</dt><dd>${(r.elapsedMs / 1000).toFixed(2)} s</dd></div>
      <div data-fig="tokens"><dt>tokens</dt><dd>${out == null ? '—' : exact(out)}</dd></div>
    </dl>
    ${r.stats ? html`<p class="pg-cmp-engine muted small">${readoutLine(r.stats)}</p>` : nothing}`;
  }

  private renderCompare(): TemplateResult | typeof nothing {
    const c = this.compare;
    if (!c) return nothing;
    const list = this.targets;
    const pick = (side: 'a' | 'b') => html`<label class="field"><span>${side.toUpperCase()}</span>
      <select data-cmp=${side} .value=${c[side]} @change=${(e: Event) => (this.compare = { ...c, [side]: (e.target as HTMLSelectElement).value })}>
        ${list.map((t) => html`<option value=${t.key} ?selected=${t.key === c[side]}>${this.targetLabel(t)}</option>`)}
      </select></label>`;
    const runs = this.compareRuns;
    return html`<section class="pg-compare" data-compare aria-label="compare two targets">
      <div class="pg-cmp-bar">${pick('a')}<span class="muted">vs</span>${pick('b')}<span class="grow"></span><span class="muted small">the next turn goes to both; keep the answer you want in the chat</span></div>
      ${runs.length
        ? html`<div class="pg-cmp-cols">
            ${runs.map((r) => {
              const n = this.nodeByAlias(r.node);
              return html`<article class="pg-cmp-col is-${r.state}" data-cmp-col=${r.key}>
                <header class="pg-cmp-h"><b>${r.label}</b>${n ? nodeChip(n) : r.node ? html`<span class="small">${r.node}</span>` : nothing}<span class="grow"></span>${r.state === 'streaming' ? html`<span class="pulse"></span>` : nothing}</header>
                ${r.reasoning ? html`<details class="pg-think"><summary>thinking</summary><div class="pg-think-body">${r.reasoning}</div></details>` : nothing}
                ${r.content || r.state === 'streaming' ? html`<div class="pg-md ${r.state === 'streaming' ? 'is-streaming' : ''}">${unsafeHTML(renderMarkdown(r.content))}</div>` : nothing}
                ${r.error ? html`<div class="pg-msg-error" role="alert">${icon('warn')} <span>${r.error}</span></div>` : nothing}
                ${this.compareFigures(r)}
                ${r.state === 'done' ? html`<button type="button" class="btn btn-sm" data-keep @click=${() => this.keepCompare(r)}>Keep this answer</button>` : nothing}
              </article>`;
            })}
          </div>`
        : nothing}
    </section>`;
  }

  private finishTurn(id: string, stats: TurnStats): void {
    const idx = this.messages.findIndex((m) => m.uid === id);
    if (idx < 0) return;
    const msgs = this.messages.slice();
    msgs[idx] = { ...msgs[idx], stats: { ...msgs[idx].stats, ...stats } };
    this.messages = msgs;
  }

  // ---- edits ------------------------------------------------------------------------------
  private startEdit(m: Message): void {
    this.editing = { uid: m.uid, text: m.content, role: m.role };
  }
  private saveEdit(resend: boolean): void {
    const e = this.editing;
    if (!e) return;
    const idx = this.messages.findIndex((m) => m.uid === e.uid);
    if (idx < 0) {
      this.editing = null;
      return;
    }
    const msgs = this.messages.slice();
    const prev = msgs[idx];
    msgs[idx] = { ...prev, content: e.text, role: e.role, ...(e.role !== 'assistant' ? { tool_calls: undefined, reasoning: undefined, stats: undefined } : {}), ...(e.role !== 'tool' ? { tool_call_id: undefined, name: undefined } : {}) };
    this.editing = null;
    if (resend) {
      this.setMessages(msgs.slice(0, idx + 1));
      void this.run();
    } else this.setMessages(msgs);
  }
  private deleteMessage(id: string): void {
    this.setMessages(this.messages.filter((m) => m.uid !== id));
    if (this.editing?.uid === id) this.editing = null;
  }
  private addMessage(role: Role): void {
    const m: Message = { uid: uid(), role, content: '' };
    this.setMessages([...this.messages, m]);
    this.editing = { uid: m.uid, text: '', role };
    this.stickToEnd = true;
    this.scrollTranscript();
    this.updateComplete.then(() => this.querySelector<HTMLTextAreaElement>(`[data-uid="${m.uid}"] textarea`)?.focus());
  }

  private sendToolResults(m: Message): void {
    if (!m.tool_calls?.length || this.streaming) return;
    const missing = m.tool_calls.filter((c) => !(this.toolResults[c.id] ?? '').trim());
    if (missing.length) this.say('warn', `${missing.length} call${missing.length === 1 ? ' has' : 's have'} no result; sent as an empty string.`);
    const idx = this.messages.findIndex((x) => x.uid === m.uid);
    const results = toolResultMessages(m.tool_calls, this.toolResults);
    this.setMessages([...this.messages.slice(0, idx + 1), ...results]);
    void this.run();
  }

  // ---- presets ----------------------------------------------------------------------------
  private onPreset(a: PresetAction): void {
    switch (a.action) {
      case 'save': {
        const r = upsertPreset(this.presets, presetFrom(a.name, this.setup));
        this.presets = r.list;
        this.presetName = a.name;
        if (!r.persisted) this.storageOk = false;
        this.say(r.persisted ? 'ok' : 'warn', r.persisted ? `Saved preset "${a.name}".` : `"${a.name}" is kept for this page only — storage is blocked.`);
        this.persist(true);
        break;
      }
      case 'load': {
        const p = this.presets.find((x) => x.name === a.name);
        if (!p) return;
        this.setup = setupFrom(p);
        this.presetName = p.name;
        this.persist(true);
        this.say('ok', `Loaded "${p.name}".`);
        break;
      }
      case 'delete': {
        this.presets = deletePreset(this.presets, a.name).list;
        if (this.presetName === a.name) this.presetName = null;
        this.persist(true);
        break;
      }
      case 'rename': {
        this.presets = renamePreset(this.presets, a.name, a.to).list;
        if (this.presetName === a.name) this.presetName = a.to;
        this.persist(true);
        break;
      }
      case 'export':
        this.download(exportPresets(this.presets), `playground-presets-${new Date().toISOString().slice(0, 10)}.json`, 'application/json');
        break;
      case 'import': {
        const r = importPresets(this.presets, a.text);
        this.presets = r.list;
        if (!r.added) this.say('warn', `Import refused: ${r.errors[0] ?? 'no presets in the file'}`);
        else this.say(r.errors.length ? 'warn' : 'ok', `Imported ${r.added} preset${r.added === 1 ? '' : 's'}${r.errors.length ? `; skipped: ${r.errors.join('; ')}` : '.'}`);
        break;
      }
    }
  }

  private download(text: string, name: string, type: string): void {
    const a = document.createElement('a');
    const url = URL.createObjectURL(new Blob([text], { type }));
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  private exportConversation(kind: 'json' | 'md'): void {
    this.exportOpen = false;
    if (kind === 'json') this.download(toJsonExport(this.setup, this.messages, this.model), exportFileName('json'), 'application/json');
    else this.download(toMarkdownExport(this.setup, this.messages, this.model), exportFileName('md'), 'text/markdown');
  }

  // ---- rendering --------------------------------------------------------------------------
  private renderMessage(m: Message, i: number): TemplateResult {
    const isLast = i === this.messages.length - 1;
    const live = this.streaming && isLast && m.role === 'assistant' && m.stats?.finish === null;
    const editing = this.editing?.uid === m.uid;
    const answered = m.tool_calls?.length ? this.messages.slice(i + 1).some((x) => x.role === 'tool') : false;
    return html`<article class="pg-msg role-${m.role} ${live ? 'is-live' : ''} ${editing ? 'is-editing' : ''}" data-uid=${m.uid} data-role=${m.role}>
      <header class="pg-msg-head">
        <span class="pg-role">${m.role}${m.role === 'tool' && m.name ? html` <code>${m.name}</code>` : nothing}</span>
        ${!editing && !live
          ? html`<span class="pg-msg-tools">
              ${copyButton(m.content, 'Copy message')}
              <button type="button" class="btn btn-ghost btn-sm" aria-label="Edit message" title="Edit" @click=${() => this.startEdit(m)}>${icon('edit')}</button>
              <button type="button" class="btn btn-ghost btn-sm" aria-label="Delete message" title="Delete" @click=${() => this.deleteMessage(m.uid)}>${icon('trash')}</button>
            </span>`
          : nothing}
      </header>
      <div class="pg-msg-body">${editing ? this.renderEditor(m) : this.renderBody(m, live, answered)}</div>
      ${m.role === 'assistant' && m.stats && !editing ? html`<footer class="pg-msg-foot num" data-testid="readout-line">${m.stats.finish === null ? html`<span class="pulse"></span> streaming` : readoutLine(m.stats)}${m.stats.finish === 'aborted' ? html` · <span class="warn-ink">aborted</span>` : nothing}${m.stats.node ? html` <span class="pg-on" data-node-on>on ${this.nodeByAlias(m.stats.node) ? nodeChip(this.nodeByAlias(m.stats.node)!, { status: false }) : m.stats.node}</span>` : nothing}</footer>` : nothing}
    </article>`;
  }

  private renderEditor(m: Message): TemplateResult {
    const e = this.editing!;
    return html`<div class="pg-edit">
      <textarea rows="4" .value=${e.text} @input=${(ev: Event) => (this.editing = { ...e, text: (ev.target as HTMLTextAreaElement).value })} @keydown=${(ev: KeyboardEvent) => {
        if (ev.key === 'Escape') this.editing = null;
        if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) this.saveEdit(e.role === 'user');
      }}></textarea>
      <div class="pg-edit-row">
        <label class="field"><span>role</span><select .value=${e.role} @change=${(ev: Event) => (this.editing = { ...e, role: (ev.target as HTMLSelectElement).value as Role })}>${ROLES.map((r) => html`<option value=${r} ?selected=${r === e.role}>${r}</option>`)}</select></label>
        <span class="grow"></span>
        <button type="button" class="btn btn-ghost btn-sm" @click=${() => (this.editing = null)}>Cancel</button>
        <button type="button" class="btn btn-sm" @click=${() => this.saveEdit(false)}>Save</button>
        ${e.role === 'user' ? html`<button type="button" class="btn btn-primary btn-sm" ?disabled=${this.streaming} @click=${() => this.saveEdit(true)}>${icon('send')} Save & resend</button>` : nothing}
        ${m.role !== e.role ? html`<span class="muted small">role changes to ${e.role}</span>` : nothing}
      </div>
    </div>`;
  }

  private renderBody(m: Message, live: boolean, answered: boolean): TemplateResult {
    if (m.role === 'assistant') {
      const thinkingOnly = live && !m.content.trim() && !m.tool_calls?.length;
      return html`
        ${m.reasoning
          ? html`<details class="pg-think" ?open=${thinkingOnly}>
              <summary>${live && thinkingOnly ? html`<span class="pulse"></span> thinking` : 'thinking'}${m.stats?.usage?.completion_tokens_details?.reasoning_tokens != null ? html` <span class="num muted">${exact(m.stats.usage.completion_tokens_details.reasoning_tokens)} tokens</span>` : nothing}</summary>
              <div class="pg-think-body">${m.reasoning}</div>
            </details>`
          : nothing}
        ${m.content || (live && !m.reasoning && !m.tool_calls?.length) ? html`<div class="pg-md ${live ? 'is-streaming' : ''}">${unsafeHTML(renderMarkdown(m.content))}</div>` : nothing}
        ${m.tool_calls?.length ? this.renderToolCalls(m, answered, live) : nothing}
        ${m.stats?.error ? html`<div class="pg-msg-error" role="alert">${icon('warn')} <span>${m.stats.error.message}</span></div>` : nothing}
      `;
    }
    if (m.role === 'tool') return html`<pre class="pg-tool-result num">${m.content || html`<span class="muted">(empty result)</span>`}</pre>`;
    return html`<div class="pg-text">${m.content || html`<span class="muted">(empty)</span>`}</div>`;
  }

  private renderToolCalls(m: Message, answered: boolean, live: boolean): TemplateResult {
    return html`<div class="pg-calls">
      ${m.tool_calls!.map((c) => {
        const args = prettyArgs(c.function.arguments);
        const tpl = templateFor(c.function.name);
        return html`<div class="pg-call" data-call=${c.id}>
          <div class="pg-call-head"><span class="pg-call-name"><code>${c.function.name || '…'}</code>${c.id ? html`<span class="muted small num">${c.id}</span>` : nothing}</span>${copyButton(c.function.arguments, 'Copy arguments')}</div>
          <pre class="pg-call-args ${args.parsed ? '' : 'is-raw'} ${live ? 'is-streaming' : ''}">${args.text || (live ? '' : '{}')}</pre>
          ${!answered && !live
            ? html`<div class="pg-call-result">
                <label class="field field-block"><span>result for <code>${c.function.name}</code></span>
                  <textarea rows="2" placeholder="what the tool returned (any text; JSON is usual)" .value=${this.toolResults[c.id] ?? ''} @input=${(e: Event) => (this.toolResults = { ...this.toolResults, [c.id]: (e.target as HTMLTextAreaElement).value })}></textarea>
                </label>
                <div class="pg-call-picks">
                  ${tpl ? html`<button type="button" class="btn btn-sm" @click=${() => (this.toolResults = { ...this.toolResults, [c.id]: tpl.example })}>template result</button>` : nothing}
                  <button type="button" class="btn btn-sm" @click=${() => (this.toolResults = { ...this.toolResults, [c.id]: echoResult(c) })}>echo the arguments</button>
                  <button type="button" class="btn btn-sm" @click=${() => (this.toolResults = { ...this.toolResults, [c.id]: '{"ok": true}' })}><code>{"ok": true}</code></button>
                </div>
              </div>`
            : nothing}
        </div>`;
      })}
      ${!answered && !live ? html`<button type="button" class="btn btn-primary pg-send-results" ?disabled=${this.streaming} @click=${() => this.sendToolResults(m)}>${icon('send')} Send result${m.tool_calls!.length > 1 ? 's' : ''} and continue</button>` : nothing}
    </div>`;
  }

  private renderComposer(): TemplateResult {
    const blocked = this.blocked;
    return html`<form class="pg-composer" @submit=${(e: Event) => (e.preventDefault(), this.send())}>
      <label class="sr-only" for="pg-composer">Message</label>
      <textarea id="pg-composer" rows="3" placeholder=${this.messages.length ? 'Reply as user…' : 'Ask the engine something…'} .value=${this.composer} @input=${(e: Event) => (this.composer = (e.target as HTMLTextAreaElement).value)}
        @keydown=${(e: KeyboardEvent) => {
          if (e.key !== 'Enter') return;
          if (e.metaKey || e.ctrlKey || (this.enterSends && !e.shiftKey)) {
            e.preventDefault();
            this.send();
          }
        }}></textarea>
      <div class="pg-composer-row">
        <label class="switch" title="Enter sends; Shift+Enter is a newline. Off: Enter is a newline, ⌘/Ctrl+Enter sends."><input type="checkbox" .checked=${this.enterSends} @change=${(e: Event) => (this.enterSends = (e.target as HTMLInputElement).checked)} /><span>Enter sends</span></label>
        <div class="pg-add">
          <label class="field"><span>+ add</span>
            <select aria-label="Add a message with a role" .value=${''} @change=${(e: Event) => {
              const r = (e.target as HTMLSelectElement).value as Role | '';
              (e.target as HTMLSelectElement).value = '';
              if (r) this.addMessage(r);
            }}>
              <option value="">message…</option>
              ${ROLES.map((r) => html`<option value=${r}>${r}</option>`)}
            </select>
          </label>
        </div>
        <span class="grow"></span>
        ${blocked ? html`<span class="pg-blocked warn-ink small" role="status">${blocked}</span>` : nothing}
        ${this.streaming || this.comparing
          ? html`<button type="button" class="btn pg-stop" @click=${() => this.stop()}>${icon('stop')} Stop</button>`
          : html`<button type="submit" class="btn btn-primary pg-send" ?disabled=${!!blocked || (!this.composer.trim() && !this.messages.length)}>${icon('send')} Send</button>`}
      </div>
    </form>`;
  }

  private renderReadout(): TemplateResult {
    const last = [...this.messages].reverse().find((m) => m.role === 'assistant' && m.stats && m.stats.finish !== null);
    const figs = readoutOf(last?.stats);
    return html`<section class="pg-readout ${this.readoutMore ? 'is-more' : ''}" aria-label="last response readout" data-testid="readout">
      <div class="pg-readout-strip">
        ${figs.map((f) => html`<div class="pg-fig ${f.key ? 'is-key' : ''} ${f.na ? 'is-na' : ''}" data-fig=${f.id} title=${f.title ?? nothing}><span class="pg-fig-label">${f.label}</span><span class="pg-fig-value num">${f.value}${f.unit ? html`<span class="pg-fig-unit">${f.unit}</span>` : nothing}</span></div>`)}
      </div>
      <div class="pg-readout-tools">
        <button type="button" class="btn btn-ghost btn-sm pg-readout-more" aria-expanded=${this.readoutMore} @click=${() => (this.readoutMore = !this.readoutMore)}>${this.readoutMore ? 'less' : 'more'}</button>
        <button type="button" class="btn btn-ghost btn-sm ${this.drawer === 'json' ? 'is-on' : ''}" aria-pressed=${this.drawer === 'json'} ?disabled=${!this.lastRequest} @click=${() => (this.drawer = this.drawer === 'json' ? 'none' : 'json')}>JSON</button>
        <button type="button" class="btn btn-ghost btn-sm ${this.drawer === 'raw' ? 'is-on' : ''}" aria-pressed=${this.drawer === 'raw'} @click=${() => (this.drawer = this.drawer === 'raw' ? 'none' : 'raw')}>Raw request</button>
      </div>
    </section>`;
  }

  private renderDrawer(): TemplateResult | typeof nothing {
    if (this.drawer === 'raw') {
      // the composer's text is the next user message: the drawer shows exactly what Send posts
      const pending = this.composer.trim();
      const body = this.currentRequest(pending ? [{ uid: 'pending', role: 'user', content: pending }] : []);
      const json = JSON.stringify(body, null, 2);
      return html`<section class="pg-drawer" data-drawer="raw">
        <header class="pg-drawer-head"><h3>Raw request</h3><span class="muted small">what the next Send posts to <code>POST ${this.target.deployment ? '/v1/fleet/gateway/chat/completions' : '/v1/chat/completions'}</code>${this.target.pin ? html` with <code>X-Tandem-Pin: ${this.target.pin.id}</code>` : nothing}</span><span class="grow"></span>${copyButton(json, 'Copy JSON')}<button type="button" class="btn btn-ghost btn-sm" @click=${async () => {
          try {
            await navigator.clipboard.writeText(asCurl(body, location.origin));
            this.say('ok', 'curl copied.');
          } catch {
            this.say('warn', 'The clipboard is blocked.');
          }
        }}>copy as curl</button><button type="button" class="btn btn-ghost btn-sm" aria-label="Close" @click=${() => (this.drawer = 'none')}>${icon('x')}</button></header>
        <pre class="pg-json" data-testid="raw-request">${json}</pre>
      </section>`;
    }
    if (this.drawer === 'json') {
      const req = this.lastRequest ? JSON.stringify(this.lastRequest, null, 2) : '';
      const res = this.lastResponse ? JSON.stringify({ message: this.lastResponse.message, finish_reason: this.lastResponse.finish_reason, ...(this.lastResponse.error ? { error: this.lastResponse.error } : {}), usage: this.lastResponse.usage, timings: this.lastResponse.timings, metrics: this.lastResponse.metrics }, null, 2) : '';
      return html`<section class="pg-drawer pg-drawer-2" data-drawer="json">
        <div class="pg-drawer-pane">
          <header class="pg-drawer-head"><h3>Last request</h3><span class="grow"></span>${copyButton(req, 'Copy request')}</header>
          <pre class="pg-json" data-testid="last-request">${req || html`<span class="muted">nothing sent yet</span>`}</pre>
        </div>
        <div class="pg-drawer-pane">
          <header class="pg-drawer-head"><h3>Last response</h3>${this.lastResponse ? html`<span class="muted small num">${this.lastResponse.chunks} chunks · ${exact(this.lastResponse.bytes)} B</span>` : nothing}<span class="grow"></span>${copyButton(res, 'Copy response')}<button type="button" class="btn btn-ghost btn-sm" aria-label="Close" @click=${() => (this.drawer = 'none')}>${icon('x')}</button></header>
          <pre class="pg-json" data-testid="last-response">${res || html`<span class="muted">${this.streaming ? 'streaming…' : 'no response yet'}</span>`}</pre>
        </div>
      </section>`;
    }
    return nothing;
  }

  private renderToolbar(): TemplateResult {
    const canRegen = !this.streaming && this.messages.some((m) => m.role === 'user' || m.role === 'tool');
    return html`<div class="pg-toolbar">
      <button type="button" class="btn btn-sm" @click=${() => this.newChat()}>${icon('plus')} New chat</button>
      <button type="button" class="btn btn-sm pg-iconic" title="Regenerate" ?disabled=${!canRegen} @click=${() => this.regenerate()}>${icon('refresh')}<span class="pg-btn-label">Regenerate</span></button>
      <div class="pg-menu">
        <button type="button" class="btn btn-sm pg-iconic" title="Export" aria-haspopup="menu" aria-expanded=${this.exportOpen} ?disabled=${!this.messages.length} @click=${() => (this.exportOpen = !this.exportOpen)}>${icon('down')}<span class="pg-btn-label">Export</span></button>
        ${this.exportOpen ? html`<div class="pg-menu-pop" role="menu"><button type="button" role="menuitem" class="btn btn-ghost btn-sm" @click=${() => this.exportConversation('json')}>as JSON</button><button type="button" role="menuitem" class="btn btn-ghost btn-sm" @click=${() => this.exportConversation('md')}>as Markdown</button></div>` : nothing}
      </div>
      <span class="grow"></span>
      ${this.notice ? html`<span class="pg-notice pg-notice-${this.notice.kind}" role="status">${this.notice.text}</span>` : nothing}
      <label class="field pg-preset-pick"><span>preset</span>
        <select aria-label="Load a preset" .value=${this.presetName ?? ''} @change=${(e: Event) => {
          const n = (e.target as HTMLSelectElement).value;
          if (n) this.onPreset({ action: 'load', name: n });
        }}>
          <option value="">${this.presets.length ? 'choose…' : 'none saved'}</option>
          ${this.presets.map((p) => html`<option value=${p.name} ?selected=${p.name === this.presetName}>${p.name}${p.name === this.presetName && this.presetDirty ? ' •' : ''}</option>`)}
        </select>
      </label>
      <button type="button" class="btn btn-sm pg-save-preset" @click=${() => {
        this.setupOpen = true;
        const panel = this.querySelector<QsePgSetup>('qse-pg-setup');
        panel?.show('presets');
        this.updateComplete.then(() => panel?.querySelector<HTMLInputElement>('#pg-preset-name')?.focus());
      }}>Save preset</button>
      ${this.profile ? html`<span class="badge badge-on" data-pg-profile title="model TandemLLM, header X-Tandem-Profile: ${this.profile}">TandemLLM · ${this.profile}</span>` : nothing}
      ${this.targets.length > 1
        ? html`<label class="field pg-target" data-pg-target><span>target</span>
              <select aria-label="Where the turns go" .value=${this.target.key} @change=${(e: Event) => setParams({ target: (e.target as HTMLSelectElement).value || null })}>
                ${this.targets.map((t) => html`<option value=${t.key} ?selected=${t.key === this.target.key}>${this.targetLabel(t)}</option>`)}
              </select></label>
            <button type="button" class="btn btn-sm ${this.compare ? 'is-on' : ''}" aria-pressed=${!!this.compare} data-pg-compare title="The same next turn on two targets, side by side" @click=${() => this.toggleCompare()}>Compare</button>`
        : nothing}
      <button type="button" class="btn btn-sm pg-setup-toggle" aria-expanded=${this.setupOpen} aria-controls="pg-setup" @click=${() => (this.setupOpen = !this.setupOpen)}>${icon('sliders')} Setup${this.presetDirty || Object.keys(this.setup.params).length || this.setup.system ? html`<span class="pg-dot" aria-hidden="true"></span>` : nothing}</button>
    </div>`;
  }

  render() {
    return html`<div class="view view-playground pg ${this.setupOpen ? 'is-setup-open' : ''}" @click=${(e: Event) => {
      if (this.exportOpen && !(e.target as HTMLElement).closest('.pg-menu')) this.exportOpen = false;
    }}>
      ${this.renderToolbar()}
      <div class="pg-bench">
        <section class="pg-main" aria-label="conversation">
          <div class="pg-transcript" role="log" aria-live="polite" @scroll=${(e: Event) => {
            const el = e.currentTarget as HTMLElement;
            this.stickToEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          }}>
            ${this.messages.length
              ? repeat(
                  this.messages,
                  (m) => m.uid,
                  (m, i) => this.renderMessage(m, i),
                )
              : html`<div class="pg-empty">
                  <p class="pg-empty-title">A bench for the engine.</p>
                  <p class="muted">Type a message and send it, or set a system prompt, parameters and tools on the right first. Every turn shows the engine's own figures; nothing typed here leaves this browser except the request itself.</p>
                </div>`}
          </div>
          ${this.renderCompare()}
          ${this.renderComposer()}
          ${this.renderReadout()}
          ${this.renderDrawer()}
        </section>
        <div class="pg-scrim" @click=${() => (this.setupOpen = false)}></div>
        <aside class="pg-setup" id="pg-setup" aria-label="setup">
          <header class="pg-setup-head"><h2>Setup</h2><span class="grow"></span><button type="button" class="btn btn-ghost btn-icon pg-setup-close" aria-label="Close setup" @click=${() => (this.setupOpen = false)}>${icon('x')}</button></header>
          <qse-pg-setup
            .setup=${this.setup}
            .defaults=${this.defaults}
            .defaultsReady=${this.defaultsReady}
            .presets=${this.presets}
            .presetName=${this.presetName}
            .presetDirty=${this.presetDirty}
            .tools=${this.toolsValidation}
            .paramErrors=${this.paramErrors}
            .storageOk=${this.storageOk}
            @pg-change=${(e: CustomEvent<Setup>) => this.setSetup(e.detail)}
            @pg-preset=${(e: CustomEvent<PresetAction>) => this.onPreset(e.detail)}
          ></qse-pg-setup>
        </aside>
      </div>
    </div>`;
  }
}
