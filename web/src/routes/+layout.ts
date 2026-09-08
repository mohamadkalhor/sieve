// Static build, no Node server: FastAPI serves `web/build`.
//
// `ssr` is on **for the build only**. There is no Node server in the
// deployment; SSR here means SvelteKit renders each shell once, at build time,
// into a static HTML file. Every screen still reads `/v1` from the browser,
// because the fetches live in `$effect`, which never runs during rendering.
//
// `prerender` is now **true**, which is a different thing and was costing real
// time. Without it, `index.html` was an empty shell and *nothing at all* --
// not even a heading -- appeared until the JS bundle had downloaded, parsed
// and hydrated. Lighthouse measured that on a throttled phone as a largest
// contentful paint of 7.0 s, of which 6.5 s was render delay on a paragraph of
// static text that could have been in the HTML all along.
//
// With prerendering, each route's shell -- nav, heading, the sentence that
// says what the screen is for -- is written into HTML at build time and paints
// immediately. The data still arrives afterwards from `/v1`, because `ssr` is
// off and the effects only run in the browser. Nothing about the deployment
// changes: it is still a directory of static files behind FastAPI.
export const prerender = true;
export const ssr = true;

// A route with a parameter has no build-time list of values, and the fallback
// still serves those. `trailingSlash` keeps the emitted paths matching what the
// SPA fallback expects.
export const trailingSlash = 'never';
