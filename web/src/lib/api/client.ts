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
  Leaderboard,
  Reachable,
  TargetDiff,
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
  // Which effort mode this row is, and the family it is a mode of. /v1/models
  // has always returned both -- ModelRef carries them -- and the Field needs
  // them to join one model's modes into a line.
  effort: string | null;
  family: string | null;
  reachable: boolean;
  local_ids: string[];
  price: {
    input?: number | null;
    output?: number | null;
    per_unit?: number | null;
    unit: string;
  } | null;
}

/** `GET /v1/status`: when Sieve last looked, and how often it looks. */
export interface StatusRow {
  /** the last pull of any source, new data or not */
  pulled_at: string | null;
  /** the last decision the scheduled loop recorded */
  ran_at: string | null;
  /** the configured cadence, e.g. `hourly` */
  schedule: string;
  sources_enabled: number;
  /** every call telemetry holds (pruned to thirty days), and the newest one */
  telemetry_calls: number;
  telemetry_at: string | null;
}

export interface SourceRow {
  name: string;
  enabled: boolean;
  registered: boolean;
  needs_key: boolean;
  key_present: boolean;
  /** observations written */
  rows: number;
  /** price rows written: a source may supply these and no observations at all */
  prices: number;
  last_price: string | null;
  /** the later of the two; null only when the source never wrote either */
  last_pull: string | null;
  modalities: Modality[];
}

export interface Recommendation {
  profile: string;
  models: { id: string; local_ids: string[]; final: number | null; confidence: number | null }[];
  computed_at: string;
}

/**
 * `/v1/connectors`: a gateway Sieve can read a model list from, and push its
 * routing to. Phase 3 turned what used to be a block in the server's own
 * config file into rows, so a person with a different router can add one from
 * the Connectors screen.
 *
 * No response ever carries a key: `token_env` is the *name* of an environment
 * variable, which is why it is safe to put on a page.
 */
export type ConnectorKind = 'ninerouter' | 'openai_compat';

export interface ConnectorRow {
  id: string;
  name: string;
  kind: ConnectorKind;
  base_url: string;
  token_env: string;
  /** pull the model list from it */
  read: boolean;
  /** push routing decisions to it */
  write: boolean;
  poll_minutes: number;
  last_pull_at: string | null;
  last_push_at: string | null;
  /** whatever the last read or write failed with, verbatim */
  last_error: string | null;
}

/** What POST and PUT take. PUT accepts any subset of it. */
export interface ConnectorBody {
  name: string;
  kind: ConnectorKind;
  base_url: string;
  token_env: string;
  read: boolean;
  write: boolean;
  poll_minutes: number;
}

/** The answer to `test` and to `pull`: it worked, or it says why not. */
export interface ConnectorProbe {
  ok: boolean;
  models_count: number;
  error: string | null;
}

/* -------------------------------------------------------------------------- */
/* one profile, as the Profiles screen manages it                              */
/* -------------------------------------------------------------------------- */

/**
 * One weight, with the room it is allowed to move in.
 *
 * `min`/`max` are not decoration: a seat may be allowed to care about cost
 * between 0.1 and 0.4 and nowhere else, and a slider that can be dragged
 * outside that range is offering something the server will refuse.
 */
export interface WeightControl {
  value: number;
  min: number;
  max: number;
  locked: boolean;
}

/**
 * Every control behind one profile row: `GET/PUT /v1/profiles/{name}/settings`.
 *
 * Until that route lands the screen builds this same shape out of `Profile` --
 * the weights with an open 0..1 range and nothing locked, the list length and
 * the floor from `policy` -- so the sliders still move and the list still
 * re-ranks. Only the two fields that have no older equivalent, price
 * sensitivity and experience weight, are disabled while that is true.
 */
export interface ProfileSettings {
  list_length: number;
  floor_score: number;
  price_sensitivity: number;
  experience_weight: number;
  auto_apply: boolean;
  weights: Record<string, WeightControl>;
  /** per-profile overrides; a prefix absent here uses the default */
  cost_multipliers: Record<string, number>;
}

/** What a model is doing on a profile's list. */
export type ModelStatus = 'active' | 'pinned' | 'removed';

/**
 * What a draft would ship, and the ranking behind it.
 *
 * `models` is already the answer: pinned first, then everything active and
 * reachable above the floor, cut to `list_length`. The `ranking` alongside it
 * is what those ids scored, so a screen can show a number next to each without
 * a second request.
 */
export interface PreviewResult {
  profile: string;
  models: string[];
  settings: ProfileSettings;
  ranking: Ranking;
}

/** What `apply` shipped, and to which connectors. */
export interface ApplyOutcome {
  chain: Chain;
  results: TargetResult[];
}

/** `GET /v1/profiles/{name}/history`: who changed what, from what to what. */
export interface HistoryRow {
  who: string;
  when: string;
  what: string;
  before?: unknown;
  after?: unknown;
}

/**
 * `GET /v1/profiles/{name}/experience`: your own traffic, per model, over the
 * last thirty days. `experience` is smoothed -- (successes + 1) / (calls + 2)
 * -- so one lucky call does not read as a perfect record.
 */
export interface ExperienceRow {
  model_id: string;
  successes: number;
  outcomes: number;
  experience: number;
}

/**
 * What the Profiles screen sends to `POST /v1/profiles`.
 *
 * The profile to copy is spelled `from` by the server and `copy_from` in the
 * design note this screen was built to; both are sent, because an unknown key
 * is ignored either way and a missing one silently starts an empty profile.
 */
export interface NewProfileBody {
  name: string;
  modality: Modality;
  from?: string;
  copy_from?: string;
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
  status: (o?: RequestOptions) => request<StatusRow>('/v1/status', o),

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

  /**
   * The ranking for one modality, best first. Also says whether a
   * quality-against-cost scatter is answerable here at all.
   */
  leaderboard: (modality: Modality, metric?: string, o?: RequestOptions) =>
    request<Leaderboard>(`/v1/leaderboard${q({ modality, metric })}`, o),

  /** What every target holds now, so a diff is against reality and not a belief. */
  diff: (o?: RequestOptions) => request<TargetDiff[]>('/v1/diff', o),

  sources: (o?: RequestOptions) => request<SourceRow[]>('/v1/sources', o),
  connectors: (o?: RequestOptions) => request<ConnectorRow[]>('/v1/connectors', o),

  createConnector: (body: ConnectorBody, o?: RequestOptions) =>
    request<ConnectorRow>('/v1/connectors', { ...o, method: 'POST', body }),

  /** Partial: the switches send one field, the form sends all of them. */
  updateConnector: (id: string, body: Partial<ConnectorBody>, o?: RequestOptions) =>
    request<ConnectorRow>(`/v1/connectors/${encodeURIComponent(id)}`, {
      ...o,
      method: 'PUT',
      body
    }),

  removeConnector: (id: string, o?: RequestOptions) =>
    request<null>(`/v1/connectors/${encodeURIComponent(id)}`, { ...o, method: 'DELETE' }),

  /** Reaches the gateway and counts what it serves; writes nothing. */
  testConnector: (id: string, o?: RequestOptions) =>
    request<ConnectorProbe>(`/v1/connectors/${encodeURIComponent(id)}/test`, {
      ...o,
      method: 'POST'
    }),

  pullConnector: (id: string, o?: RequestOptions) =>
    request<ConnectorProbe>(`/v1/connectors/${encodeURIComponent(id)}/pull`, {
      ...o,
      method: 'POST'
    }),

  connectorModels: (id: string, o?: RequestOptions) =>
    request<string[]>(`/v1/connectors/${encodeURIComponent(id)}/models`, o),


  /* ---- one profile, as the Profiles screen manages it ------------------ */

  /**
   * Every control behind one row. A 404 here is not the user's mistake: the
   * route arrives with the settings module, and until it does the screen falls
   * back to the profile's own weights and policy.
   */
  profileSettings: (name: string, o?: RequestOptions) =>
    request<ProfileSettings>(`/v1/profiles/${encodeURIComponent(name)}/settings`, o),

  saveProfileSettings: (name: string, body: Partial<ProfileSettings>, o?: RequestOptions) =>
    request<ProfileSettings>(`/v1/profiles/${encodeURIComponent(name)}/settings`, {
      ...o,
      method: 'PUT',
      body
    }),

  /**
   * Pin or remove one model on one profile. Written through the moment it is
   * clicked, because a pin that looks set and is only in a draft is a lie
   * about what the seat will ship.
   */
  setModelStatus: (name: string, modelId: string, status: ModelStatus, o?: RequestOptions) =>
    request<{ status: ModelStatus }>(
      `/v1/profiles/${encodeURIComponent(name)}/models/${encodeURIComponent(modelId)}/status`,
      { ...o, method: 'PUT', body: { status } }
    ),

  /**
   * The list a draft would ship, without shipping it. Meant to be fast and
   * called often: a row debounces 250 ms and sends whatever its controls hold.
   */
  preview: (name: string, settings: Partial<ProfileSettings>, o?: RequestOptions) =>
    request<PreviewResult>(`/v1/profiles/${encodeURIComponent(name)}/preview`, {
      ...o,
      method: 'POST',
      body: settings
    }),

  applyProfile: (name: string, o?: RequestOptions) =>
    request<ApplyOutcome>(`/v1/profiles/${encodeURIComponent(name)}/apply`, {
      ...o,
      method: 'POST'
    }),

  history: (name: string, o?: RequestOptions) =>
    request<HistoryRow[]>(`/v1/profiles/${encodeURIComponent(name)}/history`, o),

  experience: (name: string, o?: RequestOptions) =>
    request<ExperienceRow[]>(`/v1/profiles/${encodeURIComponent(name)}/experience`, o),

  /**
   * A profile from nothing, or copied from one that already works.
   *
   * `createProfile` above is the older shape (`from`, `purpose`) that phase 1
   * shipped. Both are kept because a server may have either: the screen sends
   * this one and falls back to that one when the body is refused.
   */
  newProfile: (body: NewProfileBody, o?: RequestOptions) =>
    request<Profile>('/v1/profiles', { ...o, method: 'POST', body }),

  renameProfile: (name: string, next: string, o?: RequestOptions) =>
    request<Profile>(`/v1/profiles/${encodeURIComponent(name)}`, {
      ...o,
      method: 'PATCH',
      body: { name: next }
    }),

  /**
   * Delete. Refused with 409 `in_use` while a write connector may still be
   * holding this profile's combo; `force` is the second ask, which the screen
   * only sends after showing what the first refusal said.
   */
  removeProfile: (name: string, force = false, o?: RequestOptions) =>
    request<{ deleted: string; actor: string }>(
      `/v1/profiles/${encodeURIComponent(name)}${force ? '?force=1' : ''}`,
      { ...o, method: 'DELETE' }
    ),

  /** The cost multipliers every profile inherits, by local-id prefix. */
  costMultipliers: (o?: RequestOptions) =>
    request<Record<string, number>>('/v1/cost-multipliers', o),

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

  /** Merged into the existing policy: send only what changed. */
  setPolicy: (name: string, policy: Record<string, unknown>, o?: RequestOptions) =>
    request<Profile>(`/v1/profiles/${encodeURIComponent(name)}/policy`, {
      ...o,
      method: 'PATCH',
      body: policy
    }),

  /**
   * Replaces the whole `require` block, because the interesting edit is
   * *removing* a constraint and a merge cannot say that.
   */
  setConstraints: (name: string, require: Record<string, unknown>, o?: RequestOptions) =>
    request<Profile>(`/v1/profiles/${encodeURIComponent(name)}/constraints`, {
      ...o,
      method: 'PATCH',
      body: require
    }),

  /** Merged. Changing the shape re-prices every model on the seat. */
  setShape: (name: string, shape: Record<string, unknown>, o?: RequestOptions) =>
    request<Profile>(`/v1/profiles/${encodeURIComponent(name)}/shape`, {
      ...o,
      method: 'PATCH',
      body: shape
    }),

  /** A new profile, cloned from one that already works. */
  createProfile: (
    body: { name: string; from: string; purpose?: string },
    o?: RequestOptions
  ) => request<Profile>('/v1/profiles', { ...o, method: 'POST', body }),

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
