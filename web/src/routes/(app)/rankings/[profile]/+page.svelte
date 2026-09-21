<!--
  The redirect lives in `./+page.ts` and runs before SvelteKit renders anything,
  so nothing here is ever drawn. This file exists because a directory with a
  loader but no page is not a route at all: the load never runs and the URL
  404s, which is what happened to `/rankings/<name>`.
-->
