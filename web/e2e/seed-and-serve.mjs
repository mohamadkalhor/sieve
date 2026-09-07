/**
 * Seed a throwaway store from the recorded fixtures, then serve it.
 *
 * The smoke test is only worth running against data, and the data has to be
 * the same every time or the assertions are folklore. So: a temporary
 * sieve.toml, a pull from `tests/fixtures`, a plan, and then the real server
 * with the built web app in front of it. Nothing here touches the network and
 * nothing here touches your own store.
 */
import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const port = process.env.SIEVE_PORT ?? '8110';
const repo = resolve(import.meta.dirname, '../..');
const work = mkdtempSync(join(tmpdir(), 'sieve-e2e-'));
const config = join(work, 'sieve.toml');

const toml = `
[store]
path = ${JSON.stringify(join(work, 'sieve.db'))}

[server]
host = "127.0.0.1"
port = ${port}
web = ${JSON.stringify(join(repo, 'web', 'build'))}

[paths]
axes = ${JSON.stringify(join(repo, 'data', 'axes'))}
profiles = ${JSON.stringify(join(work, 'profiles'))}
out = ${JSON.stringify(join(work, 'out'))}

[sources.openrouter]
enabled = true
modalities = ["llm"]

[sources.aa_llm]
enabled = true
key_env = "ARTIFICIAL_ANALYSIS_API_KEY"
modalities = ["llm"]

[sources.aa_media]
enabled = true
key_env = "ARTIFICIAL_ANALYSIS_API_KEY"
modalities = ["text-to-video", "text-to-image"]

[inventories.gateway]
kind = "list"
models = [
  "anthropic/claude-opus-5", "anthropic/claude-sonnet-5", "openai/gpt-5-2",
  "google/gemini-3-pro", "z-ai/glm-5.3", "deepseek/deepseek-v4",
  "google/veo-4", "openai/sora-3"
]

[targets.out]
kind = "file"
dir = ${JSON.stringify(join(work, 'out'))}
`;
writeFileSync(config, toml, 'utf-8');

// the editor writes profiles back to disk, so give it a copy, not the repo's
spawnSync(process.platform === 'win32' ? 'xcopy' : 'cp',
  process.platform === 'win32'
    ? [join(repo, 'profiles'), join(work, 'profiles'), '/E', '/I', '/Q', '/Y']
    : ['-r', join(repo, 'profiles'), join(work, 'profiles')],
  { stdio: 'ignore' }
);

const env = {
  ...process.env,
  SIEVE_FIXTURES: '1',
  SIEVE_CONFIG: config,
  ARTIFICIAL_ANALYSIS_API_KEY: 'fixture-mode',
  SIEVE_TOKENS: process.env.SIEVE_TOKENS ?? 'ci:read,profiles:write,apply,telemetry:ci-secret'
};

const run = (args) => {
  const result = spawnSync('uv', ['run', 'sieve', '--config', config, ...args], {
    cwd: repo,
    env,
    stdio: 'inherit',
    shell: process.platform === 'win32'
  });
  if (result.status !== 0) {
    console.error(`sieve ${args.join(' ')} failed with ${result.status}`);
    process.exit(1);
  }
};

run(['pull', 'openrouter', 'aa_llm', 'aa_media', 'gateway']);
run(['check']);
run(['plan', '--store']);

const server = spawn('uv', ['run', 'sieve', '--config', config, 'serve', '--port', port], {
  cwd: repo,
  env,
  stdio: 'inherit',
  shell: process.platform === 'win32'
});

const stop = () => {
  server.kill();
  process.exit(0);
};
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
server.on('exit', (code) => process.exit(code ?? 0));
