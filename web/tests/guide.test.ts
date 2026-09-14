import { describe, expect, it } from 'vitest';
import { blocks, inline } from '../src/lib/markdown';
import { api } from '../src/lib/api/client';

 describe('operating guide', () => {
  it('parses headings, paragraphs, lists and fenced JSON without interpreting HTML', () => {
    expect(blocks('# Guide\n\nA **profile**.\n\n- one\n- two\n\n1. export\n2. apply\n\n```json\n{"x":1}\n```\n\n<script>alert(1)</script>')).toEqual([
      { kind: 'heading', level: 1, text: 'Guide' },
      { kind: 'paragraph', text: 'A **profile**.' },
      { kind: 'list', ordered: false, items: ['one', 'two'] },
      { kind: 'list', ordered: true, items: ['export', 'apply'] },
      { kind: 'code', text: '{"x":1}' },
      { kind: 'paragraph', text: '<script>alert(1)</script>' }
    ]);
    expect(inline('Use `GET` and **read**.')).toEqual([
      { kind: 'text', text: 'Use ' }, { kind: 'code', text: 'GET' },
      { kind: 'text', text: ' and ' }, { kind: 'strong', text: 'read' },
      { kind: 'text', text: '.' }
    ]);
  });
  it('fetches Markdown as text and preserves API failures', async () => {
    expect(await api.guide({ fetch: async () => new Response('# Guide') })).toEqual({ ok: true, value: '# Guide' });
    const result = await api.guide({ fetch: async () => new Response(JSON.stringify({ error: { code: 'unauthorized', message: 'Sign in' } }), { status: 401 }) });
    expect(result).toEqual({ ok: false, error: { code: 'unauthorized', message: 'Sign in', status: 401 } });
  });
  it('encodes alias modality and prefixes on deletes', async () => {
    const paths: string[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      paths.push(String(input));
      expect(init?.method).toBe('DELETE');
      return new Response('{}');
    };
    await api.removeAlias('gw/a b', 'llm', { fetch: fetcher });
    await api.removeCostMultiplier('gw/a b', { fetch: fetcher });
    expect(paths).toEqual(['/v1/aliases/gw%2Fa%20b?modality=llm', '/v1/cost-multipliers/gw%2Fa%20b']);
  });
});
