// Rankings and Chains are gone: both were one column of what the Profiles
// screen now shows whole, and keeping three pages over one object meant three
// places to look and two of them stale.
//
// The URLs stay, because they are in people's bookmarks and in the decision
// log. They land on the seats list.
import { redirect } from '@sveltejs/kit';

export const load = () => {
  redirect(307, '/seats');
};
