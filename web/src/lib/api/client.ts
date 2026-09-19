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
  TargetResult,
  Unit
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

/**
 * A short sentence a screen can put in an empty state. A 401/403 without a
 * pasted token bounces the browser to gate before this is ever read (see
 * `redirectToLogin`/`redirectToPending` in `request`); these strings are
 * mainly what a pasted, wrong bearer token sees, since that case is exempt.
 */
export function explainError(error: ApiError): string {
  if (error.code === 'not_built') return error.message;
  if (error.status === 401) return 'This needs a token, or a signed-in session. Sign in, or set one below and reload.';
  if (error.status === 403) return 'Your token or your role does not carry the scope this needs.';
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
  responseType?: 'json' | 'text';
}

/**
 * gate v2 (AUTH-CONTRACT.md section 7): a 401 whose body says
 * `error.code == "unauthenticated"` is gate's own wall speaking -- either
 * nginx's edge or this app's own `EdgeAuthMiddleware` -- and the app's own
 * "no bearer token" 401 means the same thing for a browser that has no
 * pasted token either. Either way the browser has no working session and
 * belongs on the login page, `next` pointing back at where it was.
 */
function redirectToLogin(): void {
  if (typeof window === 'undefined') return;
  const { pathname, search, hash } = window.location;
  window.location.href = `/auth/login?next=${encodeURIComponent(pathname + search + hash)}`;
}

function redirectToPending(): void {
  if (typeof window === 'undefined') return;
  window.location.href = '/auth/pending';
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
      // gate v2's session cookie is host-only (AUTH-CONTRACT.md section 3):
      // it is never meant for another origin, so `same-origin` is the
      // correct mode now, not a weaker stand-in for `include`.
      credentials: 'same-origin',
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
    payload = response.ok && options.responseType === 'text'
      ? await response.text()
      : await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const body = payload as { error?: { code?: string; message?: string }; shape?: unknown } | null;
    // A call carrying a pasted bearer token is a person testing *that*
    // token: a 401/403 there is telling them the token is wrong, not that
    // their browser has no session, so it must never bounce them to gate.
    if (!options.token) {
      if (response.status === 401) redirectToLogin();
      else if (response.status === 403 && body?.error?.code === 'pending') redirectToPending();
    }
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

/** One of the three named steps of the loop, or all three in order. */
export type Step = 'full' | 'pull_sources' | 'harvest_connectors' | 'ship_profiles';

export const STEPS: Step[] = ['full', 'pull_sources', 'harvest_connectors', 'ship_profiles'];

export const STEP_LABEL: Record<Step, string> = {
  full: 'Full run',
  pull_sources: 'Pull sources',
  harvest_connectors: 'Harvest connectors',
  ship_profiles: 'Ship profiles'
};

export const STEP_SAYS: Record<Step, string> = {
  full: 'harvest, then pull, then ship',
  pull_sources: 'fetch the benchmark sources into the store',
  harvest_connectors: 'ask every connector what it serves, refresh the inventory',
  ship_profiles: 'rank, decide, and ship the seats that auto-apply'
};

/** A row of the `runs` table: one press of Run now, or one scheduled firing. */
export interface RunRow {
  id: string;
  step: Step;
  requested_by: string;
  started: string;
  finished: string | null;
  running: boolean;
  ok: boolean | null;
  summary: string | null;
  error: string | null;
  /** how long it took, or how long it has been going */
  seconds: number;
  has_log: boolean;
}

/** A row of the `schedules` table, with the next moment it is due. */
export interface ScheduleRow {
  step: Step;
  mode: 'off' | 'hourly' | 'daily';
  /** minutes past the hour, for `hourly` */
  at_minute: number;
  /** HH:MM in `timezone`, for `daily` */
  at_time: string;
  timezone: string;
  last_fired: string | null;
  next_fire: string | null;
}

/** Who the API thinks is calling: a gate session, or a token's name. */
export interface MeRow {
  name: string;
  role?: string;
  scopes?: string[];
}

/** `GET /v1/status`: when Sieve last looked, and how often it looks. */
export interface StatusRow {
  /** the last pull of any source, new data or not */
  pulled_at: string | null;
  /** the last decision the scheduled loop recorded */
  ran_at: string | null;
  /** the cadence of `full`, read from the schedules table -- not from TOML */
  schedule: string;
  sources_enabled: number;
  /** every call telemetry holds (pruned to thirty days), and the newest one */
  telemetry_calls: number;
  telemetry_at: string | null;
  /**
   * `reachable` and `unscored` (CONSOLE.md section 4.4): the two numbers the
   * status bar has to say without opening a screen. Absent on an older server,
   * and then the bar omits those two items rather than showing a zero it does
   * not know.
   */
  reachable?: number;
  unscored?: number;
  /** what is going now and what finished last; absent on an older server */
  runs?: {
    running: RunRow | null;
    last: RunRow | null;
    last_by_step: Record<string, RunRow | null>;
  };
  schedules?: ScheduleRow[];
  /** the signed-in person, when gate says there is one */
  user?: MeRow | null;
}

export interface AxisRow extends Axis {
  /** one line saying what this axis means, for the person moving its slider */
  meaning: string;
  fields_count: number;
  profiles: string[];
  builtin: boolean;
}

export interface SourceFieldRow {
  field: string;
  rows: number;
}

export interface AliasRow {
  alias: string;
  model_id: string;
  modality: Modality;
  origin: 'user' | 'source';
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
  /**
   * Whether the environment variable named by `token_env` actually holds
   * something on the server. Never the value -- only whether there is one.
   */
  token_present?: boolean;
  /** the same question for the admin variable, when the connector names one */
  admin_token_present?: boolean;
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
 * Everything behind one profile: `GET/PUT /v1/profiles/{name}/settings`.
 *
 * A profile is its weights -- each a share of the score, the shares adding to
 * one -- and `ship`, how many models go out. There is nothing else to hold: a
 * floor, a price sensitivity, an experience weight, per-weight bounds and an
 * auto-apply switch all used to live here and all of them changed the answer
 * without moving a slider.
 */
export type ProfileMode = 'auto' | 'manual';

/** What a shipped model can be required to do. */
export type Need = 'vision' | 'reasoning' | 'tools' | 'structured_output';

export const NEEDS: Need[] = ['vision', 'reasoning', 'tools', 'structured_output'];

export interface ProfileSettings {
  ship: number;
  weights: Record<string, number>;
  /** auto: ranked by the weights; manual: exactly `manual`, in order */
  mode?: ProfileMode;
  manual?: string[];
  /** auto: always ship these, first */
  pinned?: string[];
  /** auto: never ship these */
  removed?: string[];
  /** both: a model ships only if it is known to do each */
  needs?: Need[];
  /** router prefix -> a multiplier on the score of every model it serves */
  prefix_weights?: Record<string, number>;
  /** keys the server accepted and ignored, one sentence each */
  warnings?: string[];
}

/** What a page sends: any subset, plus the axes it wants taken off. */
export interface SettingsPatch {
  ship?: number;
  weights?: Record<string, number | null>;
  remove_axes?: string[];
  mode?: ProfileMode;
  manual?: string[];
  pinned?: string[];
  removed?: string[];
  needs?: Need[];
  prefix_weights?: Record<string, number>;
}

/** One model on a list, as a page draws it. */
export interface Listed {
  id: string;
  name: string;
  local_ids: string[];
  score: number;
  /** true, false, or null when no source said */
  abilities?: Partial<Record<Need, boolean | null>>;
  /** the profile's needs this model is not known to meet */
  lacks?: Need[];
  pinned?: boolean;
  /** false when no source measured it and nobody scored it by hand */
  scored?: boolean;
  /**
   * What each axis gave this model, in the order the proposed weights name
   * them (CONSOLE.md section 4.2). Absent on a server that does not say how a
   * score is made up -- and then the inspector says so in words, rather than
   * drawing empty bars.
   */
  axes?: { axis: string; value: number | null; coverage: number; contribution: number }[];
  /** the score before health and trim; `score` stays the final number */
  raw?: number;
  health?: number;
  /** what trim multiplied by; 1 when no prefix_weights apply */
  factor?: number;
  confidence?: number;
  cost_per_task?: number | null;
  cost_from?: 'shape' | 'telemetry' | null;
}

/**
 * What these weights would ship, without shipping it.
 *
 * `models` is the list itself -- the top `ship` reachable models in score
 * order -- and `next` the ten behind it, so "show more" needs no second call.
 */
export interface PreviewResult {
  profile: string;
  mode?: ProfileMode;
  ship: number;
  models: Listed[];
  next: Listed[];
  /** pinned or listed models a need keeps out, shown in place */
  blocked?: Listed[];
  removed?: Listed[];
  /** pinned or listed ids nothing reachable serves */
  missing?: { id: string; name: string }[];
  failed_needs?: number;
  /** every reachable model of this modality, for picking by hand */
  pool?: Listed[];
  /** router ids that matched nothing: link one to pin it */
  unlinked?: { local_id: string; name: string }[];
  settings: ProfileSettings;
  computed_at: string;
  warnings: string[];
}

/** What `apply` shipped, what it is called, and where it went. */
export interface ApplyOutcome {
  chain: Chain;
  /** the combo names written, e.g. `sieve-coder` */
  combos: string[];
  models: { id: string; name: string }[];
  shipped_at: string;
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

/** Every modality, for a form that has to ask which one a model is. */
export const MODALITY_OPTIONS: Modality[] = [
  'llm',
  'text-to-image',
  'image-editing',
  'text-to-video',
  'image-to-video',
  'video-editing',
  'text-to-speech',
  'speech-to-text',
  'speech-to-speech',
  'music'
];

/**
 * `GET /v1/unscored`: a model a router serves that no source has benchmarked.
 * `no_match` means the router's id matched nothing in the catalogue;
 * `no_scores` means it matched a model nobody has measured.
 */
export interface UnscoredRow {
  local_id: string;
  model_id: string | null;
  name: string;
  modality: Modality | null;
  reason: 'no_match' | 'no_scores';
  /** axis -> 0..1, given by hand */
  hand: Record<string, number>;
  router: string;
}

/** `PUT /v1/hand-scores`: 0..1 per axis, `null` clears one. */
export interface HandScoresBody {
  local_id: string;
  modality: Modality;
  scores: Record<string, number | null>;
  name?: string;
}

/**
 * One seat, as the seats list and the seat header need it (CONSOLE.md
 * section 4.1).
 *
 * Everything a row shows comes in one request, `live` and `lineup` are the two
 * orders a seat can have -- what the gateway holds now, and what the saved
 * settings would ship right now -- and both are `null` when nobody has stored
 * one. `in_step` is the comparison, and it is `null` whenever either side is,
 * because "the same" and "no idea" are different answers.
 */
export interface SeatRow {
  name: string;
  modality: Modality;
  purpose: string;
  mode: 'auto' | 'manual';
  ship: number;
  /** what the gateway holds now, in order; null when no chain was ever stored */
  live: { id: string; name: string }[] | null;
  /** what the SAVED settings would ship now, in order; null when there is no stored ranking */
  lineup: { id: string; name: string }[] | null;
  /** lineup ids == live ids, in order. null whenever either side is null */
  in_step: boolean | null;
  /** how many ids differ (added + removed + moved); null with in_step */
  changes: number | null;
  shipped_at: string | null;
}

/** The seats list, and whether it came from the one request that answers it. */
export interface SeatsResult {
  rows: SeatRow[];
  /**
   * true when this is the fallback path (section 4.1's 404): rows from
   * `/v1/profiles` and one chain call per seat, with no lineup, so no seat
   * carries a badge and the list says so at the top.
   */
  degraded: boolean;
}

/**
 * What one model can do and who says so, its posted price, and where it is
 * served (CONSOLE.md section 4.3).
 *
 * `abilities` always carries all four needs; `yes` and `no` name the sources
 * that said so, so the inspector can show a claim and its author together.
 */
export interface ModelCard {
  id: string;
  name: string;
  creator: string;
  modality: Modality;
  effort: string | null;
  family: string | null;
  price: {
    unit: Unit;
    input: number | null;
    output: number | null;
    per_unit: number | null;
    source: string;
    observed_at: string;
  } | null;
  /** one entry per need, always all four */
  abilities: Record<Need, { answer: boolean | null; yes: string[]; no: string[] }>;
  context_window: number | null;
  served_by: { local_id: string; prefix: string; inventory: string; stale: boolean }[];
  scored: boolean;
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
  guide: (o?: RequestOptions) => request<string>('/v1/guide', { ...o, responseType: 'text' }),
  aliases: (o?: RequestOptions) => request<AliasRow[]>('/v1/aliases', o),
  removeAlias: (alias: string, modality: Modality, o?: RequestOptions) =>
    request<{ deleted: string }>(`/v1/aliases/${encodeURIComponent(alias)}${q({ modality })}`, {
      ...o, method: 'DELETE'
    }),
  removeCostMultiplier: (prefix: string, o?: RequestOptions) =>
    request<{ deleted: string }>(`/v1/cost-multipliers/${encodeURIComponent(prefix)}`, {
      ...o, method: 'DELETE'
    }),
  status: (o?: RequestOptions) => request<StatusRow>('/v1/status', o),

  /* -- who is calling, and the loop under hand control (AMS-31) ----------- */

  /** AMS-28 lands `/v1/me`; until then `/v1/status` carries the same answer. */
  me: (o?: RequestOptions) => request<MeRow>('/v1/me', o),

  schedules: (o?: RequestOptions) => request<ScheduleRow[]>('/v1/schedules', o),

  saveSchedule: (
    step: Step,
    body: { mode: string; at_minute?: number; at_time?: string },
    o?: RequestOptions
  ) => request<ScheduleRow>(`/v1/schedules/${step}`, { ...o, method: 'PUT', body }),

  /** 202 with the new row, or 409 with `run_in_flight` when one is going. */
  startRun: (step: Step, o?: RequestOptions) =>
    request<RunRow>(`/v1/runs/${step}`, { ...o, method: 'POST' }),

  runs: (limit = 20, step?: Step, o?: RequestOptions) =>
    request<RunRow[]>(`/v1/runs${q({ limit, step })}`, o),

  run: (id: string, o?: RequestOptions) => request<RunRow>(`/v1/runs/${encodeURIComponent(id)}`, o),

  /** The log is text, not JSON, so it does not go through `request`. */
  runLog: async (id: string, o?: RequestOptions): Promise<Result<string>> => {
    const run = o?.fetch ?? globalThis.fetch;
    try {
      const reply = await run(`${API_BASE}/v1/runs/${encodeURIComponent(id)}/log`, {
        credentials: 'same-origin',
        headers: o?.token ? { authorization: `Bearer ${o.token}` } : {}
      });
      const text = await reply.text();
      if (!reply.ok) {
        return fail({ code: `http_${reply.status}`, message: reply.statusText, status: reply.status });
      }
      return ok(text);
    } catch (cause) {
      return fail({
        code: 'unreachable',
        message: cause instanceof Error ? cause.message : 'the API did not answer',
        status: 0
      });
    }
  },

  modalities: (o?: RequestOptions) => request<ModalityCount[]>('/v1/modalities', o),

  axes: (modality?: Modality, o?: RequestOptions) =>
    request<AxisRow[]>(`/v1/axes${q({ modality })}`, o),

  axis: (name: string, modality?: Modality, o?: RequestOptions) =>
    request<AxisRow>(`/v1/axes/${encodeURIComponent(name)}${q({ modality })}`, o),

  createAxis: (body: Axis, o?: RequestOptions) =>
    request<AxisRow>('/v1/axes', { ...o, method: 'POST', body }),

  updateAxis: (name: string, body: Axis, o?: RequestOptions) =>
    request<AxisRow>(`/v1/axes/${encodeURIComponent(name)}`, { ...o, method: 'PUT', body }),

  removeAxis: (name: string, modality: Modality, force = false, o?: RequestOptions) =>
    request<{ deleted: string; profiles_zeroed: string[] }>(
      `/v1/axes/${encodeURIComponent(name)}${q({ modality, force })}`,
      { ...o, method: 'DELETE' }
    ),

  models: (
    params: { modality?: Modality; reachable?: boolean; q?: string; limit?: number; cursor?: string } = {},
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

  /**
   * Every seat in one request (CONSOLE.md section 4.1), replacing the two calls
   * per seat the list used to make.
   *
   * On an older server (404) it falls back to that older pair, and says so: the
   * rows then have no lineup, so no seat carries an "n changes" badge and the
   * list says at the top that it is provisional. The chain holds ids, not
   * names, so on that path the id stands in for the name rather than the client
   * inventing one from a prefix.
   */
  seats: async (o?: RequestOptions): Promise<Result<SeatsResult>> => {
    const direct = await request<SeatRow[]>('/v1/seats', o);
    if (direct.ok) return ok({ rows: direct.value, degraded: false });
    if (direct.error.status !== 404) return fail(direct.error);

    const profiles = await request<Profile[]>('/v1/profiles', o);
    if (!profiles.ok) return fail(profiles.error);

    const rows: SeatRow[] = [];
    for (const profile of profiles.value) {
      const chain = await request<Chain>(`/v1/chains/${encodeURIComponent(profile.name)}`, o);
      const live = chain.ok
        ? [chain.value.primary, ...(chain.value.fallbacks ?? [])].map((id) => ({ id, name: id }))
        : null;
      rows.push({
        name: profile.name,
        modality: profile.modality,
        purpose: profile.purpose,
        mode: profile.mode ?? 'auto',
        ship: profile.ship ?? 0,
        live,
        lineup: null,
        in_step: null,
        changes: null,
        shipped_at: chain.ok ? chain.value.computed_at : null
      });
    }
    return ok({ rows, degraded: true });
  },

  /** One model's card: what it can do, who says so, its price, where it is served (§4.3). */
  modelCard: (id: string, modality: Modality, o?: RequestOptions) =>
    request<ModelCard>(`/v1/model-card${q({ id, modality })}`, o),

  /** The one word a seat's purpose is: the whole sentence, replaced. */
  setPurpose: (name: string, purpose: string, o?: RequestOptions) =>
    request<Profile>(`/v1/profiles/${encodeURIComponent(name)}`, {
      ...o,
      method: 'PATCH',
      body: { purpose }
    }),

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
  sourceFields: (name: string, o?: RequestOptions) =>
    request<SourceFieldRow[]>(`/v1/sources/${encodeURIComponent(name)}/fields`, o),
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

  saveProfileSettings: (name: string, body: SettingsPatch, o?: RequestOptions) =>
    request<ProfileSettings>(`/v1/profiles/${encodeURIComponent(name)}/settings`, {
      ...o,
      method: 'PUT',
      body
    }),

  /**
   * The list these weights would ship, without shipping it. Meant to be fast
   * and called often: the page debounces 400 ms and sends what it holds.
   */
  preview: (name: string, settings: SettingsPatch, o?: RequestOptions) =>
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

  /**
   * The whole profile back, as `PUT /v1/profiles/{name}`.
   *
   * This is the only route that writes a profile's `purpose` -- the sentence
   * under its name -- so the editable description sends the profile it just
   * read with that one field changed, rather than a patch the server has no
   * route for.
   */
  saveProfile: (name: string, profile: Profile, o?: RequestOptions) =>
    request<Profile>(`/v1/profiles/${encodeURIComponent(name)}`, {
      ...o,
      method: 'PUT',
      body: profile
    }),

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

  /**
   * Set the defaults, by prefix. Merged: send only the prefixes that changed.
   *
   * The prefixes themselves are Sieve's own -- it derives them from the local
   * ids the connectors serve -- so a screen offers the ones that come back and
   * never invents one.
   */
  saveCostMultipliers: (values: Record<string, number>, o?: RequestOptions) =>
    request<Record<string, number>>('/v1/cost-multipliers', {
      ...o,
      method: 'PUT',
      body: values
    }),

  unscored: (o?: RequestOptions) => request<UnscoredRow[]>('/v1/unscored', o),

  linkUnscored: (
    body: { local_id: string; modality: string; name?: string },
    o?: RequestOptions
  ) =>
    request<{ model_id: string; local_id: string }>('/v1/unscored/link', {
      ...o,
      method: 'POST',
      body
    }),

  saveHandScores: (body: HandScoresBody, o?: RequestOptions) =>
    request<{ model_id: string; hand: Record<string, number> }>('/v1/hand-scores', {
      ...o,
      method: 'PUT',
      body
    }),

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

/**
 * `/v1/events` as a callback, unsubscribed by the returned function.
 *
 * `EventSource` carries the browser's cookies on its own and cannot be given
 * a bearer header, so gate v2's edge rule applies to it exactly as it does
 * to any other read: no session behind the edge closes the stream with a
 * 401. `EventSource` gives no way to read that status code off an `error`
 * event, though, so the only way to tell "gate says no" apart from "the
 * network hiccuped" is to ask gate directly (AUTH-CONTRACT.md section 7).
 */
export function subscribe(
  onEvent: (kind: string, data: unknown) => void,
  kinds: string[] = ['pull', 'ranking', 'decision', 'apply']
): () => void {
  if (typeof EventSource === 'undefined') return () => {};
  const source = new EventSource(`${API_BASE}/v1/events`);
  const onError = (): void => {
    void (async () => {
      try {
        const reply = await fetch('/auth/me', { credentials: 'same-origin' });
        if (reply.status === 401) redirectToLogin();
      } catch {
        // Off, or unreachable, or there is no gate in front of this box at
        // all (every local dev checkout) -- none of those is "gate said no",
        // so the stream is left to `EventSource`'s own retry.
      }
    })();
  };
  source.addEventListener('error', onError);
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
    source.removeEventListener('error', onError);
    for (const [kind, handler] of listeners) {
      source.removeEventListener(kind, handler as EventListener);
    }
    source.close();
  };
}
