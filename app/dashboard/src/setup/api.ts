// /v1/setup/* (server/setup_api.py, contract 1.0): the setup guide's routes. Same origin, same error
// handling as the rest of the dashboard (api/client.ts); the chat is a POST, so its SSE is read off
// fetch's body rather than an EventSource. A 401 from a setup route (a box with an admin password)
// also raises `qse-setup-unauthorized` on window, and the page shows its sign-in (signin.ts).
//
// VIS-252: the guide of another Spark (setSetupNode) asks the same routes of TandemApp's controller,
// which relays them to that node (/v1/fleet/nodes/{id}/setup/*); the browser never calls the node.
// There a write (apply, remove, reset, a retry, waking Wren) reads the controller's plan of it first
// and goes only with its confirm, after the page's confirmer (<qse-setup-confirm>) said yes; a no is
// an ApiError of type `cancelled` (status 0), a plan that may not run one of type `blocked` (409).

import { apiBase, ApiError, request } from '../api/client';
import { fleet, type SetupConfirm, type SetupWrite } from '../api/fleet';
import type {
  SetupApplyRequest,
  SetupApplyResponse,
  SetupAssistantStatus,
  SetupChatEvent,
  SetupChatRequest,
  CommunityBody,
  SetupCurrent,
  SetupDevice,
  SetupEstimate,
  SetupInstalledWeights,
  SetupRemoveResult,
  SetupJob,
  SetupModel,
  SetupModelsList,
  SetupPlan,
  SetupResetLevels,
  SetupResetRequest,
  SetupResetResult,
  SetupSelection,
} from '../api/types';
import { SseParser } from '../lib/sse';

/** the node whose guide this is: null for this server's own */
let node: { id: string; alias: string } | null = null;
export function setSetupNode(n: { id: string; alias: string } | null): void {
  node = n;
}
export function setupNode(): { id: string; alias: string } | null {
  return node;
}
/** the routes' prefix: this server's /v1/setup, or the controller's relay to the node */
export function setupBase(): string {
  return node ? `/v1/fleet/nodes/${encodeURIComponent(node.id)}/setup` : '/v1/setup';
}

/** asks the user about a write's plan on another node: true runs it */
export type Confirmer = (plan: SetupConfirm) => Promise<boolean>;
let confirmer: Confirmer | null = null;
export function setConfirmer(fn: Confirmer | null): void {
  confirmer = fn;
}

/** a write's `?confirm=` on another node ('' on this server's own guide) */
async function confirmed(route: SetupWrite, p: Parameters<typeof fleet.setupConfirm>[2] = {}): Promise<string> {
  if (!node) return '';
  const at = `${setupBase()}/${route}`;
  const plan = await fleet.setupConfirm(node.id, route, p).catch((e) => {
    if (e instanceof ApiError && e.status === 401) setupUnauthorized();
    throw e;
  });
  const yes = confirmer ? await confirmer(plan) : false;
  if (!plan.allowed || !plan.confirm) throw new ApiError(at, 409, plan.blocked ?? `${plan.title}: refused`, 'blocked');
  if (!yes) throw new ApiError(at, 0, `Not sent: ${plan.title.charAt(0).toLowerCase()}${plan.title.slice(1)} was cancelled.`, 'cancelled');
  return `?confirm=${encodeURIComponent(plan.confirm)}`;
}

/** a refusal the user chose (no on the plan): nothing to report as an error */
export const isCancelled = (e: unknown): boolean => e instanceof ApiError && e.type === 'cancelled';

export function setupUnauthorized(): void {
  window.dispatchEvent(new CustomEvent('qse-setup-unauthorized'));
}

const get = <T>(path: string): Promise<T> =>
  request<T>(path).catch((e) => {
    if (e instanceof ApiError && e.status === 401) setupUnauthorized();
    throw e;
  });
const post = <T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> =>
  request<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }).catch((e) => {
    if (e instanceof ApiError && e.status === 401) setupUnauthorized();
    throw e;
  });

export const setupApi = {
  models: () => get<SetupModelsList>(`${setupBase()}/models`),
  model: (id: string) => get<SetupModel>(`${setupBase()}/models/${encodeURIComponent(id)}`),
  current: (id: string) => get<SetupCurrent>(`${setupBase()}/models/${encodeURIComponent(id)}/current`),
  device: () => get<SetupDevice>(`${setupBase()}/device`),
  estimate: (sel: SetupSelection, signal?: AbortSignal) => post<SetupEstimate>(`${setupBase()}/estimate`, sel, signal),
  plan: (sel: SetupSelection, signal?: AbortSignal) => post<SetupPlan>(`${setupBase()}/plan`, { selection: sel }, signal),
  apply: async (body: SetupApplyRequest) =>
    post<SetupApplyResponse>(`${setupBase()}/apply${await confirmed('apply', { model: body.selection.model, profile: body.selection.profile })}`, body),
  job: (id: string) => get<SetupJob>(`${setupBase()}/jobs/${encodeURIComponent(id)}?format=json`),
  jobUrl: (id: string) => `${apiBase()}${setupBase()}/jobs/${encodeURIComponent(id)}`,
  /** SRV-560: the weights on the box, and removing some */
  weights: () => get<SetupInstalledWeights>(`${setupBase()}/weights`),
  remove: async (profiles: string[]) => post<SetupRemoveResult>(`${setupBase()}/remove${await confirmed('remove', { profiles })}`, { profiles, confirm: true }),
  /** VIS-213: a failed or rolled back job again, from the failed step's side (the download is kept) */
  retry: async (id: string) => post<SetupApplyResponse>(`${setupBase()}/jobs/${encodeURIComponent(id)}/retry${await confirmed(`jobs/${encodeURIComponent(id)}/retry`)}`, {}),
  /** VIS-213: the job as text for a bug report (no secret) */
  diagnostics: (id: string) =>
    request<string>(`${setupBase()}/jobs/${encodeURIComponent(id)}/diagnostics`, {}, 'text').catch((e) => {
      if (e instanceof ApiError && e.status === 401) setupUnauthorized();
      throw e;
    }),
  /** the community list, fetched from GitHub now (CONTRACT.md addition 3: on the weights section's
   *  open and on Refresh only; one conditional GET, 10 s); the cache or the bundled copy when GitHub
   *  does not answer, with `sync.error` */
  communitySync: (signal?: AbortSignal) => post<CommunityBody>(`${setupBase()}/community/sync`, {}, signal),
  community: () => get<CommunityBody>(`${setupBase()}/community`),
  resetLevels: () => get<SetupResetLevels>(`${setupBase()}/reset`),
  reset: async (body: SetupResetRequest) => post<SetupResetResult>(`${setupBase()}/reset${await confirmed('reset', { level: body.level })}`, body),
  assistant: {
    status: () => get<SetupAssistantStatus>(`${setupBase()}/assistant/status`),
    load: async () => post<SetupAssistantStatus>(`${setupBase()}/assistant/load${await confirmed('assistant/load')}`, {}),
    unload: () => post<SetupAssistantStatus>(`${setupBase()}/assistant/unload`, {}),
  },
  health: () => request<{ status: string; mode?: string }>('/health'),
  /** the sign-in after a 401 (signin.ts): 204 and the session cookie; 404 with the dashboard login off.
   *  Another node's guide asks the controller, whose admin password it is (VIS-252). */
  signIn: (password: string) =>
    node
      ? fleet.signIn(password)
      : request<void>('/v1/dashboard/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: password }) }, 'none'),
};

/** POST /v1/setup/assistant/chat and hand each event to `onEvent` until the stream ends. */
export async function streamChat(body: SetupChatRequest, onEvent: (e: SetupChatEvent) => void, signal: AbortSignal): Promise<void> {
  const endpoint = `${setupBase()}/assistant/chat`;
  let res: Response;
  try {
    res = await fetch(apiBase() + endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      credentials: 'same-origin',
      cache: 'no-store',
      signal,
    });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new ApiError(endpoint, 0, (e as Error).message, 'offline');
  }
  if (res.status === 401) setupUnauthorized();
  if (!res.ok || !res.body) {
    let msg = `HTTP ${res.status}`;
    let type = 'error';
    try {
      const j = await res.json();
      msg = j?.error?.message ?? msg;
      type = j?.error?.type ?? type;
    } catch {
      /* not json */
    }
    throw new ApiError(endpoint, res.status, msg, type);
  }
  const parser = new SseParser();
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const ev of parser.feed(dec.decode(value, { stream: true }))) {
      if (ev.data === '[DONE]') return;
      let parsed: SetupChatEvent;
      try {
        parsed = JSON.parse(ev.data);
      } catch {
        continue;
      }
      onEvent(parsed);
    }
  }
}
