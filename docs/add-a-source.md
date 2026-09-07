# Add a source

A source is a plugin that pulls measurements. Sieve never calls a model; it
reads what somebody else measured, so a source is a read of a public feed, a
download, or a directory of files you dropped there yourself.

## The shape

```python
from typing import ClassVar

from sieve.contracts import HttpClient, Modality, PullResult, SourceConfig
from sieve.sources.base import make_observation, utcnow


class MyBenchSource:
    name = "mybench"
    modality: ClassVar[list[Modality]] = ["llm"]
    needs_key = True

    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult:
        at = utcnow()
        result = PullResult(source=self.name)

        response = http.get(URL, headers={"x-api-key": cfg.key() or ""})
        if response.status != 200:
            result.warnings.append(f"mybench: HTTP {response.status}")
            return result                      # a bad pull warns; it never raises

        for entry in response.body["models"]:
            ...
        result.rate_limit = http.rate_limit()
        return result
```

Register it:

```toml
[project.entry-points."sieve.sources"]
mybench = "sieve.sources.mybench:MyBenchSource"
```

That is the whole contract. `sieve pull mybench` now works, and the Sources
screen lists it.

## Four rules that are not negotiable

**All network through the `http` you are handed.** It sets timeouts, retries
with backoff on 429 and 5xx, and records `x-ratelimit-*`. In tests it is a
fixture player, which is why nothing in the suite touches the network. Reach for
`httpx` directly and you have opted out of all of that.

**A key comes from `cfg.key()`, never from the config file.** `sieve.toml` names
the environment variable; it never holds the value.

**Store a field you do not recognise.** A benchmark reaches a site before it
reaches the docs. Store it under its own name and add a warning listing what was
new — someone can then point an axis at it without waiting for a release:

```python
if name not in KNOWN_FIELDS:
    unknown.add(name)
result.warnings.append(f"mybench: stored new field(s): {', '.join(sorted(unknown))}")
```

**A missing value is missing.** `make_observation` returns `None` when the
source published nothing, and you drop it. Never substitute a zero — a model
that was not measured would then rank as the worst one measured.

## Record a fixture

```python
from sieve.http import Http, fixture_slug
body = Http().get(URL).body
# trim to 60 models or fewer, then:
open(f"tests/fixtures/{fixture_slug(URL)}.json", "w").write(json.dumps(body))
```

Strip every key and auth header before you commit it, and check:

```bash
grep -rnE 'sk-[A-Za-z0-9_-]{20,}|"(authorization|x-api-key)"' tests/fixtures/
```

The player picks the file up by URL with no extra code, and `SIEVE_FIXTURES=1`
makes the CLI and the API use it too.

## What belongs in an observation

| field | meaning |
|---|---|
| `field` | the source's own name for it, **verbatim** — an axis names this |
| `unit` | one of the `Unit` literals in `contracts.py` |
| `n` | sample size or appearances, if published — `min_n` in an axis reads it |
| `ci95` | the confidence interval, as a number |
| `observed_at` | when the *source* published it, not when you pulled |

`observed_at` matters: observations are unique on
`(model_id, source, field, observed_at)`, so a re-pull of unchanged data adds
nothing, and a genuinely new measurement is a new row beside the old one.
