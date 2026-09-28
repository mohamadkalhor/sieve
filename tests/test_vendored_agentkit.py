"""The vendored ``agentkit`` is the copy this app was tested against.

Written by ``agentkit/tools/vendor.py``. ``_vendor/agentkit/HASH`` is the sha256
of the tree as it was copied; if this test fails, something in the copy changed
and whoever changed it has to vendor again (or say why in review).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

VENDORED = Path(__file__).resolve().parent.parent / "_vendor" / "agentkit"
#: The one file a hash of the tree cannot describe, matched where it lives
#: rather than by name: a copy that happens to contain its own ``HASH`` file is
#: a change like any other, and ``VERSION`` is inside the hash too.
RECEIPT = "HASH"


def _tree_hash() -> str:
    """sha256 over the copy: every file's path and bytes, and nothing else."""
    files = [
        path
        for path in VENDORED.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.relative_to(VENDORED).as_posix() != RECEIPT
    ]
    assert files, f"nothing vendored under {VENDORED}"
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda one: one.relative_to(VENDORED).as_posix()):
        digest.update(path.relative_to(VENDORED).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_the_vendored_agentkit_matches_its_hash() -> None:
    recorded = (VENDORED / RECEIPT).read_text(encoding="utf-8").strip()
    assert _tree_hash() == recorded, (
        "the vendored agentkit does not match the HASH beside it -- the copy was "
        "edited, or vendored from a different tree: run tools/vendor.py again "
        "from the kit checkout"
    )
