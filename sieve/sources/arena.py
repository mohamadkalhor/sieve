"""LMArena — human preference Elo, for text and for media.

PLAN §11 phase 3, the first of the three "more data" sources. Every other
source Sieve reads is a benchmark: a fixed set of questions, scored. This one
is people choosing between two answers, hundreds of thousands of times, which
measures something the benchmarks cannot — whether the output is the one a
person wanted.

`lmarena-ai/leaderboard-dataset` on Hugging Face, **CC-BY-4.0**, 22 leaderboards
covering text, agentic use, web development, vision, image, image editing and
video. Attribution belongs in `docs/sources.md`; the licence is why this can be
read at all.

**The brief said this had no REST API and had to be a parquet download.** It
does have one: Hugging Face's datasets-server serves the same rows as JSON, and
its `/filter` endpoint narrows the text leaderboard from 10,517 rows to the 399
that are the leaderboard proper. So there is no parquet reader and no `pyarrow`
here — one more heavy dependency avoided by checking rather than assuming.

Two things this source has that no other does:

- **A real publication date.** Every row carries `leaderboard_publish_date`, so
  `observed_at` is when the leaderboard was published rather than when we
  happened to fetch it. `aa_llm` stamps pull time and therefore writes a fresh
  set of rows every hour; this one deduplicates on its own.
- **Effort modes already separated.** The rows are `claude-opus-5-high`,
  `claude-opus-5-max`, `gpt-5-6-sol-medium`. That is PLAN §2.1a's rule arriving
  from the outside, and it is why these ids match ours without special care.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from sieve.catalog.registry import canonical_id
from sieve.contracts import (
    HttpClient,
    Modality,
    ModelRef,
    Observation,
    PullResult,
    SourceConfig,
)
from sieve.sources.base import as_float, as_int, slugify, utcnow

DATASET = "lmarena-ai/leaderboard-dataset"
API = "https://datasets-server.huggingface.co"
INFO = f"https://huggingface.co/api/datasets/{DATASET}"

#: The dataset serves at most 100 rows a request.
PAGE = 100

#: A guard: 400 rows is the whole text leaderboard, and no other config is close.
MAX_PAGES = 40

#: Which leaderboard measures which modality. A config with no modality here is
#: skipped rather than forced: `video_edit` is video-to-video, which Sieve has
#: no name for, and calling it `text-to-video` would be a lie about what the
#: votes were cast on.
CONFIGS: dict[str, Modality] = {
    # every one of these is people judging an LLM's output, but they are
    # different contests and must not share a field name -- the mistake the
    # music endpoints made in part 2
    "text": "llm",
    "agent": "llm",
    "webdev": "llm",
    "vision": "llm",
    "document": "llm",
    "search": "llm",
    "text_to_image": "text-to-image",
    "image_edit": "image-editing",
    "text_to_video": "text-to-video",
    "image_to_video": "image-to-video",
}


def model_id_of(row: dict[str, Any]) -> str | None:
    """`<organization>/<model_name>`, both already slug-shaped in this dataset."""
    name = str(row.get("model_name") or "").strip()
    if not name:
        return None
    org = str(row.get("organization") or "").strip()
    # The arena writes an effort mode in brackets -- "gpt-image-2 (medium)" --
    # and brackets in a canonical id match nothing, ever. Folded to the same
    # `<base>-<mode>` shape Artificial Analysis uses, which is what PLAN 2.1a
    # settled on and what the rest of these rows already look like.
    name = name.replace("(", " ").replace(")", " ")
    return canonical_id(org, "-".join(name.split()))


def ci95_of(row: dict[str, Any]) -> float | None:
    """Half the published interval.

    The dataset gives the bounds rather than the width, and they are not always
    symmetric about the rating, so the wider half is taken -- the same rule
    `parse_ci95` applies to Artificial Analysis's "-12/14".
    """
    lower, upper = as_float(row.get("rating_lower")), as_float(row.get("rating_upper"))
    rating = as_float(row.get("rating"))
    if lower is None or upper is None or rating is None:
        return None
    return max(abs(rating - lower), abs(upper - rating))


def field_for(config: str, category: str | None) -> str:
    """`elo:text`, or `elo:text_to_image:3d_modeling` for a sub-category.

    The config is always in the name. Six of these leaderboards measure `llm`,
    and writing them all as `elo` would mean five of them silently losing to the
    sixth on the `(model, source, field, observed_at)` key.
    """
    base = f"elo:{slugify(config)}"
    if not category or category == "overall":
        return base
    return f"{base}:{slugify(category)}"


class ArenaSource:
    """`Source` for LMArena. Human preference Elo; no prices, no capabilities."""

    name = "arena"
    needs_key = False
    modalities: ClassVar[list[Modality]] = sorted(set(CONFIGS.values()))

    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult:
        result = PullResult(source=self.name)
        pulled_at = utcnow()
        wanted = set(cfg.modalities or self.modalities)
        configs = [c for c, m in CONFIGS.items() if m in wanted]

        # Every category, or just the leaderboard proper. `overall` is 399 rows
        # of text against 10,517 for all categories, so the default is the one
        # people mean by "the Arena leaderboard".
        all_categories = bool(cfg.options.get("categories", False))

        revision = self._revision(http, result)
        cache = self._cache_path(cfg)
        if revision and cache and self._unchanged(cache, revision):
            result.warnings.append(
                f"arena: dataset unchanged since the last pull ({revision[:12]}); "
                "nothing fetched. Delete the cache file to force one."
            )
            return result

        seen: set[tuple[str, Modality]] = set()
        empty: list[str] = []
        for config in sorted(configs):
            modality = CONFIGS[config]
            before = len(result.warnings)
            rows = self._rows(http, config, all_categories, result)
            if not rows and len(result.warnings) == before:
                # answered, and answered with nothing. That is not the same as
                # failing, and it is not the same as being fine either.
                empty.append(config)
            for row in rows:
                model_id = model_id_of(row)
                if model_id is None:
                    continue
                if (model_id, modality) not in seen:
                    seen.add((model_id, modality))
                    result.models.append(
                        ModelRef(
                            id=model_id,
                            modality=modality,
                            name=str(row.get("model_name") or model_id),
                            creator=model_id.split("/", 1)[0],
                        )
                    )
                result.observations.extend(
                    self._observations(row, model_id, modality, config, pulled_at)
                )

        if empty:
            result.warnings.append(
                f"arena: {', '.join(empty)} returned no rows -- the leaderboard "
                "may have been renamed or retired; nothing was stored for it"
            )

        if result.observations and revision and cache:
            self._remember(cache, revision)
        return result

    # -- reading ---------------------------------------------------------- #

    def _revision(self, http: HttpClient, result: PullResult) -> str | None:
        """The dataset's commit sha: this source's ETag.

        One cheap request that says whether the other forty are worth making.
        """
        response = http.get(INFO)
        if response.status != 200:
            result.warnings.append(f"arena: HTTP {response.status} from {INFO}")
            result.ok = False
            return None
        body = response.body if isinstance(response.body, dict) else {}
        sha = body.get("sha")
        return str(sha) if sha else None

    def _rows(
        self, http: HttpClient, config: str, all_categories: bool, result: PullResult
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            url, params = self._request(config, all_categories, page * PAGE)
            response = http.get(url, params=params)
            if response.status != 200:
                result.warnings.append(
                    f"arena: HTTP {response.status} on {config}"
                    + (" -- nothing stored" if not out else " -- kept the earlier pages")
                )
                result.ok = False
                return out
            body = response.body if isinstance(response.body, dict) else {}
            rows = body.get("rows")
            if not isinstance(rows, list) or not rows:
                break
            out.extend(
                r["row"] for r in rows if isinstance(r, dict) and isinstance(r.get("row"), dict)
            )
            total = as_int(body.get("num_rows_total"))
            if total is not None and len(out) >= total:
                break
            if len(rows) < PAGE:
                # a short page is the last page. Without this the next request
                # runs off the end and its 404 is reported as a failure -- true
                # of a trimmed recording, and of any endpoint whose count is
                # larger than what it will actually serve.
                break
        return out

    def _request(
        self, config: str, all_categories: bool, offset: int
    ) -> tuple[str, dict[str, str]]:
        """`(url, params)` -- the query stays out of the url on purpose.

        `fixture_slug` drops a query string, so every config folded onto one
        recording name and each overwrote the last. Passed as params they are
        part of the name, which is what the `__include_categories_true`
        suffix on the Artificial Analysis recordings is for.
        """
        params = {
            "dataset": DATASET,
            "config": config,
            "split": "latest",
            "offset": str(offset),
            "length": str(PAGE),
        }
        if all_categories:
            return f"{API}/rows", params
        params["where"] = "\"category\"='overall'"
        return f"{API}/filter", params

    def _observations(
        self,
        row: dict[str, Any],
        model_id: str,
        modality: Modality,
        config: str,
        pulled_at: Any,
    ) -> list[Observation]:
        """The Elo and its rank, dated by the leaderboard rather than by us."""
        from datetime import UTC, datetime

        published = pulled_at
        raw_date = str(row.get("leaderboard_publish_date") or "")
        if raw_date:
            try:
                published = datetime.fromisoformat(raw_date).replace(tzinfo=UTC)
            except ValueError:
                published = pulled_at

        category = row.get("category")
        field = field_for(config, str(category) if category else None)
        rating = as_float(row.get("rating"))
        if rating is None:
            return []

        votes = as_int(row.get("vote_count"))
        out = [
            Observation(
                model_id=model_id,
                modality=modality,
                source=self.name,
                field=field,
                value=rating,
                unit="elo",
                n=votes,
                ci95=ci95_of(row),
                observed_at=published,
                pulled_at=pulled_at,
            )
        ]
        rank = as_float(row.get("rank"))
        if rank is not None:
            out.append(
                Observation(
                    model_id=model_id,
                    modality=modality,
                    source=self.name,
                    field=f"rank:{slugify(config)}"
                    if not category or category == "overall"
                    else f"rank:{slugify(config)}:{slugify(str(category))}",
                    value=rank,
                    unit="count",
                    observed_at=published,
                    pulled_at=pulled_at,
                )
            )
        return out

    # -- the cache -------------------------------------------------------- #

    def _cache_path(self, cfg: SourceConfig) -> Path | None:
        raw = cfg.options.get("cache") or cfg.dir
        if not raw:
            return None
        return Path(str(raw)) / "arena-revision.json"

    def _unchanged(self, cache: Path, revision: str) -> bool:
        if not cache.is_file():
            return False
        try:
            held = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return bool(held.get("sha") == revision)

    def _remember(self, cache: Path, revision: str) -> None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(
                json.dumps({"sha": revision, "at": utcnow().isoformat()}, indent=1),
                encoding="utf-8",
            )
        except OSError:
            # a cache that cannot be written is a slower pull, not a failure
            pass
