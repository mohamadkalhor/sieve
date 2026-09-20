/**
 * The model card, read once per model and kept (CONSOLE.md sections 4.3 and
 * 5.3, REVIEW.md finding 9).
 *
 * The inspector asks one question about one model, and the card is the answer:
 * what it can do and who said so, the price, and which router ids serve it.
 * Three things in here are load-bearing.
 *
 * **The key is the model and the modality.** A card is about a model *in a
 * modality* -- the same id can be listed as an image model and as an llm -- so
 * two seats never share an answer by accident, and a re-render never re-asks.
 *
 * **A 404 is not a failure.** The route answers 404 when it has no card, and
 * section 4.3 says what to draw then: the abilities come from the preview row
 * (`Listed.abilities`, with the "who" column reading "source not reported",
 * because something did answer) and the price is looked up again by exact id
 * and modality. That lookup is a second question, and it can fail on its own --
 * which is why its error is kept *apart* from the card's. Without that, a
 * lookup that failed would render as "No posted price", turning a question
 * nobody answered into a fact about the model.
 *
 * **An error keeps what is already on screen.** A failed read is said out loud
 * and can be asked again; it never blanks a card that was read a moment ago.
 */
import { api as defaultApi } from '$lib/api/client';
import type {
  ApiError,
  Listed,
  ModelCard,
  ModelRow,
  RequestOptions,
  Result
} from '$lib/api/client';
import { NEEDS } from '$lib/api/client';
import { session } from '$lib/session.svelte';
import type { Modality, Unit } from '$lib/types';
import type { CardCacheLike, CardState } from '../contracts';

/** The slice of the client this cache calls, so a test can answer it by hand. */
export interface CardApi {
  modelCard(id: string, modality: Modality, o?: RequestOptions): Promise<Result<ModelCard>>;
  models(
    params: { modality?: Modality; reachable?: boolean; q?: string; limit?: number; cursor?: string },
    o?: RequestOptions
  ): Promise<Result<{ items: ModelRow[]; next_cursor: string | null }>>;
}

/**
 * What a card read borrows from its surroundings: the client, and the bearer
 * token this browser has. Both injected, so the class has no global inside it.
 */
export interface CardCacheDeps {
  api: CardApi;
  token(): string | undefined;
}

/** The deps a browser uses. */
export function browserCardDeps(): CardCacheDeps {
  return {
    api: defaultApi as unknown as CardApi,
    token: () => session.token || undefined
  };
}

/** Everything the inspector needs about one read, including how to ask again. */
export interface CardRead {
  card: ModelCard | null;
  state: CardState;
  /** why the read failed: `state === 'error'` means this is the reason */
  error: ApiError | null;
  /**
   * The fallback's price lookup failed. Kept apart from `error` because it is
   * one block's absence, not a failed card -- and because "no posted price" is
   * only sayable when the lookup actually came back saying so.
   */
  priceError: ApiError | null;
  /** Ask again, keeping whatever is already on screen while it runs. */
  retry(): void;
}

interface Entry {
  card: ModelCard | null;
  state: CardState;
  error: ApiError | null;
  priceError: ApiError | null;
  /** whether a fallback card was built from a preview row; without one, it is bare */
  fromRow: boolean;
}

function loading(): Entry {
  return { card: null, state: 'loading', error: null, priceError: null, fromRow: false };
}

/** How many pages of `/v1/models` the fallback's exact-match hunt may read. */
const LOOKUP_PAGE = 50;
const LOOKUP_PAGES = 5;

/** The source of a router id: everything before its first slash. */
export function prefixOf(localId: string): string {
  const cut = localId.indexOf('/');
  return cut > 0 ? localId.slice(0, cut) : localId;
}

/** One need of a preview row as a card-shaped answer, with no authors named. */
export function rowAbilities(row: Listed | null): ModelCard['abilities'] {
  const abilities = {} as ModelCard['abilities'];
  for (const need of NEEDS) {
    abilities[need] = { answer: row?.abilities?.[need] ?? null, yes: [], no: [] };
  }
  return abilities;
}

/**
 * The card section 4.3 draws when the route has none.
 *
 * `row` is the preview row, when the seat has answered with one. Its
 * `abilities` are the same answers the card would carry -- the server asserts
 * they agree -- and with no sources named the "who" column reads "source not
 * reported". The price comes from the exact-match lookup, never from the row's
 * own list entry: that entry is a listing, not this model's posted price.
 */
export function fallbackCard(
  row: Listed | null,
  id: string,
  modality: Modality,
  price: ModelCard['price']
): ModelCard {
  return {
    id,
    name: row?.name ?? id,
    // The route that would say these is the one that answered 404; the card
    // says nothing rather than guessing a creator or a context window.
    creator: '',
    modality,
    effort: null,
    family: null,
    price,
    abilities: rowAbilities(row),
    context_window: null,
    served_by: (row?.local_ids ?? []).map((local_id) => ({
      local_id,
      prefix: prefixOf(local_id),
      // The inventory a listing came from is not in `Listed`; the id itself is.
      inventory: '',
      stale: false
    })),
    scored: row?.scored ?? false
  };
}

export class CardCache implements CardCacheLike {
  /**
   * The answers, keyed by modality and id. Only ever written from an answer's
   * callback -- never while a template is rendering -- so reading a card during
   * render is safe.
   */
  private entries = $state<Record<string, Entry>>({});
  /** keys with a read in flight, so a re-render does not ask twice */
  private reading = new Set<string>();
  private deps: CardCacheDeps;

  constructor(deps: CardCacheDeps = browserCardDeps()) {
    this.deps = deps;
  }

  /**
   * The card for one model, starting the read when there is not one yet.
   *
   * `row` is the preview row, when there is one: the fallback needs it for the
   * abilities and the router ids. A fallback built before the preview arrived
   * is read once more when the row turns up, rather than drawn emptier than
   * what the app already knows.
   */
  get(id: string, modality: Modality, row?: Listed | null): CardRead {
    const key = this.key(modality, id);
    const held = this.entries[key];
    const near = row ?? null;
    if (!held || (held.state === 'fallback' && !held.fromRow && near)) {
      this.ask(key, id, modality, near);
    }
    const entry = this.entries[key] ?? loading();
    return {
      card: entry.card,
      state: entry.state,
      error: entry.error,
      priceError: entry.priceError,
      retry: () => this.again(key, id, modality, near)
    };
  }

  private key(modality: Modality, id: string): string {
    return `${modality}\u0000${id}`;
  }

  private options(): RequestOptions {
    const token = this.deps.token();
    return token ? { token } : {};
  }

  private ask(key: string, id: string, modality: Modality, row: Listed | null): void {
    if (this.reading.has(key)) return;
    this.reading.add(key);
    void this.read(key, id, modality, row).finally(() => this.reading.delete(key));
  }

  /** Ask again: the old card stays on screen while the new read runs. */
  private again(key: string, id: string, modality: Modality, row: Listed | null): void {
    const held = this.entries[key];
    this.hold(key, { ...loading(), card: held?.card ?? null, fromRow: held?.fromRow ?? false });
    this.ask(key, id, modality, row);
  }

  private hold(key: string, entry: Entry): void {
    this.entries = { ...this.entries, [key]: entry };
  }

  private async read(
    key: string,
    id: string,
    modality: Modality,
    row: Listed | null
  ): Promise<void> {
    const answer = await this.deps.api.modelCard(id, modality, this.options());
    if (answer.ok) {
      this.hold(key, {
        card: answer.value,
        state: 'ready',
        error: null,
        priceError: null,
        fromRow: true
      });
      return;
    }

    if (answer.error.status !== 404) {
      // A wall, not an absence: the card says nothing about the model, and the
      // pane says what happened instead of drawing an empty one.
      this.hold(key, {
        card: this.entries[key]?.card ?? null,
        state: 'error',
        error: answer.error,
        priceError: null,
        fromRow: false
      });
      return;
    }

    const lookup = await this.priceFor(id, modality);
    this.hold(key, {
      card: fallbackCard(row, id, modality, lookup.price),
      state: 'fallback',
      error: null,
      priceError: lookup.error,
      fromRow: row !== null
    });
  }

  /**
   * This model's posted price, by exact id and modality.
   *
   * `/v1/models` matches loosely -- `q` is a search -- so the first row is not
   * the answer; the hunt follows `next_cursor` until a row carries the exact id
   * *and* the modality asked about. A listing whose modality is `null` is a
   * listing that did not report one, not a listing for another modality, so it
   * counts -- refusing it would turn a posted price into "no price posted".
   * Running out of rows is a successful answer of "no price posted"; a failed
   * page is not, and its error travels back with the null so the price block can
   * tell the two apart.
   */
  private async priceFor(
    id: string,
    modality: Modality
  ): Promise<{ price: ModelCard['price']; error: ApiError | null }> {
    let cursor: string | undefined;
    for (let page = 0; page < LOOKUP_PAGES; page += 1) {
      const answer = await this.deps.api.models(
        { q: id, modality, limit: LOOKUP_PAGE, cursor },
        this.options()
      );
      if (!answer.ok) return { price: null, error: answer.error };
      const match = answer.value.items.find(
        (row) => row.id === id && (row.modality === null || row.modality === modality)
      );
      if (match) {
        if (!match.price) return { price: null, error: null };
        return {
          price: {
            // The listing carries the unit as text; the card's `Unit` is the
            // same word from the same server.
            unit: match.price.unit as Unit,
            input: match.price.input ?? null,
            output: match.price.output ?? null,
            per_unit: match.price.per_unit ?? null,
            // A listing carries no source and no observation time, and nothing
            // in the pane draws them: the empty strings are the type's shape,
            // not a claim about where this price came from.
            source: '',
            observed_at: ''
          },
          error: null
        };
      }
      if (!answer.value.next_cursor) break;
      cursor = answer.value.next_cursor;
    }
    return { price: null, error: null };
  }
}

/**
 * The cache the console reads cards through: one per app load, made on first
 * use.
 *
 * `context.ts` hands the key to the seat route ("provided by the seat route,
 * read by the inspector"), so a shell that provides one there replaces this
 * with `cards()`. Until then the panel reads this, which is the same thing one
 * level down: a model opened, closed and opened again is read once, and its
 * card stays on screen while the next read runs (CONSOLE.md section 5.3,
 * REVIEW.md finding 9).
 */
let app: CardCache | null = null;

export function cardCache(): CardCache {
  if (app === null) app = new CardCache();
  return app;
}
