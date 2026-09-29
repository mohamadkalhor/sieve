"""§3.9 -- MCP over stateless Streamable HTTP, on ``POST /v1/mcp``.

One endpoint per app, one JSON-RPC 2.0 message per request, no session and
nothing kept between calls. It is mounted with the app's own credential
dependency, so §3.1 runs on it exactly as it runs on every other ``/v1`` route:

| the app calls | what that does |
|---|---|
| ``mcp.mount(app, registry, auth.credential(cfg))`` | ``POST /v1/mcp``, after ``errors.install(app)`` |

A tool is not an implementation. :func:`mount` builds the schemas a client sees
from the tool's pydantic models, and then, for a call, sends the *route's own*
request through ``httpx.ASGITransport`` into the app it is mounted on:

* the same ``Authorization`` header the MCP request carried -- never a cookie,
  and never a key the caller did not send, so a tool cannot do more than the
  key that asked for it;
* ``_idempotency_key``, the one argument the layer owns, removed from the
  arguments and sent as the ``Idempotency-Key`` header (§3.4), which is what
  makes a retried call one run and not two;
* the route's own scope, limits, store and handler, because it *is* that route.

Answering, too, is the route's: a 2xx body is the tool result's
``structuredContent`` verbatim, and a non-2xx answer is handed back with
``isError`` true and §3.3's envelope as ``structuredContent``, so an agent reads
``rate_limited`` or ``not_allowed`` and can tell a 403 from a 429.

Two things this layer decides on its own, because they are not HTTP:

* **Malformed JSON-RPC is a non-2xx** (400 ``bad_json``, 400 ``bad_request``,
  413 ``too_large``, 415 ``bad_content_type``) and therefore §3.3's envelope --
  the contract owns every non-2xx under ``/v1``, and a client of this kit
  already knows how to read one. A *valid* JSON-RPC request that names
  something this server does not have (an unknown method, an unknown tool, a
  protocol version we do not speak) is a 200 whose body is a JSON-RPC error,
  which is what the MCP specification asks for and what a JSON-RPC client
  expects.
* **No key, no tools.** §3.1 lets a session cookie read ``/v1``, but a tool call
  is dispatched *as the bearer of the MCP request*, and a cookie cannot be
  forwarded as one. So a request with no key is refused here, at the door, with
  401 ``sign_in`` -- rather than letting a `tools/call` fail in a way that
  looks like the route's fault.

``MCP-Protocol-Version`` is checked when it is sent (a different version is 400
``bad_request``) and assumed to be ours when it is not, which is the
specification's own backwards-compatibility rule for a server that speaks one
version.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Iterator
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from agentkit import VERSION, auth, errors
from agentkit.idem import HEADER as IDEMPOTENCY_HEADER
from agentkit.registry import METHODS, Registry, Tool

__all__ = ["PROTOCOL_VERSION", "ROUTE_EXTENSION", "check_parity", "mount"]

logger = logging.getLogger("agentkit.mcp")

#: The MCP revision this layer speaks. An ``initialize`` asking for another one
#: is refused with the list of what we do speak, so a client fails loudly
#: instead of guessing.
PROTOCOL_VERSION = "2025-11-25"

#: The header a client puts the version in after ``initialize``.
VERSION_HEADER = "MCP-Protocol-Version"

JSONRPC = "2.0"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

#: The one argument the MCP layer owns: it never reaches a body, it becomes the
#: header §3.4 reads. Required in the published schema of a ``run`` tool.
IDEMPOTENCY_ARG = "_idempotency_key"

#: What ``tools/list`` publishes about the route a tool is a name for: the
#: registry's own ``method`` and ``path`` (§3.9 says an entry names the route it
#: calls). A conformance run sees only the wire, and a tool that has to be
#: checked *as* its route -- schemas, scope, the answer it gives, the run marker
#: -- cannot be matched to a route by shape: two routes may take and return the
#: same models, and then "the tool is a name for a route" would pass while the
#: tool dispatches somewhere else. So the route travels with the tool.
ROUTE_EXTENSION = "x-aio-route"

#: The largest JSON-RPC request this endpoint reads, and the largest answer it
#: will carry back. Beyond them the answer is 413 ``too_large``: a body big
#: enough to be a denial of service is refused rather than parsed.
MAX_REQUEST = 256 * 1024
MAX_RESULT = 2 * 1024 * 1024

#: Methods whose arguments do not go in a body.
READ_METHODS = frozenset({"GET", "HEAD"})

#: The address the in-process dispatch is sent to. The transport is
#: ``ASGITransport``, so nothing is ever resolved or connected: the host is a
#: placeholder that must look like one.
BASE_URL = "http://aio.invalid"

#: How long a tool call may take before the layer gives up on the route. A
#: route that queues work answers at once; one that blocks is answering 503 to
#: the agent, which is better than an agent that waits forever.
TIMEOUT = 30.0

_IDEMPOTENCY_SCHEMA: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z0-9_.:-]{1,128}$",
    "description": (
        "Your key for this call, 1-128 characters of A-Z a-z 0-9 _ . : - . It "
        "is sent as the Idempotency-Key header: reuse it only to retry this "
        "same call, and the first answer comes back instead of a second run."
    ),
}

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_NOT_AN_ID = "A JSON-RPC id is a string or a number."
# A path argument that would resolve to another route: see :func:`_elsewhere`.
_ELSEWHERE = (
    'Is a path argument naming one thing: "." and ".." resolve to another '
    'route, and "/" is not part of a name.'
)

INSTRUCTIONS = (
    "Every tool is one of this app's own /v1 routes: a call runs the same "
    "credential rule, scope, rate limits, idempotency and handler a REST "
    "caller runs, as the API key you send. Send _idempotency_key with every "
    "write and run tool and reuse it only to retry that same call. A tool's "
    "structuredContent is the route's own JSON: a 2xx body verbatim, or the "
    "contract's error envelope with isError true (rate_limited, not_allowed, "
    "idempotency_key_required, ...)."
)


class _Refused(Exception):
    """A JSON-RPC error answer: HTTP 200, an ``error`` object in the body."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.data = data


# -- mounting ----------------------------------------------------------------


def mount(
    app: Any,
    registry: Registry,
    credential_dep: Callable[..., auth.Principal],
) -> APIRouter:
    """Put ``POST /v1/mcp`` on ``app`` and return the router that carries it.

    ``credential_dep`` is the app's own credential dependency
    (``auth.credential(cfg)``, or ``cfg.dependency``), so §3.1's table answers
    for this endpoint too: a mixed call is 400, a junk key is 401, and nothing
    an app listed as public becomes a way in. Call it after
    ``errors.install(app)`` -- a refusal raised by a dependency is answered by
    those handlers.
    """
    if not isinstance(registry, Registry):
        raise TypeError(f"mount() takes a Registry: got {registry!r}")
    if errors.APIError not in getattr(app, "exception_handlers", {}):
        raise ValueError(
            "mcp.mount() must come after errors.install(app): an MCP request "
            "with no key is refused by the credential dependency, and that "
            "refusal is answered by errors.install's handlers."
        )
    server = _Server(app, registry)
    v1 = APIRouter(prefix=errors.PREFIX)

    @v1.post(
        "/mcp",
        summary="MCP over stateless Streamable HTTP",
        response_class=JSONResponse,
    )
    async def mcp(
        request: Request,
        principal: auth.Principal = Depends(credential_dep),
    ) -> Response:
        """initialize, tools/list, tools/call -- one JSON-RPC 2.0 message per request."""
        return await server.post(request, principal)

    app.include_router(v1)
    return v1


# -- the transport -----------------------------------------------------------


class _Server:
    """One app's MCP surface: the checks HTTP owes, then the route's work."""

    def __init__(self, app: Any, registry: Registry) -> None:
        self.app = app
        self.registry = registry
        self._index: dict[tuple[str, str], tuple[str, dict[str, Any]]] | None = None

    # -- the endpoint -------------------------------------------------------

    async def post(self, request: Request, principal: auth.Principal) -> Response:
        asked = request.headers.get(VERSION_HEADER)
        if asked is not None and asked != PROTOCOL_VERSION:
            return self._bad_request(
                request,
                f"This endpoint speaks MCP {PROTOCOL_VERSION}.",
                detail={"supported": [PROTOCOL_VERSION], "requested": asked},
            )
        declared = _media_type(request.headers.get("content-type"))
        if declared is not None and declared != "application/json":
            return errors.envelope(
                415,
                "bad_content_type",
                errors.MESSAGES["bad_content_type"],
                request_id=errors.request_id(request),
            )
        if principal.kind != "key":
            return errors.envelope(
                401,
                "sign_in",
                "MCP is called with an API key: every tool runs as the bearer "
                "of the request that asked for it, and a session cookie cannot "
                "be sent on.",
                request_id=errors.request_id(request),
            )
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > MAX_REQUEST:
                return errors.envelope(
                    413,
                    "too_large",
                    errors.MESSAGES["too_large"],
                    request_id=errors.request_id(request),
                )
        try:
            message = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return errors.envelope(
                400,
                "bad_json",
                errors.MESSAGES["bad_json"],
                request_id=errors.request_id(request),
            )
        if isinstance(message, list):
            return self._bad_request(
                request, "One JSON-RPC message per request: batches are not supported."
            )
        if not isinstance(message, dict) or message.get("jsonrpc") != JSONRPC:
            return self._bad_request(request, "Not a JSON-RPC 2.0 request.")
        rid = message.get("id")
        if "id" in message and not _is_id(rid):
            return self._bad_request(request, _NOT_AN_ID)
        if "method" not in message:
            # An answer to something this server never asked: nothing to say.
            return Response(status_code=202)
        method = message.get("method")
        if not isinstance(method, str):
            return self._bad_request(request, "A JSON-RPC method is a string.")
        params = message.get("params", {})
        if not isinstance(params, dict):
            if "id" not in message:
                return Response(status_code=202)
            return self._error(rid, INVALID_PARAMS, "params must be an object.")
        if "id" not in message:
            # A notification (notifications/initialized is one): acknowledge it
            # and say nothing, which is what the protocol asks for.
            return Response(status_code=202)
        try:
            result = await self._dispatch(request, method, params)
        except _Refused as refused:
            return self._error(rid, refused.code, refused.message, refused.data)
        return JSONResponse({"jsonrpc": JSONRPC, "id": rid, "result": result})

    async def _dispatch(
        self, request: Request, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [self._describe(tool) for tool in self.registry.tools()]}
        if method == "tools/call":
            return await self._tools_call(request, params)
        raise _Refused(METHOD_NOT_FOUND, f"Unknown method: {method!r}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        asked = params.get("protocolVersion")
        if asked != PROTOCOL_VERSION:
            raise _Refused(
                INVALID_PARAMS,
                "Unsupported protocol version.",
                {"supported": [PROTOCOL_VERSION], "requested": asked},
            )
        name = _server_name(self.app)
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": name,
                "title": str(getattr(self.app, "title", None) or name),
                "version": VERSION,
            },
            "instructions": INSTRUCTIONS,
        }

    def _describe(self, tool: Tool) -> dict[str, Any]:
        operation = self._operation(tool)
        return {
            "name": tool.name,
            "description": _description(tool, operation),
            "inputSchema": _published_input(tool),
            "outputSchema": _model_schema(tool.output_model),
            # §3.9: the route this name stands for, so a caller that only has
            # the wire can check the tool *is* that route (§3.8's own rule: an
            # app says what it is, and then it can be held to it).
            ROUTE_EXTENSION: {"method": tool.method, "path": tool.path},
        }

    # -- one tool call ------------------------------------------------------

    async def _tools_call(
        self, request: Request, params: dict[str, Any]
    ) -> dict[str, Any]:
        name = params.get("name")
        tool = self.registry.get(name)
        if tool is None:
            raise _Refused(INVALID_PARAMS, f"Unknown tool: {name!r}")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise _Refused(INVALID_PARAMS, "arguments must be an object.")

        key: str | None = None
        plain: dict[str, Any] = {}
        for argument, value in arguments.items():
            if not (isinstance(argument, str) and argument.startswith("_")):
                plain[argument] = value
                continue
            if argument != IDEMPOTENCY_ARG:
                # The '_' namespace belongs to this layer. Passing a strange
                # one on to the route would be inventing an argument.
                return self._tool_refusal(
                    request,
                    422,
                    "invalid_body",
                    f"{argument!r} is not an argument of {tool.name!r}.",
                    detail={
                        "fields": [
                            {
                                "path": argument,
                                "message": "Reserved by the MCP layer; the only "
                                "reserved argument is _idempotency_key.",
                            }
                        ]
                    },
                )
            if not isinstance(value, str) or not value:
                return self._tool_refusal(
                    request,
                    422,
                    "invalid_body",
                    errors.MESSAGES["invalid_body"],
                    detail={
                        "fields": [
                            {
                                "path": IDEMPOTENCY_ARG,
                                "message": "Is the Idempotency-Key to send: 1-128 "
                                "characters of A-Z a-z 0-9 _ . : -",
                            }
                        ]
                    },
                )
            key = value

        path, query, body, bad = _shape(tool, plain)
        if bad:
            return self._tool_refusal(
                request,
                422,
                "invalid_body",
                errors.MESSAGES["invalid_body"],
                detail={"fields": bad},
            )

        # The caller's own bearer, verbatim: the route re-runs §3.1 and §3.2 on
        # it, so the tool has exactly the authority the MCP request had.
        headers = {"authorization": request.headers.get("authorization") or ""}
        if key is not None:
            headers[IDEMPOTENCY_HEADER] = key
        try:
            answer = await _send(self.app, tool, path, query, body, headers)
        except Exception:  # noqa: BLE001 -- the transport itself failed
            logger.exception("MCP dispatch of %s %s failed", tool.method, path)
            return self._tool_refusal(
                request, 503, "unavailable", errors.MESSAGES["unavailable"]
            )
        if len(answer.content) > MAX_RESULT:
            return self._tool_refusal(
                request,
                413,
                "too_large",
                "The route's answer is larger than this MCP layer carries.",
            )
        try:
            payload = answer.json()
        except ValueError:
            payload = None
        if 200 <= answer.status_code < 300:
            if payload is None:
                payload = {"text": answer.text}
            return _tool_result(payload)
        if not (isinstance(payload, dict) and isinstance(payload.get("error"), dict)):
            # Under /v1 every non-2xx is the envelope; a route outside the
            # contract (or a proxy answer) is wrapped in one rather than handed
            # to the agent as something it cannot read.
            payload = _envelope_body(
                answer.status_code, _code_of(answer.status_code), request
            )
        return _tool_error(payload)

    # -- answers ------------------------------------------------------------

    def _bad_request(
        self, request: Request, message: str, detail: Any = None
    ) -> Response:
        return errors.envelope(
            400,
            "bad_request",
            message,
            request_id=errors.request_id(request),
            detail=detail,
        )

    def _error(
        self, rid: Any, code: int, message: str, data: Any = None
    ) -> JSONResponse:
        error: dict[str, Any] = {"code": int(code), "message": str(message)}
        if data is not None:
            error["data"] = data
        return JSONResponse({"jsonrpc": JSONRPC, "id": rid, "error": error})

    def _tool_refusal(
        self,
        request: Request,
        status: int,
        code: str,
        message: str,
        detail: Any = None,
    ) -> dict[str, Any]:
        """A refusal this layer makes, in the shape a route's refusal has."""
        return _tool_error(
            _envelope_body(status, code, request, message=message, detail=detail)
        )

    # -- the app's OpenAPI --------------------------------------------------

    def _operation(self, tool: Tool) -> dict[str, Any] | None:
        if self._index is None:
            self._index = _route_index(self.app)
        found = self._index.get((tool.method, _path_key(tool.path)))
        return found[1] if found else None


async def _send(
    app: Any,
    tool: Tool,
    path: str,
    query: dict[str, Any] | None,
    body: Any,
    headers: dict[str, str],
) -> httpx.Response:
    """The route's own request, through the app itself -- no socket, no proxy.

    One client per call: the endpoint is stateless, and a client kept on the app
    would be a connection pool holding state this contract does not have.
    ``raise_app_exceptions=False`` so a route that raises is answered by the
    app's own §3.3 handler (a 500 envelope) instead of unwinding into the MCP
    layer.
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url=BASE_URL, timeout=TIMEOUT
    ) as client:
        return await client.request(
            tool.method, path, params=query or None, json=body, headers=headers
        )


# -- parity ------------------------------------------------------------------


def check_parity(app: Any, registry: Registry) -> list[str]:
    """Every way a tool and the route behind it disagree; empty when they match.

    ``tools/list`` publishes a schema that a machine plans against, so a tool
    whose route does not exist, whose arguments are not the model the route
    accepts, whose answer is not the model it publishes, or whose scope is not
    the scope the route demands is a lie told to an agent -- and the agent will
    believe it. This returns one line per lie, and no lines when the registry
    and the app's OpenAPI agree. Run it in the app's own suite (§3.9's
    conformance suite calls it for every tool of every app), so a renamed route
    and a renamed tool cannot drift apart in silence.

    Reading it: a tool's ``input_model`` is compared against the route's
    parameters (for a ``GET``) or its request body (for the others, both when a
    route has both), and its ``output_model`` against the schema of the route's
    first 2xx response. ``_``-prefixed arguments are the MCP layer's own
    (``_idempotency_key``) and are not part of the comparison, titles are
    cosmetic, and ``required`` is compared as a set. The scope comes from the
    route's dependency tree (``auth.SCOPE_MARK``), never from the registry: the
    registry is the thing being checked.
    """
    spec = app.openapi()
    defs = (spec.get("components") or {}).get("schemas") or {}
    routes = _route_index(app)
    out: list[str] = []
    for tool in registry.tools():
        found = routes.get((tool.method, _path_key(tool.path)))
        if found is None:
            out.append(f"{tool.name}: no route answers {tool.method} {tool.path}")
            continue
        path, operation = found
        pairs = (
            ("input", _tool_input(tool), _route_input(operation, defs)),
            ("output", _model_schema(tool.output_model), _route_output(operation, defs)),
        )
        for label, wanted, have in pairs:
            if _comparable(wanted) != _comparable(have):
                out.append(
                    f"{tool.name}: the {label} schema of the tool and of "
                    f"{tool.method} {path} differ: tool {_short(wanted)} vs "
                    f"route {_short(have)}"
                )
        scopes = _route_scopes(app, tool.method, path)
        if scopes != {tool.scope}:
            out.append(
                f"{tool.name}: the tool says scope {tool.scope!r}, but "
                f"{tool.method} {path} demands "
                f"{sorted(scopes) if scopes else 'no scope'}"
            )
    return out


def _comparable(node: Any) -> Any:
    """A schema as parity compares it: the spellings that mean nothing are gone.

    Three pairs of spellings say the same thing, and the two sides (pydantic's
    schema, FastAPI's OpenAPI) do not agree on which they write:

    * ``"default": null`` -- FastAPI leaves it out, pydantic writes it; an
      optional field already reads as null when omitted;
    * ``"properties": {}`` -- a tool that takes no arguments is the empty
      object, and a route that takes none has no ``properties`` at all;
    * ``"additionalProperties": true`` -- what an object says by default.

    Only comparison sees this: what ``tools/list`` publishes keeps every word.
    """
    if not isinstance(node, dict):
        if isinstance(node, list):
            return [_comparable(part) for part in node]
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            if value:
                out[key] = {name: _comparable(sub) for name, sub in value.items()}
            continue
        if key == "default" and value is None:
            continue
        if key == "additionalProperties" and value is True:
            continue
        out[key] = _comparable(value)
    return out


def _route_index(app: Any) -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
    """Every operation of the app's OpenAPI, keyed by ``(METHOD, path)``.

    The path is normalised (``/v1/jobs/{id}`` and ``/v1/jobs/{job_id}`` are one
    route) because a placeholder's name is not part of the address -- but the
    real spelling comes back in the value, for the messages and for the route
    lookup.
    """
    index: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    for path, operations in (app.openapi().get("paths") or {}).items():
        if not isinstance(operations, dict):
            continue
        for method, operation in operations.items():
            if not isinstance(operation, dict) or method.upper() not in METHODS:
                continue
            index[(method.upper(), _path_key(path))] = (path, operation)
    return index


def _route_input(operation: dict[str, Any], defs: dict[str, Any]) -> Any:
    """The schema of everything a route takes: its parameters, its body, or both."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in operation.get("parameters") or []:
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        if not isinstance(name, str):
            continue
        properties[name] = _inline(parameter.get("schema") or {}, defs)
        if parameter.get("required"):
            required.append(name)
    body = operation.get("requestBody")
    extra: dict[str, Any] = {}
    if isinstance(body, dict):
        schema = _inline(
            ((body.get("content") or {}).get("application/json") or {}).get("schema"),
            defs,
        )
        if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
            properties.update(schema["properties"])
            required.extend(
                name
                for name in schema.get("required") or []
                if isinstance(name, str)
            )
            for keyword in ("additionalProperties", "patternProperties"):
                if keyword in schema:
                    extra[keyword] = schema[keyword]
        elif not properties:
            # A body that is not an object (a list, a scalar) is not something a
            # pydantic input model can mirror: report the route's own schema, so
            # the parity line says what the route really takes.
            return _without_docstring(_tidy(schema))
    out: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        out["required"] = sorted(set(required))
    out.update(extra)
    return _without_docstring(_tidy(out))


def _route_output(operation: dict[str, Any], defs: dict[str, Any]) -> Any:
    """The schema of the route's first 2xx answer, or ``None`` when there is none."""
    responses = operation.get("responses") or {}
    for status in sorted(responses):
        if not str(status).startswith("2"):
            continue
        response = responses[status] or {}
        schema = ((response.get("content") or {}).get("application/json") or {}).get(
            "schema"
        )
        return _without_docstring(_tidy(_inline(schema, defs)))
    return None


def _route_scopes(app: Any, method: str, path: str) -> set[str]:
    """The scopes the route itself demands, read off its dependency tree.

    The registry is not asked: the registry is the thing being checked. The
    route is the one whose *own* path is ``path`` -- the path its router was
    given, which is the path a request arrives on because the kit's own routers
    carry ``errors.PREFIX`` themselves and are included without a second prefix.
    """
    out: set[str] = set()
    for route in _routes_under(getattr(app, "routes", None) or ()):
        if getattr(route, "path", None) != path:
            continue
        if method not in (getattr(route, "methods", None) or ()):
            continue
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for dependency in _dependencies(dependant):
            scope = getattr(dependency, auth.SCOPE_MARK, None)
            if isinstance(scope, str) and scope:
                out.add(scope)
    return out


def _routes_under(source: Any) -> Iterator[Any]:
    """Every route under ``source``, however this version of FastAPI keeps them.

    ``include_router`` once copied a router's routes into ``app.routes``; it now
    appends one wrapper per included router, and the wrapper holds the router
    rather than its routes. So this descends by shape -- into anything with
    ``routes``, ``original_router`` or ``router`` -- and yields what is left:
    the ``APIRoute`` objects, a mount, or a plain route. Nothing private is
    imported, so a FastAPI upgrade that renames its wrapper does not break the
    check; the parity line would say so if it ever did.
    """
    if isinstance(source, (list, tuple)):
        for item in source:
            yield from _routes_under(item)
        return
    for name in ("routes", "original_router", "router"):
        inner = getattr(source, name, None)
        if inner is None or inner is source:
            continue
        if isinstance(inner, (list, tuple)) or hasattr(inner, "routes"):
            yield from _routes_under(inner)
            return
    yield source


def _dependencies(dependant: Any) -> Iterator[Any]:
    """Every callable in a route's dependency tree, the route's own included."""
    for sub in getattr(dependant, "dependencies", None) or ():
        yield getattr(sub, "call", None)
        yield from _dependencies(sub)


def _tool_input(tool: Tool) -> Any:
    """The tool's arguments as a schema, without the layer's own arguments."""
    schema = _model_schema(tool.input_model)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        schema = dict(schema)
        schema["properties"] = {
            name: value
            for name, value in properties.items()
            if not str(name).startswith("_")
        }
    required = schema.get("required")
    if isinstance(required, list):
        kept = [name for name in required if not str(name).startswith("_")]
        schema = dict(schema)
        if kept:
            schema["required"] = sorted(kept)
        else:
            schema.pop("required", None)
    return schema


def _model_schema(model: type) -> Any:
    """A pydantic model's JSON Schema, ready to compare with OpenAPI's."""
    raw = model.model_json_schema()
    if not isinstance(raw, dict):
        return _tidy(raw)
    return _without_docstring(_tidy(_inline(raw, raw.get("$defs") or {})))


def _published_input(tool: Tool) -> Any:
    """What ``tools/list`` publishes: the tool's arguments, plus the layer's own.

    Everything that writes carries ``_idempotency_key`` (a run tool requires it,
    §3.9). It cannot be a field of the input model: pydantic refuses a field
    whose name starts with an underscore, which is exactly why the layer owns
    it and the parity check ignores it.
    """
    schema = _tool_input(tool)
    if tool.method in READ_METHODS and not tool.run:
        # A read runs to no effect, so there is nothing for a key to make once:
        # publishing one would be offering an argument that does nothing.
        return schema
    properties = schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        return schema  # not an object schema: nothing to add an argument to
    properties[IDEMPOTENCY_ARG] = dict(_IDEMPOTENCY_SCHEMA)
    if tool.run:
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = sorted(set(required) | {IDEMPOTENCY_ARG})
        else:
            schema["required"] = [IDEMPOTENCY_ARG]
    return schema


# -- schemas, shared with the tests ------------------------------------------


def _tidy(node: Any) -> Any:
    """Drop titles, sort ``required``: the two spellings of the same schema."""
    return _normalise(_clean(node))


def _without_docstring(node: Any) -> Any:
    """The schema without a model's own docstring (its root ``description``).

    Pydantic writes a model's docstring into the schema it emits; FastAPI does
    not write a body model's docstring into the route's schema. It is prose
    about the model either way, and the tool's own description already comes
    from the route's summary -- so the shape is compared, and published,
    without it. Nested descriptions (a field's) are kept: both sides have them.
    """
    if isinstance(node, dict) and isinstance(node.get("description"), str):
        return {key: value for key, value in node.items() if key != "description"}
    return node


def _inline(node: Any, defs: dict[str, Any], seen: tuple[str, ...] = ()) -> Any:
    """Every ``$ref`` replaced by what it points at, ``$defs`` dropped."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            target = defs.get(name)
            if target is None or name in seen:
                return {"$ref": ref}
            return _inline(target, defs, seen + (name,))
        return {
            key: _inline(value, defs, seen)
            for key, value in node.items()
            if key != "$defs"
        }
    if isinstance(node, list):
        return [_inline(value, defs, seen) for value in node]
    return node


def _clean(node: Any) -> Any:
    """Drop every JSON Schema ``title`` (cosmetic; both sides spell them apart).

    Property *names* are never touched, even one called ``title``: a key inside
    ``properties`` maps a name to a schema and is not a title itself.
    """
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "title" and isinstance(value, str):
                continue
            if key == "properties" and isinstance(value, dict):
                out[key] = {name: _clean(sub) for name, sub in value.items()}
                continue
            out[key] = _clean(value)
        return out
    if isinstance(node, list):
        return [_clean(value) for value in node]
    return node


def _normalise(node: Any) -> Any:
    """``required`` is a set of names: its order means nothing, so it is sorted."""
    if isinstance(node, dict):
        out = {key: _normalise(value) for key, value in node.items()}
        required = out.get("required")
        if isinstance(required, list) and all(isinstance(name, str) for name in required):
            out["required"] = sorted(required)
        return out
    if isinstance(node, list):
        return [_normalise(value) for value in node]
    return node


# -- small helpers -----------------------------------------------------------


def _shape(
    tool: Tool, arguments: dict[str, Any]
) -> tuple[str, dict[str, Any] | None, Any, list[dict[str, str]]]:
    """Where each argument goes: the path, the query, or the body.

    A ``{placeholder}`` in the tool's path is filled from the arguments of that
    name (and taken out of the query or the body); the rest is the query for a
    method that has no body and the body for one that has. ``bad`` lists the
    arguments that cannot be sent at all -- a missing path argument, a path
    argument that is not text or a number, a query argument that is not one
    either.
    """
    path = tool.path
    used: set[str] = set()
    bad: list[dict[str, str]] = []
    for placeholder in dict.fromkeys(_PLACEHOLDER.findall(tool.path)):
        if placeholder not in arguments:
            bad.append(
                {"path": placeholder, "message": "Is a path argument of this tool."}
            )
            continue
        value = arguments[placeholder]
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            bad.append(
                {
                    "path": placeholder,
                    "message": "Is a path argument: text or a number.",
                }
            )
            continue
        if _elsewhere(str(value)):
            # An argument that would not arrive as itself: httpx resolves ``.``
            # and ``..`` out of the URL before the request is sent, so the
            # *parent* route answers and the tool never runs, and a ``/`` is
            # never one path segment however it is escaped. §3.9's transport
            # may not reach a route the tool did not name.
            bad.append({"path": placeholder, "message": _ELSEWHERE})
            continue
        path = path.replace("{" + placeholder + "}", quote(str(value), safe=""))
        used.add(placeholder)
    if bad:
        return path, None, None, bad
    rest = {name: value for name, value in arguments.items() if name not in used}
    if tool.method in READ_METHODS:
        query: dict[str, Any] = {}
        for name, value in rest.items():
            if value is None:
                continue
            sent = _query_value(value)
            if sent is None:
                bad.append(
                    {
                        "path": name,
                        "message": "Is a query argument: text, a number, or a list "
                        "of them.",
                    }
                )
                continue
            query[name] = sent
        return path, query or None, None, bad
    return path, None, rest, []


def _elsewhere(value: str) -> bool:
    """Is this path argument a segment, or a way to somewhere else?

    A path argument names one thing, and §3.9 hands it to a route by putting it
    in the URL. ``.`` and ``..`` are not names: the URL is resolved before the
    request is sent, so the request arrives at the *parent* route -- a different
    route, with the tool's authority -- and ``/`` is a segment separator, so the
    value stops being one argument however it is escaped.
    """
    return value in (".", "..") or "/" in value


def _query_value(value: Any) -> Any:
    """One query value (or a repeated parameter), or ``None`` when it cannot be sent."""
    if isinstance(value, list):
        out = [_query_value(item) for item in value]
        if not out or any(
            item is None or isinstance(item, list) for item in out
        ):
            return None
        return out
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def _tool_result(payload: Any) -> dict[str, Any]:
    """The route's 2xx body as a tool result, verbatim where it can be.

    MCP's ``structuredContent`` is an object, so a route that answers with a
    list or a scalar (rare under ``/v1``, which answers objects) arrives as
    ``{"result": …}``; the text block is the same JSON either way.
    """
    structured = payload if isinstance(payload, dict) else {"result": payload}
    return {
        "content": [{"type": "text", "text": _text(structured)}],
        "structuredContent": structured,
        "isError": False,
    }


def _tool_error(payload: dict[str, Any]) -> dict[str, Any]:
    """A refusal, as ``isError`` true: §3.3's envelope is the structured content."""
    return {
        "content": [{"type": "text", "text": _text(payload)}],
        "structuredContent": payload,
        "isError": True,
    }


def _envelope_body(
    status: int,
    code: str,
    request: Request,
    *,
    message: str | None = None,
    detail: Any = None,
) -> dict[str, Any]:
    """§3.3's envelope as a plain dict -- the shape an ``isError`` carries.

    Built by the same function that answers a non-2xx, so a refusal this layer
    makes is the refusal a route makes, down to the shape of ``request_id``.
    """
    answer = errors.envelope(
        status,
        code,
        message if message is not None else errors.MESSAGES.get(code, ""),
        request_id=errors.request_id(request),
        detail=detail,
    )
    return json.loads(answer.body)


def _code_of(status: int) -> str:
    """The contract's code for a status, for an answer that broke the contract."""
    code = errors.STATUS_CODES.get(status)
    if code is None:
        code = "server_error" if status >= 500 else "bad_request"
    return code


def _text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _short(node: Any) -> str:
    """A schema, in a line short enough to read in a failure message."""
    text = json.dumps(node, sort_keys=True, ensure_ascii=False)
    return text if len(text) <= 400 else text[:397] + "..."


def _is_id(value: Any) -> bool:
    """A JSON-RPC id: a string or an integer, and not a boolean (a Python bool is one)."""
    return isinstance(value, str) or (
        isinstance(value, int) and not isinstance(value, bool)
    )


def _media_type(header: str | None) -> str | None:
    """The media type of a Content-Type header, lower case, without parameters.

    ``None`` when there is no header at all: a missing declaration is read as
    JSON (what every MCP client sends, and what FastAPI itself would do on any
    other ``/v1`` route), while a *declared* foreign type is 415 -- §3.3's row,
    applied here too.
    """
    if header is None:
        return None
    return header.split(";", 1)[0].strip().lower() or None


def _path_key(path: str) -> str:
    """A route's address with its placeholder names removed."""
    return _PLACEHOLDER.sub("{}", str(path)).rstrip("/") or "/"


def _server_name(app: Any) -> str:
    """The app's name for ``serverInfo``: its title, made into a token."""
    title = str(getattr(app, "title", None) or "app").strip().lower()
    name = re.sub(r"[^a-z0-9._-]+", "-", title).strip("-")
    return (name or "app")[:64]


def _description(tool: Tool, operation: dict[str, Any] | None) -> str:
    """A tool's description, from the route's own docstring (or its summary).

    The route is the documentation of the tool: writing it twice is how the two
    come to disagree.
    """
    if isinstance(operation, dict):
        for key in ("description", "summary"):
            text = operation.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return f"{tool.method} {tool.path}"
