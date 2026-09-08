"""Phase 2 part 4 — the three targets that write somewhere real.

**Nothing here touches a gateway.** The webhook is a fake HTTP server on
localhost, 9router is a scratch SQLite file built in `tmp_path` with its real
schema, and LiteLLM is a YAML file. That is the rule the brief sets and it is
also the only way these tests could be trusted: a target that needs a live
router to prove it works has proved nothing anyone else can re-run.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

from sieve.contracts import Chain, TargetConfig
from sieve.targets.litellm import LiteLLMError, LiteLLMTarget, fallbacks_for
from sieve.targets.ninerouter import (
    MANAGED_PREFIX,
    NineRouterError,
    NineRouterTarget,
    chain_models,
)
from sieve.targets.webhook import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    WebhookError,
    WebhookTarget,
    sign,
)

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def chain(
    profile: str, primary: str, *fallbacks: str, local: dict[str, list[str]] | None = None
) -> Chain:
    ids = [primary, *fallbacks]
    return Chain(
        profile=profile,
        computed_at=NOW,
        primary=primary,
        fallbacks=list(fallbacks),
        local=local if local is not None else {i: [f"gw/{i.split('/')[-1]}"] for i in ids},
    )


# --------------------------------------------------------------------------- #
# webhook
# --------------------------------------------------------------------------- #


class _Recorder(BaseHTTPRequestHandler):
    """A receiver that records what it was sent and answers however it is told."""

    status = 200
    # deliberately shared across instances: the handler is constructed per
    # request, so the recording has to live on the class
    received: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        type(self).received.append(
            {
                "body": body,
                "signature": self.headers.get(SIGNATURE_HEADER),
                "timestamp": self.headers.get(TIMESTAMP_HEADER),
            }
        )
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def receiver() -> Any:
    _Recorder.received = []
    _Recorder.status = 200
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_the_webhook_signs_exactly_the_bytes_it_sends(
    receiver: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receiver that trusts an unsigned POST routes traffic for anyone."""
    monkeypatch.setenv("HOOK_SECRET", "shh")
    port = receiver.server_address[1]
    cfg = TargetConfig(
        name="hook",
        kind="webhook",
        url=f"http://127.0.0.1:{port}/hook",
        options={"secret_env": "HOOK_SECRET"},
    )

    result = WebhookTarget().write(cfg, [chain("coder", "a/one", "a/two")], dry_run=False)
    assert result.detail["pushed"] is True
    assert result.detail["signed_with"] == "HOOK_SECRET"

    sent = _Recorder.received[0]
    assert sent["signature"] == sign("shh", sent["timestamp"], sent["body"])
    body = json.loads(sent["body"])
    assert body["chains"][0] == {
        "profile": "coder",
        "primary": "a/one",
        "fallbacks": ["a/two"],
        "local": {"a/one": ["gw/one"], "a/two": ["gw/two"]},
        "computed_at": NOW.isoformat(),
    }


def test_the_webhook_refuses_to_push_unsigned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOOK_SECRET", raising=False)
    no_env = TargetConfig(name="hook", kind="webhook", url="http://127.0.0.1:1/x", options={})
    with pytest.raises(WebhookError, match="secret_env"):
        WebhookTarget().write(no_env, [chain("coder", "a/one")], dry_run=False)

    named = TargetConfig(
        name="hook",
        kind="webhook",
        url="http://127.0.0.1:1/x",
        options={"secret_env": "HOOK_SECRET"},
    )
    with pytest.raises(WebhookError, match="not set"):
        WebhookTarget().write(named, [chain("coder", "a/one")], dry_run=False)


def test_a_dry_run_signs_nothing_and_sends_nothing(receiver: Any) -> None:
    port = receiver.server_address[1]
    cfg = TargetConfig(
        name="hook",
        kind="webhook",
        url=f"http://127.0.0.1:{port}/hook",
        options={"secret_env": "HOOK_SECRET"},
    )
    result = WebhookTarget().write(cfg, [chain("coder", "a/one")], dry_run=True)
    assert result.dry_run is True and result.detail["pushed"] is False
    assert _Recorder.received == [], "a dry run must not reach the network"


def test_a_refusal_is_not_retried(receiver: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 4xx means the receiver understood and said no. Sending it again is noise."""
    monkeypatch.setenv("HOOK_SECRET", "shh")
    _Recorder.status = 422
    port = receiver.server_address[1]
    cfg = TargetConfig(
        name="hook",
        kind="webhook",
        url=f"http://127.0.0.1:{port}/hook",
        options={"secret_env": "HOOK_SECRET", "retries": 4, "backoff": 0},
    )
    with pytest.raises(WebhookError, match="not retried"):
        WebhookTarget().write(cfg, [chain("coder", "a/one")], dry_run=False)
    assert len(_Recorder.received) == 1


def test_the_webhook_says_it_cannot_be_diffed() -> None:
    """One-way. `{}` would read as "the target holds nothing", which is a lie."""
    cfg = TargetConfig(name="hook", kind="webhook", url="http://x", options={})
    with pytest.raises(NotImplementedError, match="one-way"):
        WebhookTarget().current(cfg)


# --------------------------------------------------------------------------- #
# 9router
# --------------------------------------------------------------------------- #


def make_9router_db(path: Path, rows: list[tuple[str, list[str]]]) -> Path:
    """A scratch database with 9router's real `combos` schema."""
    db = sqlite3.connect(path)
    with db:
        db.execute(
            "CREATE TABLE combos (id TEXT PRIMARY KEY, name TEXT UNIQUE, kind TEXT,"
            " models TEXT, createdAt TEXT, updatedAt TEXT)"
        )
        for index, (name, models) in enumerate(rows):
            db.execute(
                "INSERT INTO combos VALUES (?,?,?,?,?,?)",
                (f"id{index}", name, "fallback", json.dumps(models), "t0", "t0"),
            )
    db.close()
    return path


def test_9router_refuses_when_it_has_no_way_in() -> None:
    """No token and no path: it will not guess which database to write."""
    cfg = TargetConfig(name="9r", kind="ninerouter", url="http://127.0.0.1:1", options={})
    with pytest.raises(NineRouterError) as caught:
        NineRouterTarget().write(cfg, [chain("coder", "a/one")], dry_run=True)
    message = str(caught.value)
    assert "token_env" in message and "sqlite_path" in message
    assert "Unauthorized" in message, "it should say why a token is needed at all"


def test_9router_reads_the_live_chain_back(tmp_path: Path) -> None:
    """`current()` is the whole point: a diff against what is routing traffic."""
    db = make_9router_db(
        tmp_path / "9router.db",
        [
            (f"{MANAGED_PREFIX}coder", ["gw/one", "gw/two"]),
            ("someone-elses-combo", ["gw/three"]),
        ],
    )
    cfg = TargetConfig(name="9r", kind="ninerouter", options={"sqlite_path": str(db)})

    current = NineRouterTarget().current(cfg)
    assert current == {"coder": ["gw/one", "gw/two"]}
    assert "someone-elses-combo" not in current, "only combos this target manages"


def test_9router_writes_a_chain_and_leaves_everything_else_alone(tmp_path: Path) -> None:
    db = make_9router_db(
        tmp_path / "9router.db",
        [(f"{MANAGED_PREFIX}coder", ["gw/old"]), ("handmade", ["gw/keep"])],
    )
    cfg = TargetConfig(name="9r", kind="ninerouter", options={"sqlite_path": str(db)})
    target = NineRouterTarget()

    result = target.write(
        cfg, [chain("coder", "a/one", "a/two"), chain("reader", "a/three")], dry_run=False
    )
    assert result.dry_run is False

    after = NineRouterTarget().current(cfg)
    assert after["coder"] == ["gw/one", "gw/two"], "the managed combo was updated in place"
    assert after["reader"] == ["gw/three"], "a new profile became a new combo"

    raw = sqlite3.connect(db)
    raw.row_factory = sqlite3.Row
    handmade = raw.execute("SELECT models FROM combos WHERE name='handmade'").fetchone()
    raw.close()
    assert json.loads(handmade["models"]) == ["gw/keep"], "a combo we did not create is untouched"


def test_9router_backs_the_database_up_before_writing(tmp_path: Path) -> None:
    """It belongs to another program. Corrupting it is worse than not writing."""
    db = make_9router_db(tmp_path / "9router.db", [("handmade", ["gw/keep"])])
    cfg = TargetConfig(name="9r", kind="ninerouter", options={"sqlite_path": str(db)})

    result = NineRouterTarget().write(cfg, [chain("coder", "a/one")], dry_run=False)
    backup = Path(result.detail["backup"])
    assert backup.is_file() and backup != db

    restored = sqlite3.connect(backup)
    names = {r[0] for r in restored.execute("SELECT name FROM combos")}
    restored.close()
    assert names == {"handmade"}, "the backup holds the state from before the write"


def test_9router_dry_run_writes_nothing(tmp_path: Path) -> None:
    db = make_9router_db(tmp_path / "9router.db", [])
    cfg = TargetConfig(name="9r", kind="ninerouter", options={"sqlite_path": str(db)})

    result = NineRouterTarget().write(cfg, [chain("coder", "a/one")], dry_run=True)
    assert result.dry_run is True
    assert NineRouterTarget().current(cfg) == {}
    assert not list(tmp_path.glob("*.bak")), "a dry run does not even take a backup"


def test_a_chain_with_nothing_reachable_is_skipped_and_named(tmp_path: Path) -> None:
    """A combo of canonical ids 9router has never heard of would route nowhere."""
    db = make_9router_db(tmp_path / "9router.db", [])
    cfg = TargetConfig(name="9r", kind="ninerouter", options={"sqlite_path": str(db)})

    unreachable = chain("ghost", "a/one", local={})
    result = NineRouterTarget().write(cfg, [unreachable], dry_run=False)
    assert result.detail["skipped"] == {"ghost": "no reachable local id in the chain"}
    assert NineRouterTarget().current(cfg) == {}


def test_chain_models_keeps_fallback_order_and_drops_duplicates() -> None:
    ordered = chain(
        "coder",
        "a/one",
        "a/two",
        "a/three",
        local={"a/one": ["gw/x"], "a/two": ["gw/y", "gw/x"], "a/three": ["gw/z"]},
    )
    assert chain_models(ordered) == ["gw/x", "gw/y", "gw/z"]


# --------------------------------------------------------------------------- #
# litellm
# --------------------------------------------------------------------------- #


def test_litellm_writes_only_its_own_block(tmp_path: Path) -> None:
    """`model_list` and everything else belongs to whoever wrote it."""
    path = tmp_path / "litellm.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "model_list": [{"model_name": "gw/one", "litellm_params": {"model": "x"}}],
                "general_settings": {"master_key": "kept"},
                "router_settings": {"routing_strategy": "simple-shuffle", "fallbacks": []},
            }
        ),
        encoding="utf-8",
    )
    cfg = TargetConfig(name="ll", kind="litellm", options={"path": str(path)})

    LiteLLMTarget().write(cfg, [chain("coder", "a/one", "a/two")], dry_run=False)

    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after["general_settings"] == {"master_key": "kept"}
    assert after["model_list"][0]["model_name"] == "gw/one"
    assert after["router_settings"]["routing_strategy"] == "simple-shuffle"
    assert after["router_settings"]["fallbacks"] == [{"gw/one": ["gw/two"]}]


def test_litellm_reads_its_own_file_back(tmp_path: Path) -> None:
    path = tmp_path / "litellm.yaml"
    path.write_text(
        yaml.safe_dump({"router_settings": {"fallbacks": [{"gw/one": ["gw/two", "gw/three"]}]}}),
        encoding="utf-8",
    )
    cfg = TargetConfig(name="ll", kind="litellm", options={"path": str(path)})
    assert LiteLLMTarget().current(cfg) == {"gw/one": ["gw/two", "gw/three"]}


def test_litellm_skips_a_primary_the_router_has_never_heard_of() -> None:
    """Naming a model LiteLLM does not serve fails every request at run time."""
    assert fallbacks_for([chain("ghost", "a/one", "a/two", local={})]) == []
    assert fallbacks_for([chain("solo", "a/one", local={"a/one": ["gw/one"]})]) == []


def test_litellm_needs_a_path() -> None:
    cfg = TargetConfig(name="ll", kind="litellm", options={})
    with pytest.raises(LiteLLMError, match="path"):
        LiteLLMTarget().write(cfg, [chain("coder", "a/one")], dry_run=True)


def test_litellm_dry_run_leaves_the_file_alone(tmp_path: Path) -> None:
    path = tmp_path / "litellm.yaml"
    path.write_text("model_list: []\n", encoding="utf-8")
    cfg = TargetConfig(name="ll", kind="litellm", options={"path": str(path)})

    result = LiteLLMTarget().write(cfg, [chain("coder", "a/one", "a/two")], dry_run=True)
    assert result.dry_run is True
    assert path.read_text(encoding="utf-8") == "model_list: []\n"


# --------------------------------------------------------------------------- #
# the diff compares like with like
# --------------------------------------------------------------------------- #


def test_a_target_plans_in_the_vocabulary_it_reads_back(tmp_path: Path) -> None:
    """Otherwise every run reports a change and everyone learns to ignore the diff.

    9router holds the gateway's own local ids. The engine computes canonical
    ones. Comparing the two directly would never once say "unchanged".
    """
    db = make_9router_db(tmp_path / "9router.db", [])
    cfg = TargetConfig(name="9r", kind="ninerouter", options={"sqlite_path": str(db)})
    target = NineRouterTarget()
    chains = [chain("coder", "a/one", "a/two")]

    planned = target.plan(cfg, chains)
    assert planned == {"coder": ["gw/one", "gw/two"]}, "local ids, as the combo holds them"

    target.write(cfg, chains, dry_run=False)
    assert target.current(cfg) == planned, "after a write the two sides agree exactly"


def test_the_file_target_plans_in_canonical_ids(tmp_path: Path) -> None:
    """The default: a target that stores the chain stores what the engine computed."""
    from sieve.targets.file import FileTarget

    cfg = TargetConfig(name="out", kind="file", dir=str(tmp_path))
    chains = [chain("coder", "a/one", "a/two")]
    assert FileTarget().plan(cfg, chains) == {"coder": ["a/one", "a/two"]}

    FileTarget().write(cfg, chains, dry_run=False)
    assert FileTarget().current(cfg) == FileTarget().plan(cfg, chains)


def test_litellm_plans_by_primary_because_that_is_how_it_reads_back(tmp_path: Path) -> None:
    """LiteLLM's format has no idea what a profile is, so both sides key on the primary."""
    path = tmp_path / "litellm.yaml"
    cfg = TargetConfig(name="ll", kind="litellm", options={"path": str(path)})
    chains = [chain("coder", "a/one", "a/two")]

    target = LiteLLMTarget()
    assert target.plan(cfg, chains) == {"gw/one": ["gw/two"]}

    target.write(cfg, chains, dry_run=False)
    assert target.current(cfg) == target.plan(cfg, chains)
