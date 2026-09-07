# Sieve

Weighted model selection for agent fleets — language, image, video, speech
and music models.

Sieve pulls measured benchmark data from sources you trust (Artificial
Analysis, OpenRouter, Arena, LiveBench, Epoch AI, your own CSVs), turns the
numbers into a small set of meaningful axes, scores every model against
*profiles* — one per role your agents play — intersects the result with the
models you can actually reach, and exports ranked lists and fallback chains
to wherever you route traffic. Your agents can steer it through a REST API
and an MCP server, so selection keeps improving without you in the loop.

Status: **phase 1 in progress**. See [PLAN.md](PLAN.md) and
[CONTRACTS.md](CONTRACTS.md).

## Quickstart

Coming with phase 1. The intended shape:

```bash
uv sync
cp .env.example .env            # add ARTIFICIAL_ANALYSIS_API_KEY for AA; OpenRouter needs no key
sieve pull openrouter
sieve pull aa_llm aa_media
sieve score --profile coder
sieve serve                     # API on :8110, web app on the same port
```

## Licence

MIT.
