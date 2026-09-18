/**
 * Who is signed in, held once for the whole app.
 *
 * gate v2 signs a person in with a host-only cookie on sieve's own hostname
 * (AUTH-CONTRACT.md section 3) -- same-origin now, not shared with any other
 * app -- and until AMS-28 landed, Sieve could not tell: every screen showed a
 * "Token (needed to change anything)" box to somebody who was already signed
 * in and allowed to change everything. The API can tell -- it asks gate --
 * so the page asks the API.
 *
 * This is Sieve's own `/v1/me`/`/v1/status`, about API scopes; it is a
 * different question from gate's own same-origin `/auth/me`, which the rail
 * asks separately for the sign-in chip, the Admin link and a sign-out's csrf
 * token (see `Rail.svelte`).
 *
 * `/v1/me` is AMS-28's route. Until it lands `/v1/status` carries the same
 * answer under `user`, so this works either way and needs no flag.
 */
import { api, type MeRow, type StatusRow } from '$lib/api/client';

class Session {
  /** the signed-in person, or null for "nobody, as far as the API knows" */
  user = $state<MeRow | null>(null);
  /** false until the first answer: "not signed in" and "not asked yet" differ */
  checked = $state(false);

  /**
   * The pasted bearer token, for a browser gate does not know.
   *
   * One for the whole app rather than one per screen: it used to be `let token
   * = $state('')` on four pages, so pasting it on Profiles and then opening
   * Connectors asked for it again.
   */
  token = $state('');

  /** true once `/v1/me` answered, after which `/v1/status` is not consulted */
  #authoritative = false;

  get signedIn(): boolean {
    return this.user !== null;
  }

  /** Can this person change things without pasting a token? */
  get canWrite(): boolean {
    const scopes = this.user?.scopes;
    return this.user !== null && (!scopes || scopes.includes('profiles:write'));
  }

  async refresh(): Promise<void> {
    const mine = await api.me();
    if (mine.ok && mine.value && typeof mine.value === 'object' && 'name' in mine.value) {
      this.user = mine.value;
      this.#authoritative = true;
    }
    this.checked = true;
  }

  /** Take the identity out of a status poll, unless `/v1/me` already spoke. */
  adopt(status: StatusRow | null): void {
    if (this.#authoritative || !status) return;
    this.user = status.user ?? null;
    this.checked = true;
  }
}

export const session = new Session();
