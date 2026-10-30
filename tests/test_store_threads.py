"""A connection opened by a thread that has exited must not stay open."""

from __future__ import annotations

import threading
from pathlib import Path

from sieve.store.db import Store


def test_exited_threads_do_not_leak_connections(tmp_path: Path) -> None:
    store = Store(tmp_path / "sieve.db")

    def touch() -> None:
        store.db.execute("SELECT 1").fetchone()

    for _ in range(50):
        worker = threading.Thread(target=touch)
        worker.start()
        worker.join()

    touch()
    assert len(store._all) <= 2
    store.close()


def test_live_threads_keep_their_connection(tmp_path: Path) -> None:
    store = Store(tmp_path / "sieve.db")
    release = threading.Event()
    opened = threading.Event()
    held: list[object] = []

    def hold() -> None:
        held.append(store.db)
        opened.set()
        release.wait()
        held.append(store.db)

    worker = threading.Thread(target=hold)
    worker.start()
    opened.wait()
    store.db.execute("SELECT 1").fetchone()
    release.set()
    worker.join()
    assert held[0] is held[1]
    store.close()
