import { defineConfig, devices } from '@playwright/test';

const PORT = 8110;

/**
 * The smoke test runs against a real `sieve serve` with the built web app in
 * front of it, because the things worth smoke-testing -- a deep link, the
 * slider re-ranking, a 401 shown politely -- are exactly the things that only
 * break once the two halves are wired together.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? 'list' : 'html',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure'
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // seeds a throwaway store from the fixtures, then serves it
    command: `node e2e/seed-and-serve.mjs`,
    url: `http://127.0.0.1:${PORT}/healthz`,
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: 'pipe',
    stderr: 'pipe',
    env: {
      SIEVE_PORT: String(PORT),
      SIEVE_TOKENS: process.env.SIEVE_TOKENS ?? 'ci:read,profiles:write,apply,telemetry:ci-secret'
    }
  }
});
