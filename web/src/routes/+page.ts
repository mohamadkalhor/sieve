// The console opens on the seats list (CONSOLE.md section 6.1). `/field` is
// still the running picture of the field, and the top bar names it; it is no
// longer the front door, because the front door is where you work.
import { redirect } from '@sveltejs/kit';

export const load = () => {
  redirect(307, '/seats');
};
