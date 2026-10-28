// <qse-add-spark>: Settings → Add Spark (VIS-246, concept §6, the v3 prototype's wizard). Seven steps:
// Find (discovery's live list, the consented LAN scan, Add by address, a rented or remote node by its
// ssh line), Connect (the pairing code, or SSH with the host key confirmed as ssh does), Check (the
// controller's preflight per node, each red row with its fix), Install (TandemNode and the engine at
// the fleet's version, progress per node, its log on demand), Link (two or more new LAN nodes only),
// Name (alias, colour, icon, tags) and Done (the topology with the new nodes, then Serve a model).
// The session lives on the controller (/v1/fleet/add): a reload, or another device, opens it at its
// step. Back and Next on every step; a step with nothing to do is skipped and says why. Each
// permission is asked where it is needed, with one line of why: Local Network before the first search,
// notifications for a lease reminder; Keychain is not a browser app's to ask for, and says so.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { describeError } from '../../api/client';
import { ADD_STEPS, fleet, mergeDiscovery, type AddSession, type AddStep, type AddTarget, type Discovery, type DiscoveryRow, type FleetTopology, type SshTarget } from '../../api/fleet';
import { gate, installPercent, installWords, loginWords, nameDraft, nameErrors, namesBody, nextStep, permissionRows, prevStep, rentedLine, rentedTerms, resumeStep, skipped, skippedBetween, STEP_LABEL, type NameDraft, type RentedForm } from '../../lib/addspark';
import { permission, setSystemOn, systemOn } from '../../lib/alert-notify';
import { refreshContext, registerContext, type ContextContent } from '../../lib/context';
import { onFleet, refreshFleet, type FleetState } from '../../lib/fleet-state';
import { poll, type Poller } from '../../lib/poll';
import { colourChoices } from '../../lib/sparks';
import { LightElement } from '../../ui/base';
import { icon } from '../../ui/bits';
import { isHollow, nodeChip, nodeColour } from '../../ui/node-chip';
import { clock, signInCard } from '../monitor/bits';
import { checkSession, needsSignIn, onSession } from '../monitor/data';
import '../monitor/topology';
import '../../styles/wizard.css';

const POLL_MS = 1000;
const ICONS: { id: 'desk' | 'cloud' | 'rack'; label: string }[] = [
  { id: 'desk', label: 'Desk' },
  { id: 'rack', label: 'Rack' },
  { id: 'cloud', label: 'Cloud (rented)' },
];
const GLYPH = { ok: '✓', warn: '⚠', fail: '✕' } as const;
const SOURCE: Record<string, string> = { mdns: 'mDNS', scan: 'LAN scan', ssh_config: '~/.ssh/config', address: 'by address', login: 'SSH login', pair: 'pairing' };
type Auth = 'password' | 'key_file' | 'key' | 'agent';

interface ConnectDraft {
  mode: 'code' | 'ssh';
  code: string;
  user: string;
  auth: Auth;
  password: string;
  key: string;
  keyPath: string;
}

/** "Tue 3 Nov, 23:59": a reminder days away */
const day = (t: number) => new Date(t * 1000).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

const EMPTY_RENT: RentedForm = { line: '', key: '', keyPath: '', jump: '', leaseEnd: '', cost: '' };

@customElement('qse-add-spark')
export class QseAddSpark extends LightElement {
  @state() private session: AddSession | null = null;
  @state() private fleetVersion = '';
  @state() private loadErr = '';
  @state() private disc: Discovery | null = null;
  @state() private fleetState: FleetState | null = null;
  @state() private topo: FleetTopology | null = null;
  @state() private busy = '';
  @state() private err: Record<string, string> = {};
  @state() private manual: 'addr' | 'rent' | null = null;
  @state() private addr = '';
  @state() private rent: RentedForm = { ...EMPTY_RENT };
  @state() private rentTarget: SshTarget | null = null;
  @state() private drafts: Record<string, ConnectDraft> = {};
  @state() private secrets: Record<string, string> = {};
  @state() private names: Record<string, NameDraft> = {};
  @state() private showLog: Record<string, boolean> = {};
  @state() private lanAsk = false;
  @state() private notify = permission();
  private poller: Poller | null = null;
  private unsub: (() => void)[] = [];
  private discovering = false;

  connectedCallback(): void {
    super.connectedCallback();
    void checkSession();
    this.unsub.push(
      onSession(() => this.requestUpdate()),
      onFleet((f) => {
        this.fleetState = f;
        this.seedNames();
        refreshContext();
      }),
      registerContext('add-spark', () => this.context()),
    );
    window.addEventListener('qse-signed-in', this.onSignedIn);
    this.poller = poll((cur) => this.tick(cur), POLL_MS);
    this.poller.start();
    void this.open();
  }

  disconnectedCallback(): void {
    window.removeEventListener('qse-signed-in', this.onSignedIn);
    this.unsub.forEach((u) => u());
    this.unsub = [];
    this.poller?.stop();
    super.disconnectedCallback();
  }

  private onSignedIn = (): void => void this.open();

  // ---- the session ---------------------------------------------------------------------------
  /** The active session, resumed where it was; a new one when none is open. */
  private async open(): Promise<void> {
    try {
      const got = await fleet.addSession();
      this.fleetVersion = got.fleet_version;
      this.session = got.session ?? (await fleet.startAdd());
      this.loadErr = '';
      this.seedNames();
      if (this.at === 'find') await this.find();
      if (this.at === 'done') await this.readTopology();
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) await checkSession(true);
      this.loadErr = d.status === 401 ? 'sign-in' : d.status === 404 ? 'TandemApp’s controller on this server has no Add Spark wizard yet.' : d.message;
    }
    refreshContext();
  }

  private get at(): AddStep {
    return this.session ? resumeStep(this.session) : 'find';
  }

  private set(s: AddSession): void {
    this.session = s;
    this.seedNames();
    refreshContext();
  }

  /** a write to the session: busy while it runs, its error said beside `key` */
  private async run(key: string, f: () => Promise<AddSession>): Promise<boolean> {
    this.busy = key;
    this.err = { ...this.err, [key]: '' };
    try {
      this.set(await f());
      return true;
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) await checkSession(true);
      this.err = { ...this.err, [key]: d.status === 401 ? 'Sign in with TandemApp’s admin password first (below).' : d.message };
      if (this.session) void fleet.addSession().then((g) => g.session && this.set(g.session)).catch(() => undefined);
      return false;
    } finally {
      this.busy = '';
    }
  }

  private async go(step: AddStep): Promise<void> {
    const s = this.session;
    if (!s) return;
    if (step === 'done') {
      const body = namesBody(s, this.names);
      if (!(await this.run('next', () => fleet.patchAdd(s.id, { names: body, step: 'done' })))) return;
      refreshFleet();
      await this.readTopology();
    } else {
      await this.run('next', () => fleet.patchAdd(s.id, { step }));
      if (step === 'find') await this.find();
      if (step === 'link' && this.session && !skipped(this.session, 'link') && this.session.link.state === 'todo') void this.run('link', () => fleet.linkAdd(s.id, 'test'));
    }
    (document.querySelector('.main') as HTMLElement | null)?.scrollTo?.({ top: 0 });
  }

  private async tick(isCurrent: () => boolean): Promise<void> {
    const s = this.session;
    if (!s) return;
    if (this.at === 'find' && this.disc && this.discovering) {
      const d = await fleet.discovery(this.disc.cursor).catch(() => null);
      if (d && isCurrent()) {
        this.disc = mergeDiscovery(this.disc, d);
        refreshContext();
      }
    }
    if (s.targets.some((t) => t.install?.state === 'running' || t.install?.state === 'queued') || this.at === 'install') {
      const g = await fleet.addSession().catch(() => null);
      if (g?.session && isCurrent()) {
        const was = this.session?.targets.filter((t) => t.node).length ?? 0;
        this.set(g.session);
        if (g.session.targets.filter((t) => t.node).length > was) refreshFleet();
      }
    }
  }

  // ---- Find ------------------------------------------------------------------------------------
  /** the discovery list: read it; a first run starts at once, except where macOS asks first */
  private async find(): Promise<void> {
    const d = await fleet.discovery().catch(() => null);
    if (!d) return;
    this.disc = d;
    if (d.run === 0 && d.local_network.state === 'not_asked') {
      this.lanAsk = true;
      return;
    }
    if (d.run === 0) await this.startDiscovery();
    else this.discovering = true;
  }

  private async startDiscovery(): Promise<void> {
    this.lanAsk = false;
    try {
      this.disc = await fleet.discover();
      this.discovering = true;
    } catch (e) {
      this.err = { ...this.err, find: describeError(e).message };
    }
    refreshContext();
  }

  private pickedRow(r: DiscoveryRow): AddTarget | undefined {
    // a row's id follows its strongest key and may change as sightings join it: its address too
    return this.session?.targets.find((t) => t.row != null && (t.row === r.id || (!r.node?.virtual && r.addresses.includes(t.address))));
  }

  private async toggle(r: DiscoveryRow): Promise<void> {
    const s = this.session!;
    const t = this.pickedRow(r);
    await this.run(`row:${r.id}`, () => (t ? fleet.dropTarget(s.id, t.id) : fleet.addTarget(s.id, { row: r.id })));
  }

  private async byAddress(): Promise<void> {
    const a = this.addr.trim();
    if (!a) return;
    this.busy = 'addr';
    this.err = { ...this.err, addr: '' };
    try {
      await fleet.discoverAddress(a);
      const d = await fleet.discovery();
      this.disc = d;
      this.discovering = true;
      this.addr = '';
    } catch (e) {
      this.err = { ...this.err, addr: describeError(e).message };
    } finally {
      this.busy = '';
    }
  }

  private async readLine(): Promise<void> {
    this.err = { ...this.err, rent: '' };
    this.rentTarget = null;
    try {
      this.rentTarget = (await fleet.sshTarget(rentedLine(this.rent))).target;
    } catch (e) {
      this.err = { ...this.err, rent: describeError(e).message };
    }
  }

  private async addRented(): Promise<void> {
    const s = this.session!;
    const tgt = this.rentTarget;
    if (!tgt) return;
    const terms = this.rent.leaseEnd.trim() ? rentedTerms(this.rent) : null;
    if (typeof terms === 'string') {
      this.err = { ...this.err, rent: terms };
      return;
    }
    const ok = await this.run('rent', () => fleet.addTarget(s.id, { ssh: tgt, ...(terms ? { rented: terms } : {}), ...(this.rent.key.trim() ? { key: this.rent.key } : {}) }));
    if (ok) {
      this.rent = { ...EMPTY_RENT };
      this.rentTarget = null;
      this.manual = null;
    }
  }

  private async readKeyFile(e: Event): Promise<void> {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (f) this.rent = { ...this.rent, key: await f.text() };
  }

  private findStep(): TemplateResult {
    const d = this.disc;
    const s = this.session!;
    const ln = d?.local_network;
    const picked = s.targets;
    const rows = d?.rows ?? [];
    const sc = d?.scan;
    const lanCard = this.lanAsk
      ? html`<section class="panel is-warn wz-perm" data-perm="lan">
          <div class="wz-perm-text"><b>TandemApp needs Local Network access to find Sparks.</b><p class="small muted">${permissionRows('not_asked', 'default')[0].why} macOS asks once, for the controller’s app. Without it: ${permissionRows('denied', 'default')[0].without}</p></div>
          <div class="wz-row"><button type="button" class="btn btn-ghost btn-sm" data-act="lan-later" @click=${() => ((this.lanAsk = false), (this.manual = 'addr'))}>Not now</button><button type="button" class="btn btn-primary btn-sm" data-act="lan-go" @click=${() => void this.startDiscovery()}>Continue</button></div>
        </section>`
      : ln?.state === 'denied'
        ? html`<section class="panel is-warn wz-denied" data-perm="lan-denied">
            <p class="small"><b class="warn-ink">⚠ Local Network access is off.</b> ${ln.text}.</p>
            ${ln.settings ? html`<p class="small muted">Turn it on in <b>${ln.settings}</b>, then find again.</p>` : nothing}
            <div class="wz-row"><button type="button" class="btn btn-sm" data-act="denied-addr" @click=${() => (this.manual = 'addr')}>+ Add by address</button><button type="button" class="btn btn-sm" data-act="denied-rent" @click=${() => (this.manual = 'rent')}>+ Rented / remote (SSH)</button><button type="button" class="btn btn-ghost btn-sm" @click=${() => void this.startDiscovery()}>${icon('refresh')} Find again</button></div>
          </section>`
        : nothing;
    const scanCard =
      sc && sc.state === 'ask' && ln?.state !== 'denied' && !this.lanAsk
        ? html`<section class="panel wz-scan" data-scan="ask">
            <div class="wz-perm-text"><b data-scan-question>${sc.question}</b><p class="small muted">One connect to port 22 of each of its 254 addresses (${sc.timeout_ms} ms each, ${sc.rate_per_s} a second) and the banner the server sends; never a login, nothing outside this subnet.</p></div>
            <div class="wz-row"><button type="button" class="btn btn-ghost btn-sm" data-act="scan-no" @click=${() => (this.disc = { ...d!, scan: { ...sc, state: 'none' } })}>Skip</button><button type="button" class="btn btn-sm btn-primary" data-act="scan-yes" @click=${async () => (this.disc = mergeDiscovery(this.disc, await fleet.scan(sc.subnet!)))}>Scan ${sc.subnet}</button></div>
          </section>`
        : sc && sc.state === 'running'
          ? html`<section class="panel wz-scan" data-scan="running"><div class="wz-row wz-between"><span class="small">Scanning ${sc.subnet} for SSH… <span class="num">${sc.done}/${sc.total}</span> · ${sc.open} open</span><button type="button" class="btn btn-ghost btn-sm" data-act="scan-stop" @click=${async () => (this.disc = mergeDiscovery(this.disc, await fleet.stopScan()))}>Stop</button></div><div class="wz-prog"><i style="width:${sc.total ? Math.round((sc.done / sc.total) * 100) : 0}%"></i></div></section>`
          : nothing;
    const list = rows.map((r) => {
      const t = this.pickedRow(r);
      const can = !!r.connect.length && !r.fleet;
      return html`<label class="wz-found ${t ? 'is-sel' : ''} ${can ? '' : 'is-dis'}" data-row=${r.id} data-kind=${r.kind} data-ip=${r.addresses[0] ?? ''}>
        <input type="checkbox" .checked=${!!t} ?disabled=${!can || this.busy === `row:${r.id}`} @change=${() => void this.toggle(r)} aria-label=${`Pick ${r.name ?? r.addresses[0]}`} />
        <div class="wz-found-main"><div><b>${r.name ?? r.addresses[0]}</b> ${r.name ? html`<span class="num xs muted">${r.addresses.join(', ')}</span>` : nothing}</div>
          <div class="wz-what" data-what>${can ? nothing : html`<span class="faint">○ </span>`}<span class="wz-title">${r.title}</span>${r.detail ? html` · <span class="muted">${r.detail}</span>` : nothing}</div>
          ${this.err[`row:${r.id}`] ? html`<div class="xs err-ink">${this.err[`row:${r.id}`]}</div>` : nothing}</div>
        <div class="wz-src">${r.sources.map((x) => SOURCE[x] ?? x).join(' · ')}</div>
      </label>`;
    });
    const remote = picked.filter((t) => !t.row);
    const listening = this.discovering && d && (d.mdns.state === 'browsing' || sc?.state === 'running');
    return html`${lanCard}${scanCard}
      <section class="panel" data-panel="found">
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Found${rows.length ? html` <span class="muted num">· ${rows.length}</span>` : nothing}</h2><p class="panel-sub">mDNS (<code>_tandemnode._tcp</code>, Spark-like names) · the LAN scan · <code>~/.ssh/config</code>. Results arrive live; each says what the device is.</p></div>${listening ? html`<span class="xs muted wz-live">listening…</span>` : nothing}</header>
        <div class="wz-list">${list.length ? list : html`<p class="small muted">${this.lanAsk ? 'Nothing searched yet.' : 'Nothing yet.'}</p>`}</div>
        ${remote.length ? html`<div class="wz-list wz-remote">${remote.map((t) => html`<div class="wz-found is-sel" data-target=${t.id}><span class="wz-check">✓</span><div class="wz-found-main"><div><b>${t.label}</b> <span class="num xs muted">${t.detail}</span></div><div class="wz-what"><span class="wz-title">${t.title}</span>${t.rented ? html` · lease until ${day(t.rented.lease_end)}` : nothing} · through an SSH tunnel</div></div><button type="button" class="btn btn-ghost btn-sm" @click=${() => void this.run('drop', () => fleet.dropTarget(s.id, t.id))}>Remove</button></div>`)}</div>` : nothing}
        <div class="wz-row wz-manual"><button type="button" class="btn btn-sm ${this.manual === 'addr' ? 'is-on' : ''}" data-act="manual-addr" @click=${() => (this.manual = this.manual === 'addr' ? null : 'addr')}>+ Add by address</button><button type="button" class="btn btn-sm ${this.manual === 'rent' ? 'is-on' : ''}" data-act="manual-rent" @click=${() => (this.manual = this.manual === 'rent' ? null : 'rent')}>+ Rented / remote (SSH)</button></div>
      </section>
      ${this.manual === 'addr' ? this.addrForm() : this.manual === 'rent' ? this.rentForm() : nothing}`;
  }

  private addrForm(): TemplateResult {
    return html`<section class="panel" data-panel="by-address"><form class="wz-row wz-form" @submit=${(e: Event) => (e.preventDefault(), void this.byAddress())}>
      <label class="field wz-grow"><span>Address</span><input data-in="address" inputmode="decimal" placeholder="192.0.2.70" .value=${this.addr} @input=${(e: Event) => (this.addr = (e.target as HTMLInputElement).value)} /></label>
      <button type="submit" class="btn" ?disabled=${this.busy === 'addr' || !this.addr.trim()}>${this.busy === 'addr' ? 'Checking…' : 'Check it'}</button></form>
      <p class="xs muted">Reads that one address’s SSH banner and its TandemNode ports; it then shows in the list above. Works with Local Network off for a routed address; macOS may still block a LAN one.</p>
      ${this.err.addr ? html`<p class="small err-ink">${this.err.addr}</p>` : nothing}</section>`;
  }

  private rentForm(): TemplateResult {
    const r = this.rent;
    const t = this.rentTarget;
    const upd = (k: keyof RentedForm) => (e: Event) => {
      this.rent = { ...this.rent, [k]: (e.target as HTMLInputElement).value };
      if (k === 'line' || k === 'jump' || k === 'keyPath') this.rentTarget = null;
    };
    return html`<section class="panel" data-panel="rented">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Rented / remote Spark</h2><p class="panel-sub">No discovery: paste the provider’s SSH command. Always reached through an SSH tunnel to the node’s own loopback.</p></div></header>
      <div class="wz-grid">
        <label class="field field-block wz-wide"><span>SSH command from the provider</span><input class="num" data-in="ssh-line" placeholder="ssh -p 2201 spark@198.51.100.7" .value=${r.line} @input=${upd('line')} /></label>
        <label class="field field-block"><span>Key file (from this device)</span><input type="file" data-in="key-file" @change=${(e: Event) => void this.readKeyFile(e)} /><span class="xs muted">${r.key ? 'read: kept in the controller’s memory while you add, then as a 0600 file for the tunnel' : 'or name a file on the controller’s machine:'}</span></label>
        <label class="field field-block"><span>Key file on the controller (path)</span><input class="num" data-in="key-path" placeholder="~/.ssh/rent_ed25519" .value=${r.keyPath} @input=${upd('keyPath')} /></label>
        <label class="field field-block"><span>Jump host (optional)</span><input class="num" data-in="jump" placeholder="hop@198.51.100.1" .value=${r.jump} @input=${upd('jump')} /></label>
        <label class="field field-block"><span>Lease ends (rented)</span><input type="date" data-in="lease-end" .value=${r.leaseEnd} @input=${upd('leaseEnd')} /></label>
        <label class="field field-block"><span>Cost per hour (optional)</span><input data-in="cost" inputmode="decimal" placeholder="2.40" .value=${r.cost} @input=${upd('cost')} /></label>
      </div>
      <p class="xs muted">A reminder fires before the lease ends (the Lease ends rule, 24 h by default). Release later wipes weights, caches, keys and the node agent.</p>
      ${t
        ? html`<div class="wz-route" data-route>
            <span class="eyebrow">Route</span>
            <ol>${t.route.map((h) => html`<li><span class="wz-pill">${h.kind === 'jump' ? 'jump' : 'node'}</span> <span class="num">${h.user ? `${h.user}@` : ''}${h.host}:${h.port}</span></li>`)}</ol>
            <p class="xs muted">${t.remote ? 'remote: tunnelled' : 'a LAN address: still tunnelled as a rented node'}${t.identity_file ? html` · key <span class="num">${t.identity_file}</span>` : nothing}${t.warnings.length ? html` · <span class="warn-ink">${t.warnings.join('; ')}</span>` : nothing}</p>
          </div>`
        : nothing}
      ${this.err.rent ? html`<p class="small err-ink" data-err="rent">${this.err.rent}</p>` : nothing}
      <div class="wz-row">${t
        ? html`<button type="button" class="btn btn-primary" data-act="rent-add" ?disabled=${this.busy === 'rent'} @click=${() => void this.addRented()}>${r.leaseEnd ? 'Add as rented' : 'Add as remote'}</button><button type="button" class="btn btn-ghost" @click=${() => (this.rentTarget = null)}>Edit</button>`
        : html`<button type="button" class="btn" data-act="rent-read" ?disabled=${!r.line.trim()} @click=${() => void this.readLine()}>Read the line</button>`}</div>
    </section>`;
  }

  // ---- Connect ---------------------------------------------------------------------------------
  private draft(t: AddTarget): ConnectDraft {
    return (
      this.drafts[t.id] ?? {
        mode: t.connect.includes('code') ? 'code' : 'ssh',
        code: '',
        user: t.ssh.user ?? '',
        auth: t.secret.held && t.ssh.auth === 'key' ? 'key' : t.ssh.key_file ? 'key_file' : t.transport === 'tunnel' ? 'key' : 'password',
        password: '',
        key: '',
        keyPath: t.ssh.key_file ?? '',
      }
    );
  }
  private setDraft(t: AddTarget, p: Partial<ConnectDraft>): void {
    this.drafts = { ...this.drafts, [t.id]: { ...this.draft(t), ...p } };
  }

  private async connect(t: AddTarget): Promise<void> {
    const s = this.session!;
    const d = this.draft(t);
    if (d.mode === 'code') {
      if (!/^\d{6}$/.test(d.code)) {
        this.err = { ...this.err, [t.id]: 'Enter the 6 digits the node shows' };
        return;
      }
      await this.run(t.id, () => fleet.connectTarget(s.id, t.id, { method: 'code', code: d.code }));
      return;
    }
    if (!t.host_key) {
      await this.run(t.id, () => fleet.targetHostKey(s.id, t.id));
      return;
    }
    const auth: Record<string, string | boolean> = d.auth === 'password' ? { password: d.password } : d.auth === 'key' ? (d.key ? { key: d.key } : {}) : d.auth === 'key_file' ? { key_file: d.keyPath } : { agent: true };
    const ok = await this.run(t.id, () =>
      fleet.connectTarget(s.id, t.id, { method: t.kind === 'owned' ? 'takeover' : 'ssh', user: d.user || undefined, host_key: t.host_key!.fingerprint, ...auth }),
    );
    this.setDraft(t, { password: '', key: '' });
    if (!ok) return;
  }

  private connectStep(): TemplateResult {
    return html`${this.session!.targets.map((t) => this.connectCard(t))}`;
  }

  private connectCard(t: AddTarget): TemplateResult {
    const d = this.draft(t);
    const head = html`<header class="panel-head"><div class="panel-titles"><h2 class="panel-title">${t.label} <span class="num xs muted">${t.address}</span></h2><p class="panel-sub">${t.title}${t.detail ? ` · ${t.detail}` : ''}</p></div>${
      t.state === 'paired' ? html`<span class="wz-pill is-ok" data-state="paired">✓ paired · certificate pinned</span>` : t.state === 'bare' ? html`<span class="wz-pill is-warn" data-state="bare">reached · nothing installed</span>` : nothing
    }</header>`;
    if (t.state === 'paired')
      return html`<section class="panel" data-target=${t.id}>${head}<p class="small muted">${t.via === 'code' ? 'Paired with the code. No password was used.' : 'Paired over SSH: the host key was confirmed, the password used once and not kept.'}</p></section>`;
    if (t.state === 'bare')
      return html`<section class="panel" data-target=${t.id}>${head}<p class="small muted">SSH reached ${loginWords(t)} and found TandemNode not installed${t.hw ? ` on this ${t.hw}` : ''}. Install puts TandemNode and the engine there at TandemLLM ${this.session!.fleet_version}.</p>${t.secret.held ? html`<p class="xs faint" data-secret="held">The ${t.ssh.auth === 'key' ? 'key' : 'password'} is held in the controller’s memory for the Check and Install steps only (until ${clock(t.secret.until!)}), never written anywhere.</p>` : nothing}</section>`;
    const modes = t.connect.filter((m) => m !== 'takeover' || t.kind === 'owned');
    const tabs = modes.length > 1 ? html`<div class="seg wz-seg">${modes.map((m) => html`<button type="button" class="seg-btn ${d.mode === m ? 'is-on' : ''}" data-mode=${m} @click=${() => this.setDraft(t, { mode: m === 'code' ? 'code' : 'ssh' })}>${m === 'code' ? 'Pairing code' : 'SSH'}</button>`)}</div>` : nothing;
    const err = this.err[t.id] || t.error?.message;
    let inner: TemplateResult;
    if (d.mode === 'code') {
      inner = html`<p class="small muted">Run <code>tandem node pair</code> on the node. It shows 6 digits for 5 minutes, 5 tries.</p>
        <form class="wz-row wz-form" @submit=${(e: Event) => (e.preventDefault(), void this.connect(t))}>
          <label class="field"><span class="sr-only">Pairing code</span><input class="wz-code num" data-in="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000" .value=${d.code} @input=${(e: Event) => this.setDraft(t, { code: (e.target as HTMLInputElement).value.replace(/\D/g, '').slice(0, 6) })} /></label>
          <button type="submit" class="btn btn-primary" data-act="pair" ?disabled=${this.busy === t.id}>${this.busy === t.id ? 'Pairing…' : 'Pair'}</button>
        </form>`;
    } else if (!t.host_key) {
      inner = html`<p class="small muted">${t.kind === 'owned' ? 'Another TandemApp owns it. Taking it over needs that owner, or an SSH login here: SSH access proves the node is yours.' : `SSH to ${loginWords(t)}.`} First the host key, as ssh shows it on a first connection.</p>
        <div class="wz-row"><button type="button" class="btn btn-primary" data-act="hostkey" ?disabled=${this.busy === t.id} @click=${() => void this.connect(t)}>${this.busy === t.id ? 'Reading…' : 'Read its host key'}</button></div>`;
    } else if (!t.host_key.confirmed) {
      const hk = t.host_key;
      inner = html`<div class="wz-hostkey" data-hostkey=${hk.known}>
          <p class="small"><b>${hk.known === 'changed' ? '⚠ The host key changed.' : hk.known === 'same' ? 'The host key this controller knows.' : 'Check the host key.'}</b> <span class="muted">${hk.known === 'new' ? `First connection to ${t.address}. Compare it on the node: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` : hk.known === 'changed' ? `It was ${hk.stored}. A changed key can mean a different machine at that address: refused.` : ''}</span></p>
          <div class="wz-fp num" data-fp>${hk.type.replace('ssh-', '').toUpperCase()} ${hk.fingerprint}</div>
        </div>
        ${hk.known === 'changed' ? nothing : this.sshForm(t, d)}`;
    } else inner = this.sshForm(t, d);
    return html`<section class="panel" data-target=${t.id}>${head}${tabs}${inner}${err ? html`<p class="small err-ink" data-err=${t.id}>${err}</p>` : nothing}</section>`;
  }

  private sshForm(t: AddTarget, d: ConnectDraft): TemplateResult {
    const tunnel = t.transport === 'tunnel';
    const upd = (k: keyof ConnectDraft) => (e: Event) => this.setDraft(t, { [k]: (e.target as HTMLInputElement).value });
    const auths: { id: Auth; label: string }[] = [
      ...(tunnel ? [] : [{ id: 'password' as Auth, label: 'Password, once' }]),
      { id: 'key_file', label: 'Key file on the controller' },
      { id: 'key', label: t.secret.held && t.ssh.auth === 'key' ? 'The key given in Find' : 'Paste a key' },
      { id: 'agent', label: 'The controller’s ssh-agent' },
    ];
    return html`<form class="wz-grid wz-ssh" @submit=${(e: Event) => (e.preventDefault(), void this.connect(t))}>
        <label class="field field-block"><span>User</span><input data-in="user" autocomplete="username" required placeholder="the login on the node" .value=${d.user} @input=${upd('user')} /></label>
        <label class="field field-block"><span>Sign in with</span><select data-in="auth" @change=${(e: Event) => this.setDraft(t, { auth: (e.target as HTMLSelectElement).value as Auth })}>${auths.map((a) => html`<option value=${a.id} ?selected=${d.auth === a.id}>${a.label}</option>`)}</select></label>
        ${d.auth === 'password'
          ? html`<label class="field field-block"><span>Password</span><input type="password" data-in="password" autocomplete="current-password" .value=${d.password} @input=${upd('password')} /></label>`
          : d.auth === 'key_file'
            ? html`<label class="field field-block"><span>Path</span><input class="num" data-in="key-path" placeholder="~/.ssh/id_ed25519" .value=${d.keyPath} @input=${upd('keyPath')} /></label>`
            : d.auth === 'key' && !(t.secret.held && t.ssh.auth === 'key')
              ? html`<label class="field field-block wz-wide"><span>Private key</span><textarea data-in="key" rows="3" .value=${d.key} @input=${upd('key')}></textarea></label>`
              : html`<span></span>`}
        <div class="wz-wide wz-row"><button type="submit" class="btn btn-primary" data-act="connect" ?disabled=${this.busy === t.id || !d.user.trim() || (d.auth === 'password' && !d.password)}>${this.busy === t.id ? 'Connecting…' : t.host_key?.confirmed ? 'Connect' : t.kind === 'owned' ? 'It matches: take it over' : 'It matches: connect'}</button>${t.host_key && !t.host_key.confirmed ? html`<button type="button" class="btn btn-ghost" data-act="no-match" @click=${() => (this.err = { ...this.err, [t.id]: 'Stopped. A host key that does not match can mean a different machine at that address.' })}>It does not match</button>` : nothing}</div>
      </form>
      <p class="xs muted">${d.auth === 'password' ? 'The password is used for this login and, when nothing is installed there, kept in the controller’s memory for Check and Install (30 minutes at most), then forgotten. It is never stored.' : d.auth === 'key_file' ? 'ssh reads the file where it is, on the controller’s machine; nothing copies it.' : d.auth === 'key' ? (tunnel ? 'A rented node’s tunnel logs in with this key for as long as the node is in the fleet: it is kept as a 0600 file beside the controller’s certificate, removed when the node is released.' : 'The key is used for this add only, in memory.') : 'The controller’s own ssh-agent and keys.'}</p>`;
  }

  // ---- Check -----------------------------------------------------------------------------------
  private async check(tid?: string): Promise<void> {
    const s = this.session!;
    const secrets = tid && this.secrets[tid] ? { [tid]: { password: this.secrets[tid] } } : undefined;
    await this.run('check', () => fleet.checkAdd(s.id, { ...(tid ? { targets: [tid] } : {}), ...(secrets ? { secrets } : {}) }));
    if (tid) this.secrets = { ...this.secrets, [tid]: '' };
  }

  private checkStep(): TemplateResult {
    const s = this.session!;
    const none = s.targets.every((t) => !t.preflight);
    return html`${none
        ? html`<section class="panel wz-intro"><p class="small">Check reads each device: OS, kernel, driver, CUDA, disk, memory, TandemLLM’s version, ConnectX-7 and its links, sudo and the clock. A paired Spark is read through its TandemNode agent, a bare box over the same SSH login.</p><div class="wz-row"><button type="button" class="btn btn-primary" data-act="check" ?disabled=${this.busy === 'check'} @click=${() => void this.check()}>${this.busy === 'check' ? 'Checking…' : 'Check'}</button></div>${this.err.check ? html`<p class="small err-ink">${this.err.check}</p>` : nothing}</section>`
        : nothing}
      ${s.targets.filter((t) => t.preflight).map((t) => this.checkCard(t))}`;
  }

  private checkCard(t: AddTarget): TemplateResult {
    const p = t.preflight!;
    const v = p.verdict;
    const need = p.error?.type === 'secret_needed';
    return html`<section class="panel" data-target=${t.id} data-check=${v ? (v.fails ? 'red' : 'ready') : 'error'}>
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">${t.label}</h2><p class="panel-sub">${v ? html`${v.fails ? html`<span class="err-ink">${v.fails} to fix</span>` : html`<span class="ok-ink">ready</span>`}${v.warns ? html` · <span class="warn-ink">${v.warns} warning${v.warns > 1 ? 's' : ''}</span>` : nothing} · ${p.rows.length} checks · ${p.via === 'agent' ? 'through its agent' : 'over SSH'} · ${clock(p.at)}` : nothing}</p></div>
        <button type="button" class="btn btn-ghost btn-sm" data-act="recheck" ?disabled=${this.busy === 'check'} @click=${() => void this.check(t.id)}>${icon('refresh')} Check again</button></header>
      ${need
        ? html`<form class="wz-row wz-form" @submit=${(e: Event) => (e.preventDefault(), void this.check(t.id))}><p class="small warn-ink wz-wide">${p.error!.message}</p><label class="field"><span>Password for ${t.ssh.user}</span><input type="password" data-in="again" .value=${this.secrets[t.id] ?? ''} @input=${(e: Event) => (this.secrets = { ...this.secrets, [t.id]: (e.target as HTMLInputElement).value })} /></label><button class="btn" type="submit">Check</button></form>`
        : p.error
          ? html`<p class="small err-ink">${p.error.message}</p>`
          : nothing}
      <div class="wz-pf">${p.rows.map(
        (r) => html`<div class="wz-pf-row is-${r.state}" data-pf=${r.id} data-state=${r.state}>
          <span class="g" aria-hidden="true">${GLYPH[r.state]}</span><span class="sr-only">${r.state}</span><span class="wz-pf-label">${r.label}</span>
          <div class="wz-pf-val"><span class="num small">${r.value}</span>${r.fix ? html`<div class="wz-fix small">${r.fix}</div>` : nothing}</div>
        </div>`,
      )}</div>
      ${v?.install === 'refused' ? html`<p class="small err-ink wz-refused" data-refused>Install refuses TandemLLM ${v.why.split(' ')[0]}: every node runs the fleet’s version, ${this.session!.fleet_version}.</p>` : nothing}
    </section>`;
  }

  // ---- Install ---------------------------------------------------------------------------------
  private installStep(): TemplateResult {
    const s = this.session!;
    const idle = s.targets.some((t) => t.state === 'bare' && (!t.install || t.install.state === 'failed'));
    return html`<section class="panel" data-panel="install">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Installing at the fleet’s version</h2><p class="panel-sub">Every member runs TandemLLM ${s.fleet_version}: TandemNode and the engine’s runtime, never the weights (Serve a model brings those). A Spark with it already is skipped.</p></div>
        ${idle ? html`<button type="button" class="btn btn-primary" data-act="install" ?disabled=${this.busy === 'install'} @click=${() => void this.run('install', () => fleet.installAdd(s.id))}>${s.targets.some((t) => t.install?.state === 'failed') ? 'Install again' : 'Install'}</button>` : nothing}</header>
      ${this.err.install ? html`<p class="small err-ink">${this.err.install}</p>` : nothing}
      <div class="wz-inst">${s.targets.map((t) => this.installRow(t))}</div>
    </section>`;
  }

  private installRow(t: AddTarget): TemplateResult {
    const v = t.install;
    const pc = installPercent(v);
    const open = this.showLog[t.id];
    return html`<div class="wz-inst-row" data-target=${t.id} data-install=${v?.state ?? 'todo'}>
      <div class="wz-row wz-between"><b class="small">${t.label}</b><span class="num xs muted" data-words>${pc} % · ${installWords(v)}</span></div>
      <div class="wz-prog ${v?.state === 'done' || v?.state === 'skip' ? 'is-done' : v?.state === 'failed' ? 'is-err' : ''}" role="progressbar" aria-valuenow=${pc} aria-valuemin="0" aria-valuemax="100" aria-label=${`${t.label} install`}><i style="width:${pc}%"></i></div>
      ${v?.steps
        ? html`<ol class="wz-steps-mini">${v.steps.map((x) => html`<li class="is-${x.state}" data-step=${x.id}><span aria-hidden="true">${x.state === 'done' ? '✓' : x.state === 'running' ? '●' : x.state === 'failed' ? '✕' : x.state === 'skip' ? '–' : '○'}</span> ${x.label}${x.state === 'skip' ? html` <span class="faint">(skipped)</span>` : nothing}</li>`)}</ol>
            <button type="button" class="btn btn-ghost btn-sm" data-act="log" @click=${() => (this.showLog = { ...this.showLog, [t.id]: !open })}>${open ? 'Hide log' : 'Show log'}</button>
            ${open ? html`<div class="wz-log" data-log>${(v.log ?? []).map((l) => html`<div class="wz-log-l"><span class="num faint">${clock(l.t)}</span><span class="num">${l.step}</span><span class=${l.level === 'warn' ? 'warn-ink' : ''}>${l.text}</span></div>`)}</div>` : nothing}`
        : nothing}
    </div>`;
  }

  // ---- Link ------------------------------------------------------------------------------------
  private linkStep(): TemplateResult {
    const s = this.session!;
    const why = skipped(s, 'link');
    if (why) return html`<section class="panel" data-panel="link" data-link="skipped"><h2 class="panel-title">No link to set up</h2><p class="small muted">${why}.</p></section>`;
    const nodes = s.targets.filter((t) => t.node && t.transport === 'lan');
    const byId = new Map((this.fleetState?.nodes ?? []).map((n) => [n.id, n]));
    return html`<section class="panel" data-panel="link" data-link=${s.link.state}>
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Fabric between the new Sparks</h2><p class="panel-sub">Their ConnectX-7 ports as Check read them; each link tested from the controller.</p></div></header>
      <div class="wz-fab">${nodes.map((t) => html`<div class="wz-fab-node">${byId.get(t.node!) ? nodeChip(byId.get(t.node!)!, { status: false }) : html`<b>${t.label}</b>`}<span class="num small">${t.preflight?.rows.find((r) => r.id === 'cx7')?.value ?? 'ConnectX-7 not read'}</span></div>`)}</div>
      <p class="small muted">Configuring the fabric sets link addresses and MTU 9000 on both Sparks and needs sudo on each, asked per action, never stored. It comes with the rented pair (step 7); until then set the link up by hand and test it here.</p>
      <div class="wz-row"><button type="button" class="btn" data-act="link-sudo" disabled title="comes with step 7">Configure link (sudo)…</button><button type="button" class="btn btn-primary" data-act="link-test" ?disabled=${this.busy === 'link'} @click=${() => void this.run('link', () => fleet.linkAdd(s.id, 'test'))}>${this.busy === 'link' ? 'Testing…' : s.link.state === 'done' ? 'Test again' : 'Test the links'}</button><button type="button" class="btn btn-ghost" data-act="link-skip" @click=${() => void this.run('link', () => fleet.linkAdd(s.id, 'skip'))}>Skip for now</button></div>
      ${this.err.link ? html`<p class="small err-ink">${this.err.link}</p>` : nothing}
      ${s.link.results.length
        ? html`<div class="wz-link-res">${s.link.results.map((r) => {
            const n = byId.get(r.node);
            return html`<div class="wz-row" data-result=${r.node}>${n ? nodeChip(n, { status: false }) : html`<b>${r.node}</b>`}${r.test ? html`<span class="wz-pill is-ok">✓ ${r.test.gbps ?? '—'} Gb/s</span><span class="num small">RTT ${r.test.rtt_ms ?? '—'} ms</span><span class="xs muted">${r.test.note ?? ''}</span>` : html`<span class="small err-ink">${r.error?.message}</span>`}</div>`;
          })}</div>`
        : nothing}
      ${s.link.state === 'skipped' ? html`<p class="small muted">${s.link.why}</p>` : nothing}
    </section>`;
  }

  // ---- Name ------------------------------------------------------------------------------------
  /** each new node's draft, from the node as the fleet knows it (its alias and the colour pairing gave
   *  it); a node the fleet store has not read yet waits for its next answer */
  private seedNames(): void {
    const s = this.session;
    if (!s) return;
    const nodes = this.fleetState?.nodes ?? [];
    let next: Record<string, NameDraft> | null = null;
    let missing = false;
    for (const t of s.targets) {
      if (!t.node || this.names[t.id]) continue;
      const n = nodes.find((x) => x.id === t.node);
      if (!n) {
        missing = true;
        continue;
      }
      next = { ...(next ?? this.names), [t.id]: nameDraft(t, n) };
    }
    if (next) this.names = next;
    if (missing) refreshFleet();
  }

  private nameStep(): TemplateResult {
    const s = this.session!;
    const nodes = this.fleetState?.nodes ?? [];
    const errs = nameErrors(s, this.names, nodes);
    if (s.targets.some((t) => t.node && !this.names[t.id])) return html`<div class="skeleton" style="height:220px"></div>`;
    return html`${s.targets
      .filter((t) => t.node)
      .map((t) => {
        const d = this.names[t.id];
        if (!d) return nothing;
        const others = [...nodes.filter((n) => n.id !== t.node), ...s.targets.filter((x) => x.node && x.id !== t.id && this.names[x.id]).map((x) => ({ id: x.node!, alias: this.names[x.id].alias, colour: this.names[x.id].colour }))];
        const upd = (p: Partial<NameDraft>) => (this.names = { ...this.names, [t.id]: { ...d, ...p } });
        return html`<section class="panel" data-target=${t.id} data-name>
          <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">${t.label}</h2><p class="panel-sub">${t.hw ?? t.title}</p></div>${nodeChip({ id: t.node!, alias: d.alias || '…', colour: d.colour, hw: t.hw ?? '' }, { lg: true, status: false })}</header>
          <div class="wz-grid">
            <label class="field field-block"><span>Alias</span><input data-in="alias" maxlength="40" .value=${d.alias} @input=${(e: Event) => upd({ alias: (e.target as HTMLInputElement).value })} /></label>
            <label class="field field-block"><span>Tags</span><input data-in="tags" placeholder="desk, owned" .value=${d.tags} @input=${(e: Event) => upd({ tags: (e.target as HTMLInputElement).value })} /></label>
            <div class="field field-block"><span>Colour</span><div class="wz-swatches" role="radiogroup" aria-label="Colour">${colourChoices(others, t.node!).map(
              (c) => html`<button type="button" role="radio" aria-checked=${d.colour === c.colour} class="wz-sw ${isHollow(c.colour) ? 'is-hollow' : ''} ${d.colour === c.colour ? 'is-on' : ''}" style="--c:${nodeColour(c.colour)}" data-colour=${c.colour} ?disabled=${!!c.usedBy} title=${c.usedBy ? `used by ${c.usedBy}` : `colour ${c.colour}`} aria-label=${`colour ${c.colour}${c.usedBy ? `, used by ${c.usedBy}` : ''}`} @click=${() => upd({ colour: c.colour })}></button>`,
            )}</div><span class="xs faint">1–4 are the four hues, 5–8 the same drawn hollow with dashed chart lines. A colour another Spark has is taken.</span></div>
            <label class="field field-block"><span>Icon</span><select data-in="icon" @change=${(e: Event) => upd({ icon: (e.target as HTMLSelectElement).value as NameDraft['icon'] })}>${ICONS.map((i) => html`<option value=${i.id} ?selected=${d.icon === i.id}>${i.label}</option>`)}</select></label>
          </div>
          ${errs[t.id] ? html`<p class="small err-ink" data-err=${t.id}>${errs[t.id]}</p>` : nothing}
        </section>`;
      })}`;
  }

  // ---- Done ------------------------------------------------------------------------------------
  private async readTopology(): Promise<void> {
    this.topo = await fleet.topology().catch(() => null);
  }

  private doneStep(): TemplateResult {
    const s = this.session!;
    const nodes = this.fleetState?.nodes ?? [];
    const added = s.targets.map((t) => nodes.find((n) => n.id === t.node)).filter((n): n is NonNullable<typeof n> => !!n);
    return html`<section class="panel wz-done" data-panel="done">
        <div class="wz-row wz-between"><div><h2 class="panel-title"><span class="ok-ink">✓</span> ${added.length || s.targets.length} Spark${s.targets.length > 1 ? 's' : ''} added</h2><p class="panel-sub">${added.map((n) => nodeChip(n, { status: true }))} ${added.length > 1 ? 'report' : 'reports'} metrics now. Nothing is served on ${added.length > 1 ? 'them' : 'it'} yet.</p></div>
          <div class="wz-row"><a class="btn" href="#/sparks">Open Sparks</a><a class="btn btn-primary" data-act="serve" href="#/models/place">Serve a model</a></div></div>
        ${s.reminders.length
          ? html`<div class="wz-remind" data-reminders>${s.reminders.map((r) => {
              const n = nodes.find((x) => x.id === r.node);
              return html`<p class="small" data-reminder=${r.node}>${icon('info')} <span>Reminder <b>${day(r.at)}</b>, ${r.hours} h before the lease of ${n ? nodeChip(n, { status: false }) : r.node} ends on ${day(r.lease_end)}. It shows in Events &amp; Alerts${this.notify === 'granted' && systemOn() ? ' and as a system notification' : ''}.</span></p>`;
            })}
            ${this.notify === 'default' || (this.notify === 'granted' && !systemOn())
              ? html`<div class="wz-row wz-perm-inline" data-perm="notify"><span class="xs muted">${permissionRows(null, 'default')[1].why}</span><button type="button" class="btn btn-sm" data-act="notify" @click=${() => void this.askNotify()}>Turn on notifications</button></div>`
              : this.notify === 'denied'
                ? html`<p class="xs muted" data-perm="notify-denied">Notifications are off for this site in the browser: the reminder shows in Events &amp; Alerts only.</p>`
                : nothing}</div>`
          : nothing}
      </section>
      <section class="panel" data-panel="topology">
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">The fleet with the new Spark${added.length > 1 ? 's' : ''}</h2><p class="panel-sub">New: ${added.map((n) => n.alias).join(', ') || '…'}.</p></div></header>
        ${this.topo ? html`<qse-topology class="wz-topo" .topo=${this.topo} .deployments=${this.fleetState?.deployments ?? []} .selected=${added[0] ? `node:${added[0].id}` : ''}></qse-topology>` : html`<div class="skeleton" style="height:220px"></div>`}
      </section>
      <div class="wz-row"><button type="button" class="btn btn-ghost" data-act="again" @click=${async () => ((this.names = {}), (this.drafts = {}), this.set(await fleet.startAdd()), await this.find())}>Add another Spark</button></div>`;
  }

  private async askNotify(): Promise<void> {
    if (this.notify === 'default') {
      const got = await Notification.requestPermission().catch(() => 'denied' as NotificationPermission);
      this.notify = got;
      if (got === 'granted') setSystemOn(true);
    } else if (this.notify === 'granted') setSystemOn(true);
    this.requestUpdate();
    refreshContext();
  }

  // ---- the page --------------------------------------------------------------------------------
  private context(): ContextContent {
    const s = this.session;
    const at = s ? this.at : 'find';
    const i = ADD_STEPS.indexOf(at);
    const perms = permissionRows(this.disc?.local_network.state ?? null, this.notify);
    return {
      title: 'Add Spark',
      body: html`<section class="csec"><div class="csec-h"><span class="eyebrow">Progress</span></div>
          <ol class="wz-ctx-steps">${ADD_STEPS.map((st, k) => {
            const sk = s ? skipped(s, st) : null;
            return html`<li class=${sk && k < ADD_STEPS.length - 1 ? 'is-skip' : k < i ? 'is-done' : k === i ? 'is-on' : ''}>${k < i && !sk ? '✓ ' : ''}${STEP_LABEL[st]}${sk && k < ADD_STEPS.length - 1 ? html` <span class="faint">skipped</span>` : nothing}</li>`;
          })}</ol>
          <p class="xs faint">Kept on the controller: leave at any step and come back here or from another device.</p></section>
        <section class="csec"><div class="csec-h"><span class="eyebrow">Permissions</span></div>
          ${perms.map((p) => html`<div class="wz-ctx-perm" data-ctx-perm=${p.id}><div class="wz-row wz-between"><b class="small">${p.name}</b><span class="xs ${p.ok === true ? 'ok-ink' : p.ok === false ? 'warn-ink' : 'muted'}">${p.state}</span></div><p class="xs muted">${p.why}</p>${p.ok === false || p.id === 'keychain' ? html`<p class="xs faint">Without it: ${p.without}</p>` : nothing}</div>`)}
        </section>
        ${s?.targets.length ? html`<section class="csec"><div class="csec-h"><span class="eyebrow">This add</span></div><dl class="kv">${s.targets.map((t) => html`<div><dt>${t.label}</dt><dd>${t.node ? 'paired' : t.state === 'bare' ? 'to install' : 'to connect'}</dd></div>`)}</dl></section>` : nothing}`,
    };
  }

  private stepper(at: AddStep): TemplateResult {
    const s = this.session!;
    const i = ADD_STEPS.indexOf(at);
    return html`<nav class="wz-stepper" aria-label="Add Spark steps">${ADD_STEPS.map((st, k) => {
      const sk = k < ADD_STEPS.length - 1 ? skipped(s, st) : null;
      const reach = k < i && s.state === 'active';
      return html`<button type="button" class="wz-step ${k === i ? 'is-on' : k < i ? 'is-done' : ''} ${sk ? 'is-skip' : ''}" data-step=${st} aria-current=${k === i ? 'step' : 'false'} ?disabled=${!reach || !!sk} title=${sk ?? ''} @click=${() => void this.go(st)}><span class="n">${k < i && !sk ? '✓' : sk ? '–' : k + 1}</span><span class="l">${STEP_LABEL[st]}</span></button>`;
    })}</nav>`;
  }

  render() {
    const head = html`<header class="page-head wz-head" data-page="add-spark"><div><div class="eyebrow">Settings</div><h1>Add Spark</h1><p class="page-sub">Progress is kept on the controller. Leave at any step and resume here or from another device.</p></div>${
      this.session && this.session.state === 'active' && this.session.targets.length ? html`<button type="button" class="btn btn-ghost btn-sm" data-act="restart" @click=${async () => ((this.names = {}), (this.drafts = {}), this.set(await fleet.startAdd(true)), await this.find())}>Start over</button>` : nothing
    }</header>`;
    if (this.loadErr === 'sign-in' || (!this.session && needsSignIn())) return html`${head}${signInCard('Adding a Spark')}`;
    if (this.loadErr) return html`${head}<p class="page-soon">${icon('info')} <span>${this.loadErr}</span></p>`;
    const s = this.session;
    if (!s) return html`${head}<div class="skeleton" style="height:240px"></div>`;
    const at = this.at;
    const body = { find: () => this.findStep(), connect: () => this.connectStep(), check: () => this.checkStep(), install: () => this.installStep(), link: () => this.linkStep(), name: () => this.nameStep(), done: () => this.doneStep() }[at]();
    const g = gate(s, at, at === 'name' ? this.names : undefined, this.fleetState?.nodes ?? []);
    const nx = nextStep(s, at);
    const pv = prevStep(s, at);
    const jumped = at !== 'find' ? skippedBetween(s, pv, at) : [];
    const ahead = at !== 'done' ? skippedBetween(s, at, nx) : [];
    return html`${head}${this.stepper(at)}
      ${needsSignIn() ? signInCard('Pairing and installing') : nothing}
      ${jumped.length ? html`<p class="wz-skipnote small muted" data-skipped>${jumped.map((x) => html`<span><b>${STEP_LABEL[x.step]} skipped:</b> ${x.why}.</span> `)}</p>` : nothing}
      <div class="wz-body" data-at=${at}>${body}</div>
      ${at !== 'done'
        ? html`<footer class="wz-foot">
            <button type="button" class="btn btn-ghost" data-act="back" ?disabled=${at === 'find' || this.busy === 'next'} @click=${() => void this.go(pv)}>Back</button>
            <span class="xs muted wz-why" data-why>${g === true ? (ahead.length ? `Next skips ${ahead.map((x) => STEP_LABEL[x.step]).join(' and ')}: ${ahead[0].why}` : '') : g}${this.err.next ? html`<span class="err-ink"> ${this.err.next}</span>` : nothing}</span>
            <button type="button" class="btn btn-primary" data-act="next" ?disabled=${g !== true || this.busy === 'next'} @click=${() => void this.go(nx)}>${at === 'name' ? 'Finish' : `Next: ${STEP_LABEL[nx]}`}</button>
          </footer>`
        : nothing}`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-add-spark': QseAddSpark;
  }
}
