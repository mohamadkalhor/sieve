// The list moved to `/seats` (CONSOLE.md section 6.1): a profile is the stored
// half of a seat now, so the seats list is where you start and the sections in
// the top bar stop naming it. The URL stays because it is in people's
// bookmarks and in the decision log.
import { redirect } from '@sveltejs/kit';

export const load = () => {
  redirect(307, '/seats');
};
