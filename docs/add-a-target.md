# Add a target

A target is where a chain goes: a file a gateway reads, an HTTP call, a config
block. It is the only part of Sieve that writes outward, so it is the part with
the strictest rules.

## The shape

```python
from sieve.contracts import Chain, TargetConfig, TargetResult
from sieve.targets.base import write_atomic


class MyGatewayTarget:
    name = "mygateway"

    def current(self, cfg: TargetConfig) -> dict[str, list[str]]:
        """What is in place now, so `sieve diff` can show the change.
        Return {} when the target cannot be read back."""
        ...

    def write(self, cfg: TargetConfig, chains: list[Chain], dry_run: bool) -> TargetResult:
        written = []
        for chain in chains:
            path = ...
            written.append(str(path))
            if not dry_run:
                write_atomic(path, render(chain))
        return TargetResult(target=cfg.name, written=written, dry_run=dry_run)
```

```toml
[project.entry-points."sieve.targets"]
mygateway = "sieve.targets.mygateway:MyGatewayTarget"
```

## The rules

**A dry run writes nothing.** `sieve diff` and the Chains screen both call
`write(..., dry_run=True)` to find out what *would* happen. If a dry run has a
side effect, looking becomes doing.

**Write atomically.** `write_atomic` goes through a temporary file and renames,
so a gateway reading the file never sees half a chain. If your target is an API,
prefer one call that replaces the whole set over several that replace parts.

**Report the failure, do not raise.** Set `TargetResult.error` and return. One
unreachable gateway must not abandon the writes to the others.

**Nothing writes without being asked.** A target write happens on `sieve apply
--yes`, or on `POST /v1/apply` with the `apply` scope. The scheduled recompute
never writes unless the profile says `auto_apply: true`. Do not add a code path
that writes during `pull`, `score` or `plan`.

## `current()` earns its keep

The diff a person reads before pressing Apply is only as honest as `current()`.
Where the target genuinely cannot be read back, return `{}` — the screen then
shows everything as an addition, which is the truth. Do not return the chain you
last wrote from memory; that is a diff against your own optimism.

## Testing it

```python
def test_a_dry_run_writes_nothing(tmp_path):
    cfg = TargetConfig(name="out", kind="mygateway", dir=str(tmp_path))
    result = MyGatewayTarget().write(cfg, [chain], True)
    assert result.dry_run and not list(tmp_path.iterdir())
```

Never point a target at a live gateway in a test or an example. Example configs
use `localhost`.
