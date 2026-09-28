"""§3.9 -- what an app offers over MCP, and the route behind each tool.

A tool is not an endpoint and not a second implementation: it is a *name* for a
route the app already has, plus the two pydantic models that route already
validates against. :mod:`agentkit.mcp` builds its schemas from those models and
dispatches back into the route, so a tool call runs the same §3.1 credential
rule, the same §3.2 scope, the same §3.7 limits, the same §3.4 idempotency store
and the same handler a REST caller runs. There is nothing to keep in step.

There is no authority here either: registering a tool grants nothing. A
registry that names a route a key may not use is a tool that answers 401 or 403,
never one that succeeds anyway, because the route is what decides.

:func:`agentkit.mcp.check_parity` is the other half of that promise -- it reads
the app's OpenAPI and fails when a tool and its route have drifted apart. What
it needs from the route is the scope that route demands; :func:`agentkit.auth.require`
marks each dependency it builds with ``__aio_scope__`` for exactly that reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from agentkit import auth, errors

__all__ = ["METHODS", "NAME_RE", "Registry", "Tool"]

#: An MCP tool name: 1-120 characters, first alphanumeric. The convention is
#: ``<app>_<verb>`` (``press_publish``), and the app prefix is why a name may
#: not start with an underscore: that namespace belongs to the MCP layer
#: (``_idempotency_key``, §3.9).
NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z")

#: The methods a tool may name. A route FastAPI serves with any other method is
#: not addressable here.
METHODS = frozenset({"DELETE", "GET", "HEAD", "PATCH", "POST", "PUT"})


@dataclass(frozen=True)
class Tool:
    """One route, named for MCP. Built once, when the app is wired.

    ``method`` and ``path`` must name a route of the same app -- ``path`` in the
    app's own spelling, ``{placeholders}`` included, and nothing outside
    ``/v1``, because ``/v1`` is the surface this contract owns. ``input_model``
    is the route's request body (for a ``GET``, a model of its parameters) and
    ``output_model`` the body it answers with; both are validated here as
    pydantic models, since they are what the MCP schemas are built from.

    ``scope`` is the §3.2 scope the *route* demands -- not a permission this
    object hands out -- and ``run`` marks a tool whose route is a §3.7 ``run``
    (10 a minute, not 120), which also makes ``_idempotency_key`` required in
    the schema MCP publishes.
    """

    name: str
    method: str
    path: str
    scope: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    run: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not NAME_RE.match(self.name):
            raise ValueError(
                f"a tool name looks like {NAME_RE.pattern!r} (as in "
                f"'press_publish'): got {self.name!r}"
            )
        if not isinstance(self.method, str) or self.method.upper() not in METHODS:
            raise ValueError(
                f"{self.name}: method must be one of {sorted(METHODS)}: "
                f"got {self.method!r}"
            )
        object.__setattr__(self, "method", self.method.upper())
        path = self.path
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(f"{self.name}: path must start with '/': got {path!r}")
        if path != errors.PREFIX and not path.startswith(errors.PREFIX + "/"):
            raise ValueError(
                f"{self.name}: a tool names a route of the machine surface, "
                f"under {errors.PREFIX!r}: got {path!r}"
            )
        # The contract's own validator, so a scope this kit would refuse in a
        # key is refused here too, in the same words.
        scopes = auth.clean_scopes([self.scope])
        if not scopes:
            raise ValueError(
                f"{self.name}: scope is the scope the route demands: got "
                f"{self.scope!r}"
            )
        object.__setattr__(self, "scope", next(iter(scopes)))
        for label, model in (
            ("input_model", self.input_model),
            ("output_model", self.output_model),
        ):
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                raise ValueError(
                    f"{self.name}: {label} must be a pydantic model, so MCP has "
                    f"a schema to publish: got {model!r}"
                )
        if self.run is not True and self.run is not False:
            raise ValueError(f"{self.name}: run must be True or False")

    def __repr__(self) -> str:
        return (
            f"Tool(name={self.name!r}, {self.method} {self.path}, "
            f"scope={self.scope!r}, run={self.run!r})"
        )


class Registry:
    """The tools one app offers, in registration order.

    ``add`` is the wiring step, and it refuses what would only fail later: a
    duplicate name, a method that is not an HTTP method, a path outside
    ``/v1``, a scope the contract does not know, a model that is not a pydantic
    model. An app builds this next to its routes (``agentkit/demo`` does), and
    hands it to :func:`agentkit.mcp.mount`.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> Tool:
        """Register one tool and return it, so a list can be built in one go."""
        if not isinstance(tool, Tool):
            raise TypeError(f"add() takes a Tool: got {tool!r}")
        if tool.name in self._tools:
            raise ValueError(f"two tools are named {tool.name!r}")
        self._tools[tool.name] = tool
        return tool

    def tools(self) -> tuple[Tool, ...]:
        """Every tool, in the order it was added (that is the listing order)."""
        return tuple(self._tools.values())

    def get(self, name: Any) -> Tool | None:
        """The tool of that name, or ``None``. A name that is not a string is
        not a tool -- never a lookup by anything else."""
        return self._tools.get(name) if isinstance(name, str) else None

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Any:
        return iter(self._tools.values())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def __repr__(self) -> str:
        return f"Registry({[tool.name for tool in self._tools.values()]!r})"
