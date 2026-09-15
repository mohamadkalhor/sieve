"""Catalog: canonical ids, aliases and matching a gateway's id to one of ours.

The bar in the brief: on 30 hand-listed gateway ids, 27 or more correct and
**zero wrong**. A wrong match silently routes traffic to a different model, so
it is much worse than an unmatched id, which a person sees and fixes.
"""

from __future__ import annotations

from pathlib import Path

from sieve.catalog import Matcher, canonical_id, load_aliases, normalise, save_aliases
from sieve.catalog.match import MIN_CONFIDENCE
from sieve.catalog.registry import Registry, model_ref
from sieve.contracts import Reachable
from sieve.store.db import now

CANONICAL = [
    "anthropic/claude-opus-5",
    "anthropic/claude-sonnet-5",
    "anthropic/claude-haiku-4-5",
    "openai/gpt-5-2",
    "openai/gpt-5-2-mini",
    "google/gemini-3-pro",
    "google/gemini-3-flash",
    "z-ai/glm-5.3",
    "deepseek/deepseek-v4",
    "meta-llama/llama-4-70b-instruct",
    "mistralai/mistral-large-3",
    "qwen/qwen3-72b",
    "x-ai/grok-5",
    "amazon/nova-2-pro",
    "cohere/command-a",
]

ALIAS_FILE = {"glm-5-3-turbo": "z-ai/glm-5.3", "sonnet": "anthropic/claude-sonnet-5"}

#: (gateway id, the canonical id it means, or None for "leave it alone")
GATEWAY_IDS: list[tuple[str, str | None]] = [
    # exact
    ("anthropic/claude-opus-5", "anthropic/claude-opus-5"),
    ("openai/gpt-5-2", "openai/gpt-5-2"),
    # vendor prefix a gateway invented
    ("oc-go/glm-5.3", "z-ai/glm-5.3"),
    ("proxy/anthropic/claude-opus-5", "anthropic/claude-opus-5"),
    ("gw/gemini-3-pro", "google/gemini-3-pro"),
    # no prefix at all
    ("claude-opus-5", "anthropic/claude-opus-5"),
    ("gpt-5-2-mini", "openai/gpt-5-2-mini"),
    ("grok-5", "x-ai/grok-5"),
    ("command-a", "cohere/command-a"),
    ("nova-2-pro", "amazon/nova-2-pro"),
    # dot versus dash in the version
    ("glm-5-3", "z-ai/glm-5.3"),
    ("z-ai/glm-5-3", "z-ai/glm-5.3"),
    ("claude-haiku-4.5", "anthropic/claude-haiku-4-5"),
    # case and punctuation
    ("GPT-5-2", "openai/gpt-5-2"),
    ("Claude_Sonnet_5", "anthropic/claude-sonnet-5"),
    ("QWEN3-72B", "qwen/qwen3-72b"),
    # provider decorations
    ("openai/gpt-5-2:free", "openai/gpt-5-2"),
    ("z-ai/glm-5.3:nitro", "z-ai/glm-5.3"),
    ("claude-opus-5-20260420", "anthropic/claude-opus-5"),
    ("gemini-3-flash-preview", "google/gemini-3-flash"),
    ("deepseek-v4-latest", "deepseek/deepseek-v4"),
    ("mistral-large-3-20260115", "mistralai/mistral-large-3"),
    # the alias file
    ("glm-5-3-turbo", "z-ai/glm-5.3"),
    ("sonnet", "anthropic/claude-sonnet-5"),
    # honestly unmatchable: a person aliases these
    ("some-gateway/internal-model-7", None),
    ("my-finetune-of-something", None),
    ("gpt-4o", None),
    ("llama-3-8b", None),
    ("embedding-3-large", None),
    ("whisper-2", None),
]


def _matcher() -> Matcher:
    return Matcher(CANONICAL, ALIAS_FILE)


def test_thirty_gateway_ids_match_with_nothing_wrong() -> None:
    matcher = _matcher()
    assert len(GATEWAY_IDS) == 30

    correct = 0
    wrong: list[tuple[str, str | None, str | None]] = []
    for local_id, expected in GATEWAY_IDS:
        found = matcher.match(local_id)
        got = found.model_id if found.confidence >= MIN_CONFIDENCE else None
        if got == expected:
            correct += 1
        else:
            wrong.append((local_id, expected, got))

    assert not wrong, f"wrong matches: {wrong}"
    assert correct >= 27, f"only {correct}/30 matched"


def test_a_wrong_match_is_never_preferred_to_no_match() -> None:
    """Two canonical ids on one key is ambiguity, and ambiguity is not a match."""
    matcher = Matcher(["a/thing-2", "b/thing-2"])
    assert matcher.match("thing-2").model_id is None


def test_confidence_falls_as_the_rule_gets_looser() -> None:
    matcher = _matcher()
    assert matcher.match("anthropic/claude-opus-5").confidence == 1.0
    assert matcher.match("sonnet").confidence == 0.95
    assert matcher.match("claude-opus-5").confidence == 0.85
    assert matcher.match("claude-opus-5-20260420").confidence == 0.80
    assert matcher.match("nothing-like-this").confidence == 0.0


def test_normalise_folds_case_punctuation_and_version_style() -> None:
    assert normalise("Claude-Opus-4.6") == normalise("claude-opus-4-6") == "claudeopus46"
    assert normalise("GPT_5.2") == "gpt52"


def test_canonical_id_is_creator_slash_slug() -> None:
    assert canonical_id("Anthropic", "Claude Opus 5") == "anthropic/claude-opus-5"
    assert canonical_id("z-ai", "z-ai/glm-5-3") == "z-ai/glm-5-3"


def test_a_dot_and_a_dash_separate_a_version_the_same_way() -> None:
    """One rule, or the catalogue holds the same model twice.

    `canonical_id` folded whitespace and underscores while `normalise` also
    folded the dot, so `Wan 3.0` from a published name and `wan-3-0` from a
    published slug became two canonical ids that could never meet -- which is
    exactly how a fal price failed to reach an AA score.
    """
    assert canonical_id("Alibaba", "Wan 3.0") == canonical_id("Alibaba", "wan-3-0")
    assert canonical_id("Alibaba", "Wan 3.0") == "alibaba/wan-3-0"
    assert canonical_id("", "glm-5.3") == "glm-5-3"
    assert canonical_id("OpenAI", "GPT-5.6 Sol") == "openai/gpt-5-6-sol"

    # and the id a canonical id produces is stable under the matcher's own key
    from sieve.catalog.match import normalise

    assert normalise(canonical_id("Alibaba", "Wan 3.0")) == normalise("alibaba/wan-3.0")


def test_registry_merges_aliases_and_leaves_unmatched_alone() -> None:
    registry = Registry(ALIAS_FILE)
    registry.add(model_ref(creator="anthropic", slug="claude-opus-5", modality="llm"))
    registry.add(
        model_ref(
            creator="anthropic",
            slug="claude-opus-5",
            modality="llm",
            aliases=["claude-opus-5-20260420"],
        )
    )
    held = registry.models[("anthropic/claude-opus-5", "llm")]
    assert held.aliases == ["claude-opus-5-20260420"]

    reachable = [
        Reachable(inventory="gw", local_id="claude-opus-5", seen_at=now()),
        Reachable(inventory="gw", local_id="not-a-model", seen_at=now()),
    ]
    matched, unmatched = registry.attach(reachable, "llm")
    assert [r.model_id for r in matched] == ["anthropic/claude-opus-5"]
    assert [r.local_id for r in unmatched] == ["not-a-model"]
    assert unmatched[0].model_id is None, "an unmatched id is kept, never dropped"


def test_alias_file_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "aliases.yaml"
    save_aliases(path, ALIAS_FILE)
    assert load_aliases(path) == ALIAS_FILE
    assert load_aliases(tmp_path / "absent.yaml") == {}
    assert "z-ai/glm-5.3:" in path.read_text(encoding="utf-8")


#: The live catalogue's scored ids for the families on the 2026-09-15 Unscored
#: page, so each pass meets the neighbours it must not confuse.
SCORED = [
    "anthropic/claude-4-5-haiku",
    "anthropic/claude-4-5-haiku-reasoning",
    "anthropic/claude-3-5-haiku",
    "google/gemma-4-12b",
    "google/gemma-4-31b",
    "google/gemma-4-31b-non-reasoning",
    "google/gemma-4-26b-a4b",
    "google/gemini-3-flash",
    "google/gemini-3-flash-reasoning",
    "google/gemini-3-1-pro-preview-(low)",
    "google/gemini-3-1-pro-preview-(high)",
    "google/gemini-3-5-flash",
    "google/gemini-3-5-flash-medium",
    "meta/llama-3-2-instruct-1b",
    "meta/llama-3-2-instruct-3b",
    "meta/llama-3-2-instruct-11b-vision",
    "mistral/mistral-small-3",
    "mistral/mistral-small-3-1",
    "mistral/mistral-small-3-2",
    "nvidia/nvidia-nemotron-3-super-120b-a12b",
    "nvidia/nvidia-nemotron-3-nano-30b-a3b",
    "nvidia/nvidia-nemotron-3-nano-30b-a3b-reasoning",
    "nvidia/nemotron-3-nano-omni-30b-a3b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-120b-low",
    "thinking-machines/inkling",
    "thinking-machines/inkling-small",
    "meta/muse-spark-1-3",
]

#: (router id, the scored twin, or None when the right answer is "not scored")
TWINS: list[tuple[str, str | None]] = [
    ("cc/claude-haiku-4-5-20251001", "anthropic/claude-4-5-haiku"),
    ("gemini/gemma-4-31b-it", "google/gemma-4-31b"),
    ("openrouter/google/gemma-4-26b-a4b-it:free", "google/gemma-4-26b-a4b"),
    ("gemini/gemini-3-flash-preview", "google/gemini-3-flash"),
    ("ag/gemini-3.1-pro-low", "google/gemini-3-1-pro-preview-(low)"),
    ("cf/@cf/meta/llama-3.2-1b-instruct", "meta/llama-3-2-instruct-1b"),
    ("cf/@cf/meta/llama-3.2-3b-instruct", "meta/llama-3-2-instruct-3b"),
    ("cf/@cf/mistralai/mistral-small-3.1-24b-instruct", "mistral/mistral-small-3-1"),
    (
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nvidia-nemotron-3-super-120b-a12b",
    ),
    (
        "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b",
    ),
    ("openrouter/thinkingmachines/inkling:free", "thinking-machines/inkling"),
    ("openrouter/thinkingmachines/inkling-small:free", "thinking-machines/inkling-small"),
    # an effort level nobody measured is not the default one
    ("ag/gemini-3.5-flash-low", None),
    ("ag/gpt-oss-120b-medium", None),
    # a size nobody measured is not a neighbouring size
    ("gemini/gemma-4-4b-it", None),
    ("cf/@cf/meta/llama-3.2-90b-instruct", None),
    # a derived build is not its base
    ("oc-go/muse-spark-1.3-contributor", None),
    ("oc-go/omen-alpha", None),
]


def test_a_router_id_finds_its_scored_twin_and_nothing_else() -> None:
    from sieve.catalog.match import ScoredTwins

    twins = ScoredTwins(SCORED)
    wrong = [
        (local_id, expected, twins.find(local_id).model_id)
        for local_id, expected in TWINS
        if twins.find(local_id).model_id != expected
    ]
    assert not wrong, wrong


def test_a_version_next_to_a_size_stays_a_version() -> None:
    """`3.2-1b` is version 3.2 at 1b, never a 21b; `1.1b` is one size."""
    from sieve.catalog.match import family_key

    assert family_key("llama-3.2-1b-instruct") == family_key("llama-3-2-instruct-1b")
    assert family_key("mistral-small-3.1-24b") != family_key("mistral-small-3-124b")
    assert family_key("parakeet-ctc-1.1b") == "ctc parakeet||1p1b"


def test_attach_moves_an_unscored_match_to_its_scored_twin_unless_aliased() -> None:
    registry = Registry(
        {"cc/claude-haiku-4-5-20251001": "anthropic/claude-haiku-4.5"},
        scored=["anthropic/claude-4-5-haiku", "google/gemma-4-31b"],
        pinned=["cc/claude-haiku-4-5-20251001"],
    )
    registry.add(model_ref(creator="anthropic", slug="claude-haiku-4.5", modality="llm"))
    registry.add(model_ref(creator="google", slug="gemma-4-31b-it", modality="llm"))
    reachable = [
        Reachable(inventory="gw", local_id="gemini/gemma-4-31b-it", seen_at=now()),
        Reachable(inventory="gw", local_id="cc/claude-haiku-4-5-20251001", seen_at=now()),
    ]
    matched, unmatched = registry.attach(reachable, "llm")
    assert not unmatched
    by_local = {r.local_id: r.model_id for r in matched}
    # matched OpenRouter's unscored record first, then moved to AA's scored one
    assert by_local["gemini/gemma-4-31b-it"] == "google/gemma-4-31b"
    # a person aliased this one: it stays where they put it
    assert by_local["cc/claude-haiku-4-5-20251001"] == "anthropic/claude-haiku-4.5"
