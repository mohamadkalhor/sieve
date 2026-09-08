/**
 * The only way to /v1 (CONTRACTS section 8).
 *
 * Every failure comes back as the API's own envelope rather than a thrown
 * string, because the screens have to *show* what went wrong -- a 501 naming
 * the module that has not landed, a 401 asking for a token -- rather than
 * quietly rendering an empty table that looks like "no models found".
 */
import type {
  Axis,
  Chain,
  Decision,
  Modality,
  Profile,
  Ranking,
  HealthRow,
  Reachable,
  TargetResult
} from '$lib/types';

export const API_BASE =
  (import.meta.env?.PUBLIC_SIEVE_API as string | undefined)?.replace(/\/$/, '') ?? '';

export interface ApiError {
  code: string;
  message: string;
  /** present on a 501: the JSON schema of what the route will return */
  shape?: unknown;
  status: number;
}

export type Result<T> = { ok: true; value: T } | { ok: false; error: ApiError };

export function ok<T>(value: T): Result<T> {
  return { ok: true, value };
}

export function fail<T>(error: ApiError): Result<T> {
  return { ok: false, error };
}

/** A short sentence a screen can put in an empty state. */
export function explainError(error: ApiError): string {
  if (error.code === 'not_built') return error.message;
  if (error.status === 401) return 'This needs a token. Set one in SIEVE_TOKENS and reload.';
  if (error.status === 403) return 'Your token does not carry the scope this needs.';
  if (error.status === 404) return error.message;
  if (error.status === 0) return `Cannot reach the API at ${API_BASE || 'this origin'}.`;
  return error.message;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

type Fetcher = typeof globalThis.fetch;

export interface RequestOptions {
  fetch?: Fetcher;
  token?: string;
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<Result<T>> {
  const run = options.fetch ?? globalThis.fetch;
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers['content-type'] = 'application/json';
  if (options.token) headers.authorization = `Bearer ${options.token}`;

  let response: Response;
  try {
    response = await run(`${API_BASE}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal
    });
  } catch (cause) {
    return fail({
      code: 'unreachable',
      message: cause instanceof Error ? cause.message : 'the API did not answer',
      status: 0
    });
  }

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const body = payload as { error?: { code?: string; message?: string }; shape?: unknown } | null;
    return fail({
      code: body?.error?.code ?? `http_${response.status}`,
      message: body?.error?.message ?? response.statusText,
      shape: body?.shape,
      status: response.status
    });
  }
  return ok(payload as T);
}

/* -------------------------------------------------------------------------- */
/* the endpoints the screens use                                               */
/* -------------------------------------------------------------------------- */

export interface ModalityCount {
  modality: Modality;
  models: number;
  observations: number;
}

export interface ModelRow {
  id: string;
  modality: Modality;
  name: string;
  creator: string;
  aliases: string[];
  reachable: boolean;
  local_ids: string[];
  price: {
    input?: number | null;
    output?: number | null;
    per_unit?: number | null;
    unit: string;
  } | null;
}

export interface SourceRow {
  name: string;
  enabled: boolean;
  registered: boolean;
  needs_key: boolean;
  key_present: boolean;
  rows: number;
  last_pull: string | null;
  modalities: Modality[];
}

export interface Recommendation {
  profile: string;
  models: { id: string; local_ids: string[]; final: number | null; confidence: number | null }[];
  computed_at: string;
}

const q = (params: Record<string, string | number | boolean | undefined>): string => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
};

export const api = {
  modalities: (o?: RequestOptions) => request<ModalityCount[]>('/v1/modalities', o),

  axes: (modality?: Modality, o?: RequestOptions) => request<Axis[]>(`/v1/axes${q({ modality })}`, o),

  models: (
    params: { modality?: Modality; reachable?: boolean; q?: string; limit?: number } = {},
    o?: RequestOptions
  ) => request<Page<ModelRow>>(`/v1/models${q(params)}`, o),

  profiles: (modality?: Modality, o?: RequestOptions) =>
    request<Profile[]>(`/v1/profiles${q({ modality })}`, o),

  profile: (name: string, o?: RequestOptions) =>
    request<Profile>(`/v1/profiles/${encodeURIComponent(name)}`, o),

  ranking: (profile: string, o?: RequestOptions) =>
    request<Ranking>(`/v1/rankings/${encodeURIComponent(profile)}`, o),

  chain: (profile: string, o?: RequestOptions) =>
    request<Chain>(`/v1/chains/${encodeURIComponent(profile)}`, o),

  recommend: (profile: string, n = 3, o?: RequestOptions) =>
    request<Recommendation>(`/v1/recommend${q({ profile, n })}`, o),

  decisions: (profile?: string, o?: RequestOptions) =>
    request<Decision[]>(`/v1/decisions${q({ profile, limit: 50 })}`, o),

  /** What the gateway's own traffic says, per model. The Pulse screen reads this. */
  health: (window: '24h' | '7d' = '24h', reachable = true, o?: RequestOptions) =>
    request<HealthRow[]>(`/v1/health${q({ window, reachable })}`, o),

  sources: (o?: RequestOptions) => request<SourceRow[]>('/v1/sources', o),

  inventory: (unmatched?: boolean, o?: RequestOptions) =>
    request<Reachable[]>(`/v1/inventory${q({ unmatched })}`, o),

  evaluate: (name: string, o?: RequestOptions) =>
    request<{ ranking: Ranking; chain: Chain | null; decision: Decision | null }>(
      `/v1/profiles/${encodeURIComponent(name)}/evaluate`,
      { ...o, method: 'POST' }
    ),

  setWeights: (name: string, weights: Record<string, number>, o?: RequestOptions) =>
    request<Profile>(`/v1/profiles/${encodeURIComponent(name)}/weights`, {
      ...o,
      method: 'PATCH',
      body: weights
    }),

  apply: (profiles: string[], o?: RequestOptions) =>
    request<TargetResult[]>('/v1/apply', { ...o, method: 'POST', body: { profiles } }),

  pull: (name: string, o?: RequestOptions) =>
    request<{ job: string; source: string; added: number; warnings: string[] }>(
      `/v1/sources/${encodeURIComponent(name)}/pull`,
      { ...o, method: 'POST' }
    ),

  alias: (alias: string, model_id: string, modality: Modality, o?: RequestOptions) =>
    request<{ alias: string; model_id: string }>('/v1/aliases', {
      ...o,
      method: 'PUT',
      body: { alias, model_id, modality }
    })
};

/** `/v1/events` as a callback, unsubscribed by the returned function. */
export function subscribe(
  onEvent: (kind: string, data: unknown) => void,
  kinds: string[] = ['pull', 'ranking', 'decision', 'apply']
): () => void {
  if (typeof EventSource === 'undefined') return () => {};
  const source = new EventSource(`${API_BASE}/v1/events`);
  const listeners = kinds.map((kind) => {
    const handler = (event: MessageEvent) => {
      try {
        onEvent(kind, JSON.parse(event.data));
      } catch {
        onEvent(kind, null);
      }
    };
    source.addEventListener(kind, handler as EventListener);
    return [kind, handler] as const;
  });
  return () => {
    for (const [kind, handler] of listeners) {
      source.removeEventListener(kind, handler as EventListener);
    }
    source.close();
  };
}
