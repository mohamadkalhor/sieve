/**
 * `api.pull` with the agent kit on and off: a 200 is the result, a 202 is a job
 * that is polled until it is finished and then answers with the same result.
 */
import { describe, expect, it, vi } from 'vitest';
import { api } from '../src/lib/api/client';

const RESULT = { job: 'abc', source: 'aa', added: 3, warnings: [], snapshot: 's1' };

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' }
  });
}

const quick = { pollMs: 1, sleep: async () => {} };

describe('api.pull', () => {
  it('answers a 200 as it always did, without polling', async () => {
    const fetch = vi.fn(async () => reply(200, { ...RESULT, snapshot: undefined }));
    const got = await api.pull('aa', { fetch, ...quick });
    expect(got.ok && got.value.added).toBe(3);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('polls a 202 until the job succeeds and returns its result', async () => {
    const rows = [
      { id: 'j1', status: 'queued' },
      { id: 'j1', status: 'running' },
      { id: 'j1', status: 'succeeded', result: RESULT }
    ];
    const fetch = vi.fn(async (url: RequestInfo | URL) =>
      String(url).endsWith('/pull')
        ? reply(202, { job: { id: 'j1', status: 'queued', poll: '/v1/jobs/j1' } })
        : reply(200, { job: rows.shift() })
    );
    const got = await api.pull('aa', { fetch, ...quick });
    expect(got).toEqual({ ok: true, value: RESULT });
    expect(String(fetch.mock.calls[1][0])).toContain('/v1/jobs/j1');
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it('turns a failed job into an error carrying its own code', async () => {
    const fetch = vi.fn(async (url: RequestInfo | URL) =>
      String(url).endsWith('/pull')
        ? reply(202, { job: { id: 'j2', status: 'queued', poll: '/v1/jobs/j2' } })
        : reply(200, {
            job: { id: 'j2', status: 'failed', error: { code: 'store_busy', message: 'busy' } }
          })
    );
    const got = await api.pull('aa', { fetch, ...quick });
    expect(got.ok).toBe(false);
    if (!got.ok) expect(got.error).toMatchObject({ code: 'store_busy', message: 'busy' });
  });

  it('gives up waiting, saying the pull is still going', async () => {
    const fetch = vi.fn(async (url: RequestInfo | URL) =>
      String(url).endsWith('/pull')
        ? reply(202, { job: { id: 'j3', status: 'queued', poll: '/v1/jobs/j3' } })
        : reply(200, { job: { id: 'j3', status: 'running' } })
    );
    const got = await api.pull('aa', { fetch, ...quick, timeoutMs: 0 });
    expect(got.ok).toBe(false);
    if (!got.ok) expect(got.error.code).toBe('pull_pending');
  });

  it('passes a refused submission through', async () => {
    const fetch = vi.fn(async () => reply(409, { error: { code: 'source_disabled', message: 'off' } }));
    const got = await api.pull('aa', { fetch, ...quick });
    expect(got.ok).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
