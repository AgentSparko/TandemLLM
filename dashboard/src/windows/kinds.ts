// The window kinds (VIS-256, concept §11): what the add menu offers, in its groups, and for each kind
// its label, icon and hint, the ref a new one starts with, its title from its ref, the element that
// draws it and the chunk that defines that element. No element is imported here: a kind's chunk loads
// the first time a window of it shows (`load`), so a page carries only the kinds it has open.
//
// What a kind reaches today: Logs, Chart and the Node inspector read TandemApp's controller (the fleet
// API); Terminal and Files on a Spark go through its TandemNode agent (SRV-725), which nothing reaches
// yet, so they say so and offer the chat sandbox, the one shell and file tree a box has today; the file
// and code kinds show a chat's sandbox; Chat and Playground talk to the one API.

import type { WindowKind } from '../api/fleet';
import type { Ref } from '../lib/wm';

export type KindGroup = 'Fleet' | 'Files and code' | 'View' | 'Model';

export interface KindDef {
  label: string;
  icon: string;
  hint: string;
  group: KindGroup;
  /** the ref of a new window */
  init(): Ref;
  /** the title, from its ref; `alias` names a node id */
  title(ref: Ref, alias: (id: string) => string): string;
  tag: string;
  load(): Promise<unknown>;
}

let fleetChunk: Promise<unknown> | null = null;
let sandboxChunk: Promise<unknown> | null = null;
let modelChunk: Promise<unknown> | null = null;
const loadFleet = () => (fleetChunk ??= import('./fleet'));
const loadSandbox = () => (sandboxChunk ??= import('./sandbox'));
const loadModel = () => (modelChunk ??= import('./model'));

const str = (v: unknown) => (typeof v === 'string' ? v : '');
const base = (p: string) => p.replace(/^(file|port|md|diff|image):/, '').split('/').filter(Boolean).pop() ?? p;
/** what a sandbox window is on: a node, or the chat sandbox */
export const onNode = (ref: Ref): string | null => (typeof ref.node === 'string' && ref.node !== 'sandbox' ? ref.node : null);

const sandbox = (label: string, icon: string, hint: string, group: KindGroup, title?: (r: Ref) => string): KindDef => ({
  label,
  icon,
  hint,
  group,
  init: () => ({}),
  title: (r) => (title ? title(r) : str(r.title) || (str(r.ref) ? `${label} · ${base(str(r.ref))}` : label)),
  tag: 'qse-win-sandbox',
  load: loadSandbox,
});

export const KINDS: Record<WindowKind, KindDef> = {
  terminal: {
    label: 'Terminal',
    icon: 'terminal',
    hint: 'a Spark or the chat sandbox',
    group: 'Fleet',
    init: () => ({ node: 'sandbox' }),
    title: (r, alias) => `Terminal · ${onNode(r) ? alias(onNode(r)!) : r.node === null ? 'a Spark' : 'chat sandbox'}`,
    tag: 'qse-win-sandbox',
    load: loadSandbox,
  },
  logs: {
    label: 'Logs',
    icon: 'doc',
    hint: 'live, per Spark',
    group: 'Fleet',
    init: () => ({ node: 'all', level: 'all', q: '' }),
    title: (r, alias) => `Logs · ${!r.node || r.node === 'all' ? 'all Sparks' : alias(str(r.node))}`,
    tag: 'qse-win-logs',
    load: loadFleet,
  },
  chart: {
    label: 'Chart',
    icon: 'performance',
    hint: 'any metric, any scope',
    group: 'Fleet',
    init: () => ({ metric: 'tps', scope: 'page' }),
    title: (r) => `Chart · ${CHART_LABELS[str(r.metric)] ?? 'decode'}`,
    tag: 'qse-win-chart',
    load: loadFleet,
  },
  inspector: {
    label: 'Node inspector',
    icon: 'hardware',
    hint: 'one Spark',
    group: 'Fleet',
    init: () => ({ node: null }),
    title: (r, alias) => `Inspector · ${r.node ? alias(str(r.node)) : 'a Spark'}`,
    tag: 'qse-win-inspector',
    load: loadFleet,
  },
  files: {
    label: 'Files',
    icon: 'folder',
    hint: 'a Spark or the chat sandbox',
    group: 'Files and code',
    init: () => ({ node: 'sandbox' }),
    title: (r, alias) => `Files · ${onNode(r) ? alias(onNode(r)!) : 'chat sandbox'}`,
    tag: 'qse-win-sandbox',
    load: loadSandbox,
  },
  editor: sandbox('Editor', 'edit', 'a file of the sandbox', 'Files and code', (r) => (str(r.path) ? `Editor · ${base(str(r.path))}` : 'Editor')),
  diff: sandbox('Diff', 'diff', 'what a chat changed', 'Files and code', (r) => str(r.title) || 'Diff'),
  markdown: sandbox('Markdown', 'doc', 'rendered', 'Files and code'),
  plan: sandbox('Plan', 'doc', 'a chat’s todo list', 'Files and code', () => 'Plan'),
  preview: sandbox('Preview', 'preview', 'a page or a port', 'View'),
  image: sandbox('Image', 'image', 'svg, png', 'View'),
  pdf: sandbox('PDF', 'doc', 'viewer', 'View'),
  video: sandbox('Video', 'play', 'player', 'View'),
  audio: sandbox('Audio', 'play', 'player', 'View'),
  binary: sandbox('File', 'file', 'size and download', 'View'),
  chat: {
    label: 'Chat',
    icon: 'chat',
    hint: 'a quick one',
    group: 'Model',
    init: () => ({ model: null, msgs: [] }),
    title: (r) => `Chat${str(r.model) ? ` · ${str(r.model)}` : ''}`,
    tag: 'qse-win-minichat',
    load: loadModel,
  },
  playground: {
    label: 'Playground',
    icon: 'playground',
    hint: 'one prompt, one run',
    group: 'Model',
    init: () => ({ model: null, prompt: '' }),
    title: () => 'Playground',
    tag: 'qse-win-miniplay',
    load: loadModel,
  },
  context: sandbox('Context', 'info', 'a chat’s context', 'Model', () => 'Context'),
};

/** the chart window's metric names (api/fleet.ts METRICS, kept here so the menu needs no chart) */
const CHART_LABELS: Record<string, string> = {
  tps: 'decode',
  req: 'requests',
  ttft: 'first token',
  gpu: 'GPU',
  power: 'power',
  temp: 'temperature',
  sm: 'SM clock',
  memclk: 'memory clock',
  mem: 'memory',
  cpu: 'CPU',
  disk: 'disk',
  lan: 'LAN',
  fabric: 'fabric',
  rtt: 'latency',
};

/** the add menu's groups, in order */
export const GROUPS: [KindGroup, WindowKind[]][] = [
  ['Fleet', ['terminal', 'logs', 'chart', 'inspector']],
  ['Files and code', ['files', 'editor', 'diff', 'markdown', 'plan']],
  ['View', ['preview', 'image', 'pdf', 'video', 'audio', 'binary']],
  ['Model', ['chat', 'playground', 'context']],
];

/** a window's title; a kind that cannot read its ref falls back to its label */
export function windowTitle(kind: WindowKind, ref: Ref, alias: (id: string) => string = (id) => id): string {
  try {
    return KINDS[kind].title(ref, alias);
  } catch {
    return KINDS[kind].label;
  }
}
