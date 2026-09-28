"""agentkit -- the shared kit behind the agent-io contract (version 1).

The contract itself is ``AGENT-IO.md`` at the root of this repository: the code
here follows it, never the other way round. Every app mounts the kit's pieces on
its ``/v1`` router only, next to its own ``/api`` side.
"""

from __future__ import annotations

VERSION = "0.1.0"

# The contract revision this kit implements (§3.8: an ``x-aio-contract``
# extension of the same number goes into every app's generated OpenAPI).
CONTRACT_VERSION = 1

__all__ = ["VERSION", "CONTRACT_VERSION"]
