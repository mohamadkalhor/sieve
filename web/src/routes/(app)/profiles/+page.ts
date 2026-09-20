// The profiles list is gone: it was the same one-object-per-seat list the seats
// screen now shows, and a seat is a page of its own (`/seats/[name]`).
//
// The URL stays, because it is in people's bookmarks and in the guide's own
// link to it. It lands on the seats list, exactly as `/rankings` and `/chains`
// have since the console took the list over.
import { redirect } from '@sveltejs/kit';

export const load = () => {
  redirect(307, '/seats');
};
