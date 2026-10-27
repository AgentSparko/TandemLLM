// What the right sidebar shows on each page that brings no content of its own (VIS-256, the v3
// prototype's right sidebars), from what the shell already reads: the fleet (Sparks, deployments,
// alerts and rates), the summary, the engine's version and this browser's layouts. Chat brings its own
// (its Context panel), and a page may still register its own (lib/context.ts). Every figure is the
// server's; a server without TandemApp's controller gets the box's own and says what is missing.

import { html, nothing, type TemplateResult } from 'lit';
import { fleet, type FleetAlert, type FleetDeployment } from '../api/fleet';
import type { Summary } from '../api/types';
import type { ContextContent } from '../lib/context';
import { activeAlerts, refreshFleet, type FleetState } from '../lib/fleet-state';
import { compact, duration, shortHash } from '../lib/format';
import { menuItem, type Section } from '../lib/menu';
import type { Route } from '../lib/router';
import type { Theme } from '../lib/theme';
import { resetAll } from '../lib/workspace';
import { icon } from './bits';
import { nodeChip } from './node-chip';

export interface PageData {
  route: Route;
  fleet: FleetState | null;
  summary: Summary | null;
  meta: { version: string | null; code: string | null };
  layouts: { page: string; device: string; windows: number; saved_at: number | null }[];
  theme: Theme;
  toggleTheme(): void;
}

const sec = (title: string | TemplateResult, body: TemplateResult | typeof nothing, tools?: TemplateResult) =>
  html`<section class="csec"><div class="csec-h"><span class="eyebrow">${title}</span>${tools ?? nothing}</div>${body}</section>`;

const NO_FLEET = html`<p class="muted small">The fleet shows here once TandemApp’s controller answers on this server.</p>`;

/** every Spark with what it serves and its rate now */
function sparks(f: FleetState | null): TemplateResult {
  if (!f) return NO_FLEET;
  return html`<div class="ctx-nodes">
    ${f.nodes.map(
      (n) => html`<a class="mini-now" href=${`#/sparks/${encodeURIComponent(n.id)}`} data-node=${n.id}>
        <span>${nodeChip(n, { status: true })}<span class="mini-dep">${f.deployments.filter((d) => d.members.includes(n.id)).map((d) => d.name).join(' · ')}</span></span>
        <span class="num small">${f.tps[n.id] != null ? Math.round(f.tps[n.id]) : '—'} <span class="muted">tok/s</span></span>
      </a>`,
    )}
  </div>`;
}

function alerts(f: FleetState | null): TemplateResult {
  if (!f) return NO_FLEET;
  const a = activeAlerts(f);
  if (!a.length) return html`<p class="small ok-ink">No active alerts.</p>`;
  return html`<div class="ctx-alerts">${a.map((x) => alertCard(x, f))}</div>`;
}

function alertCard(a: FleetAlert, f: FleetState): TemplateResult {
  const n = f.nodes.find((x) => x.id === a.node);
  return html`<div class="ctx-alert is-${a.severity}" data-alert=${a.id}>
    <b class="small">${a.severity === 'err' ? '⚠' : '◐'} ${a.title}</b>
    <p class="muted small">${n ? nodeChip(n, { status: false }) : nothing} ${a.detail}</p>
    <div class="ctx-alert-acts">
      <button type="button" class="btn btn-sm" @click=${async (e: Event) => {
        (e.currentTarget as HTMLButtonElement).disabled = true;
        await fleet.ack(a.id).catch(() => undefined);
        refreshFleet();
      }}>Acknowledge</button>
      ${a.action ? html`<a class="small" href=${a.action.route}>${a.action.label}</a>` : nothing}
    </div>
  </div>`;
}

function deployments(list: FleetDeployment[], f: FleetState): TemplateResult {
  return html`<div class="ctx-deps">${list.map(
    (d) => html`<div class="ctx-dep" data-dep=${d.id}>
      <div class="ctx-dep-h"><b class="small">${d.name}</b><span class="tag">${d.type}</span></div>
      <div class="ctx-dep-m">${d.members.map((m) => {
        const n = f.nodes.find((x) => x.id === m);
        return n ? nodeChip(n, { status: false }) : html`<span class="small">${m}</span>`;
      })}</div>
      <div class="small ${d.status === 'serving' ? 'muted' : 'warn-ink'}">${d.status_text}</div>
    </div>`,
  )}</div>`;
}

const endpoint = () => `${location.origin}/v1`;

function copyField(text: string): TemplateResult {
  return html`<div class="ctx-copy"><code class="small">${text}</code><button type="button" class="btn btn-ghost btn-sm" @click=${(e: Event) => {
    void navigator.clipboard?.writeText(text);
    (e.currentTarget as HTMLElement).textContent = 'Copied';
  }}>Copy</button></div>`;
}

function today(s: Summary | null): TemplateResult {
  if (!s) return html`<p class="muted small">The summary loads with the page.</p>`;
  const t = s.windows.today;
  return html`<dl class="kv">
    <div><dt>requests</dt><dd class="num">${compact(t.requests)}</dd></div>
    <div><dt>tokens in</dt><dd class="num">${compact(t.prompt_tokens)}</dd></div>
    <div><dt>of it cached</dt><dd class="num">${compact(t.cached_tokens)}</dd></div>
    <div><dt>tokens out</dt><dd class="num">${compact(t.completion_tokens)}</dd></div>
    <div><dt>decode p50</dt><dd class="num">${t.decode_tps_p50 == null ? '—' : `${Math.round(t.decode_tps_p50)} tok/s`}</dd></div>
  </dl>`;
}

function box(d: PageData): TemplateResult {
  const live = d.summary?.live;
  return html`<dl class="kv">
    <div><dt>version</dt><dd>${d.meta.version ?? '—'}</dd></div>
    <div><dt>code</dt><dd class="num">${d.meta.code ? shortHash(d.meta.code) : '—'}</dd></div>
    <div><dt>uptime</dt><dd class="num">${live ? duration(live.uptime_s) : '—'}</dd></div>
    <div><dt>running now</dt><dd class="num">${live ? `${live.running} running · ${live.waiting} waiting` : '—'}</dd></div>
  </dl>`;
}

const STEPS = ['Find', 'Connect', 'Check', 'Install', 'Link', 'Name', 'Done'];

/** The sidebar of a page: its title and its sections. */
export function pageContext(section: Section, d: PageData): ContextContent {
  const f = d.fleet;
  switch (section) {
    case 'overview':
      return { title: 'Fleet now', body: html`${sec('Alerts', alerts(f), html`<a class="small" href="#/events">All</a>`)}${sec('Sparks', sparks(f))}` };
    case 'performance':
      return { title: 'Now, per Spark', body: html`${sec('Decode now', sparks(f))}${sec('This box today', today(d.summary))}` };
    case 'usage':
      return { title: 'Today', body: html`${sec('This box', today(d.summary))}${sec('By Spark', html`<p class="muted small">Usage by Spark and by deployment comes with the gateway’s ledger (step 6).</p>`)}` };
    case 'hardware': {
      const id = d.route.path[0] === 'system' ? null : f?.nodes[0]?.id;
      return { title: 'Spark status', body: html`${sec('Now', sparks(f))}${id ? sec('Look closer', html`<p class="muted small">A Node inspector window shows one Spark’s figures, memory and lease; add one with + Window.</p>`) : nothing}${sec('This box', box(d))}` };
    }
    case 'events':
      return { title: 'Firing now', body: html`${sec('Active alerts', alerts(f))}${sec('Notifications', html`<dl class="kv"><div><dt>in the app</dt><dd>always</dd></div></dl>`)}` };
    case 'sparks': {
      const n = f?.nodes.find((x) => x.id === d.route.path[0]);
      return { title: n ? n.alias : 'Sparks', body: html`${n ? sec('This Spark', html`<dl class="kv"><div><dt>status</dt><dd>${n.status_text}</dd></div><div><dt>hardware</dt><dd>${n.hw}</dd></div><div><dt>version</dt><dd>${n.version}</dd></div><div><dt>reach</dt><dd class="num">${n.address}</dd></div></dl>`) : nothing}${sec('Fleet', sparks(f))}` };
    }
    case 'models':
      return { title: 'Deployments', body: f ? sec('Serving', deployments(f.deployments, f)) : sec('Serving', NO_FLEET) };
    case 'api':
      return { title: 'Gateway', body: html`${sec('Endpoint', copyField(endpoint()))}${f ? sec('Model ids', html`<dl class="kv">${f.deployments.map((x) => html`<div><dt>${x.name}</dt><dd><code class="small">${x.api_id}</code></dd></div>`)}</dl>`) : nothing}` };
    case 'conductor': {
      const c = d.summary?.live.conductor;
      return { title: 'Conductor', body: html`${sec('Session monitor', c ? html`<dl class="kv"><div><dt>mode</dt><dd>${c.mode}</dd></div></dl>` : html`<p class="muted small">The session monitor is off on this server.</p>`)}${sec('Screens', html`<div class="ctx-links">${['overview', 'routing', 'monitor', 'profiles', 'packages', 'deciders', 'sessions', 'memory', 'compare'].map((s) => html`<a class="small" href=${`#/conductor?screen=${s}`}>${s[0].toUpperCase() + s.slice(1)}</a>`)}</div>`)}` };
    }
    case 'playground':
      return { title: 'Targets', body: html`${sec('Endpoint', copyField(`${endpoint()}/chat/completions`))}${sec('Models', f ? html`<dl class="kv">${f.deployments.map((x) => html`<div><dt>${x.name}</dt><dd><code class="small">${x.api_id}</code></dd></div>`)}</dl>` : html`<p class="small"><code>TandemLLM</code>, the model this box serves.</p>`)}` };
    case 'dev':
      return { title: 'This box', body: html`${sec('Engine', box(d))}${sec('Today', today(d.summary))}` };
    case 'add-spark':
      return { title: 'Add Spark', body: html`${sec('Steps', html`<ol class="ctx-steps">${STEPS.map((s) => html`<li>${s}</li>`)}</ol><p class="muted small">You can leave and come back; the controller keeps the state (step 5).</p>`)}${sec('Fleet', sparks(f))}` };
    case 'app':
      return {
        title: 'This device',
        body: html`${sec('Layouts saved here', d.layouts.length ? html`<dl class="kv">${d.layouts.map((l) => html`<div><dt>${menuItem(l.page as Section).label} · ${l.device}</dt><dd class="num">${l.windows} window${l.windows === 1 ? '' : 's'}</dd></div>`)}</dl>` : html`<p class="muted small">Default layouts everywhere.</p>`)}
          <button type="button" class="btn btn-sm ctx-reset" @click=${() => resetAll()}>${icon('refresh')} Reset every layout</button>
          ${sec('Theme', html`<button type="button" class="btn btn-sm" @click=${() => d.toggleTheme()}>${icon(d.theme === 'dark' ? 'sun' : 'moon')} ${d.theme === 'dark' ? 'Light' : 'Dark'} theme</button>`)}
          ${sec('Engine', box(d))}`,
      };
    default:
      return { title: menuItem(section).label, body: sec('Fleet', sparks(f)) };
  }
}
