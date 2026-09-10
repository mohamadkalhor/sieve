import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  server: {
    /**
     * In production the API and this app are one origin, so the client asks
     * for `/v1/...` with no base. Reproducing that in dev through a proxy —
     * rather than pointing `PUBLIC_SIEVE_API` at the API's own port — means
     * the dev server exercises the same relative paths as the built site, and
     * nobody has to add a CORS origin to a config file to work on the UI.
     *
     * `SIEVE_API` overrides the target, for an API on another host or tunnel.
     */
    proxy: {
      '/v1': {
        target: process.env.SIEVE_API || 'http://127.0.0.1:8111',
        changeOrigin: true
      }
    }
  },
  test: {
    include: ['tests/**/*.test.ts', 'src/**/*.test.ts'],
    exclude: ['e2e/**', 'node_modules/**'],
    environment: 'node'
  }
});
