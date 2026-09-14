/**
 * Who did it, as a person rather than a record.
 *
 * Gate hands identities over as `gate:me@example.com`: the prefix says which
 * authority vouched for the name, which matters to the server and to nobody
 * reading a page. The rail has trimmed it since gate landed; the runs list
 * showed the raw record, so the same person appeared under two names on two
 * screens. One helper, so they cannot drift again.
 */
export function person(name: string | null | undefined): string {
  const raw = (name ?? '').trim();
  const bare = raw.startsWith('gate:') ? raw.slice(5) : raw;
  return bare.includes('@') ? bare.split('@')[0] : bare;
}
