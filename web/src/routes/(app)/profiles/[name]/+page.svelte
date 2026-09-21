<!--
  The redirect lives in `./+layout.ts` and runs before SvelteKit renders
  anything, so nothing here is ever drawn. This file exists because a directory
  with a loader but no page is not a route at all: the load never runs and the
  URL 404s. `/profiles/<name>` was a 404 for exactly that reason.
-->
