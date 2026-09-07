import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
export default {
  preprocess: vitePreprocess(),
  kit: {
    // FastAPI serves web/build; every route falls back to the SPA shell so a
    // deep link keeps working without a Node server.
    adapter: adapter({ fallback: 'index.html', strict: false }),
    alias: { $lib: 'src/lib' }
  }
};
