"""The store's writer lock -- card O9b, step 1.

Three writers reach one SQLite file and none of them coordinated:
`sieve-pull.service` (`sieve pull`, `sieve plan --store`), `sieve-run.service`
(`sieve run`), and the API's own bulk writers (`POST /v1/sources/{name}/pull`,
`POST /v1/apply`). The timer firing while somebody pressed "pull" is not a fault
in either caller: it is two writers and one file, and the interleaving that
follows -- a row folded into two sources, a chain lost to `database is locked`.

These tests pin what the lock promises: a second writer waits and then proceeds;
a second writer that cannot wait is refused with a sentence naming the file and,
when it left one, the holder's pid; one process may take it twice, because
`sieve run` reaches the pull path inside its own hold; and the API answers a
refused writer 503 `store_busy` with `Retry-After` rather than a 500 or a hang.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sieve import cli
from sieve.api.app import create_app
from sieve.config import Config, Paths, StoreConfig
from sieve.contracts import SourceConfig
from sieve.storelock import (
    DEFAULT_WAIT,
    LOCK_NAME,
    StoreBusy,
    lock_path,
    store_lock,
    wait_seconds,
)

REPO = Path(__file__).resolve().parents[1]

#: The bearer `SIEVE_TOKENS` holds below: `name:scopes:secret`.
AUTH = {"Authorization": "Bearer sekrit"}

#: A second process: take the lock, say so, hold it for a while. `flock` is what
#: decides, so this is a real contender and not a stand-in for one.
HOLDER = """
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
os.ftruncate(fd, 0)
os.write(fd, str(os.getpid()).encode("ascii"))
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""


class Holder:
    """Another writer, holding the store lock for `seconds`."""

    def __init__(self, path: Path, seconds: float = 10.0) -> None:
        self.path = Path(path)
        self.seconds = seconds
        self.pid = 0
        self._child: subprocess.Popen[str] | None = None

    def __enter__(self) -> "Holder":
        self._child = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(self.path), str(self.seconds)],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self._child.stdout is not None
        # "held" is printed after the flock succeeds: the wait below is a wait
        # for a lock somebody really has, not for a hopeful sleep.
        said = self._child.stdout.readline().strip()
        assert said == "held", said
        self.pid = int(self.path.read_text().split()[0])
        return self

    def __exit__(self, *exc: Any) -> bool:
        if self._child is not None:
            self._child.wait(timeout=30)
        return False


@pytest.fixture
def box(tmp_path: Path) -> Config:
    """A box of its own, with the profiles the API seeds from."""
    profiles = tmp_path / "profiles"
    shutil.copytree(REPO / "profiles", profiles)
    return Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(axes=str(REPO / "data" / "axes"), profiles=str(profiles)),
    )


# --------------------------------------------------------------------------- #
# the lock itself
# --------------------------------------------------------------------------- #


def test_a_second_writer_waits_and_then_proceeds(tmp_path: Path) -> None:
    """The point of the lock: the queue drains, the second pull still runs."""
    store = tmp_path / "sieve.db"
    with Holder(lock_path(store), seconds=1.0):
        started = time.monotonic()
        with store_lock(store, wait=10.0) as lock:
            waited = time.monotonic() - started
            assert lock.held is True
            assert Path(lock.path).name == LOCK_NAME
            assert waited >= 0.3, f"took a held lock without waiting ({waited:.2f}s)"
    # and it is really given back: the next writer has it at once
    with store_lock(store, wait=0):
        pass


def test_a_writer_that_cannot_wait_is_refused_and_told_who_holds_it(tmp_path: Path) -> None:
    store = tmp_path / "sieve.db"
    holder = Holder(lock_path(store), seconds=10.0)
    with holder:
        with pytest.raises(StoreBusy) as caught:
            with store_lock(store, wait=0):
                raise AssertionError("took a lock another process holds")
        said = str(caught.value)
        assert LOCK_NAME in said
        assert str(holder.pid) in said, said
        assert "no wait" in said, said


def test_the_env_var_is_how_long_a_writer_waits(tmp_path: Path, monkeypatch: Any) -> None:
    """`SIEVE_STORE_LOCK_WAIT` is the knob, and a typo in it is not a failure."""
    store = tmp_path / "sieve.db"
    monkeypatch.setenv("SIEVE_STORE_LOCK_WAIT", "0")
    with Holder(lock_path(store)):
        with pytest.raises(StoreBusy):
            with store_lock(store):
                raise AssertionError("waited for a lock it was told not to wait for")
    monkeypatch.setenv("SIEVE_STORE_LOCK_WAIT", "later")
    assert wait_seconds() == DEFAULT_WAIT
    monkeypatch.setenv("SIEVE_STORE_LOCK_WAIT", "-5")
    assert wait_seconds() == 0.0
    monkeypatch.delenv("SIEVE_STORE_LOCK_WAIT")
    assert wait_seconds() == DEFAULT_WAIT


def test_one_process_may_take_it_twice(tmp_path: Path) -> None:
    """`sieve run` pulls inside its own hold; that must nest, not deadlock."""
    store = tmp_path / "sieve.db"
    with store_lock(store, wait=0):
        with store_lock(store, wait=0) as inner:
            assert inner.held is True
    # the inner release did not let go, the outer one did: another process gets it
    with Holder(lock_path(store), seconds=0.2):
        pass


# --------------------------------------------------------------------------- #
# the CLI: a writer that could not have the store exits non-zero
# --------------------------------------------------------------------------- #


def test_the_cli_pull_exits_non_zero_when_another_writer_holds_the_store(
    box: Config, monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.setattr(cli, "_config", lambda args: box)
    monkeypatch.setenv("SIEVE_STORE_LOCK_WAIT", "0")
    holder = Holder(lock_path(box.db_path))
    with holder:
        code = cli.main(["pull"])
        said = capsys.readouterr().out
        assert code == cli.EXIT_ERROR
        assert "pull:" in said and LOCK_NAME in said, said
        assert str(holder.pid) in said, said


def test_the_cli_pull_runs_when_the_store_is_free(
    box: Config, monkeypatch: Any, capsys: Any
) -> None:
    """The lock must not be a thing every pull fails against."""
    monkeypatch.setattr(cli, "_config", lambda args: box)
    monkeypatch.setenv("SIEVE_STORE_LOCK_WAIT", "0")
    assert cli.main(["pull"]) == cli.EXIT_OK
    assert "pull:" not in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the API: a refused writer is a 503, not a 500 and not a hang
# --------------------------------------------------------------------------- #


@pytest.fixture
def api(box: Config, monkeypatch: Any) -> Iterator[tuple[TestClient, Config]]:
    """The box as the service runs it: one source, one bearer token, no kit."""
    monkeypatch.delenv("AGENT_V1", raising=False)
    monkeypatch.setenv("SIEVE_TOKENS", "bot:apply,read:sekrit")
    box.sources["ghost"] = SourceConfig(name="ghost", enabled=True)
    with TestClient(create_app(box)) as client:
        yield client, box


def test_the_pull_route_answers_store_busy_with_retry_after(
    api: tuple[TestClient, Config], monkeypatch: Any
) -> None:
    client, box = api
    monkeypatch.setenv("SIEVE_STORE_LOCK_WAIT", "0")
    holder = Holder(lock_path(box.db_path))
    with holder:
        answer = client.post("/v1/sources/ghost/pull", headers=AUTH)
        assert answer.status_code == 503, answer.text
        assert answer.json()["error"]["code"] == "store_busy"
        assert answer.headers["retry-after"] == "2"
        # F15: the lock path and holder pid go to the log, not to the caller
        assert str(holder.pid) not in answer.json()["error"]["message"]


def test_the_pull_route_reaches_the_source_when_the_store_is_free(
    api: tuple[TestClient, Config],
) -> None:
    """With the lock free the same call gets past it: 501 for a source with no
    plugin, which is the route's own next step -- and `ghost` is not one."""
    client, box = api
    answer = client.post("/v1/sources/ghost/pull", headers=AUTH)
    assert answer.status_code == 501, answer.text
    assert answer.json()["error"]["code"] == "not_built"
    # and the refusal left the lock held by nobody
    with store_lock(box, wait=0):
        pass
