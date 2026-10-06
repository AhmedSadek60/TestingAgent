"""OpenAPI analysis (spec section 19): find the endpoint that talks to the agent, the fields of its request and answer,
and how it authenticates; and, when the owner gave only the document, turn that into the target's ``api`` settings.

The document is somebody's data, not an instruction. It can describe an endpoint, and AgentLab uses that description to
fill in what the owner left out (the endpoint's path, the name of the field that carries the message, where the answer
is). It never decides *where tests are sent*: a server the document names is used only when it is the host the document
was read from, because the owner authorised testing the target they named, not whatever a file says.

Nothing here is guessed silently: every setting taken from the document is returned as a note, and everything that could
not be worked out is returned as a warning that says what the owner should add.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import Field

from agentlab.core.models import ApiConfig, TargetSpec
from agentlab.core.models.base import Model
from agentlab.core.models.target import DEFAULT_REQUEST_TEMPLATE, ResponseMapping
from agentlab.security import safeyaml

INPUT_FIELDS = ("input", "message", "prompt", "query", "question", "text", "content", "user_input")
SESSION_FIELDS = ("session_id", "sessionId", "conversation_id", "thread_id", "chat_id")
OUTPUT_FIELDS = ("output", "response", "answer", "message", "text", "reply", "content", "result")
CHAT_WORDS = ("chat", "ask", "agent", "message", "completion", "query", "run")

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
MAX_OPERATIONS = 2000
MAX_PROPERTIES = 200
#: the largest OpenAPI document that is read (bytes)
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
_DEFAULT_PORTS = {"http": 80, "https": 443}


class Endpoint(Model):
    method: str
    path: str
    summary: str = ""
    input_field: str | None = None
    session_field: str | None = None
    request_schema: dict[str, Any] = Field(default_factory=dict)
    response_fields: list[str] = Field(default_factory=list)
    streaming: bool = False
    #: the success response also offers application/json (so a streaming endpoint is not the only way to read it)
    json_response: bool = True
    auth: list[str] = Field(default_factory=list)
    chat_score: float = 0.0
    #: required request fields other than the message and the session, with the value the document suggests for them
    defaults: dict[str, Any] = Field(default_factory=dict)
    #: required request fields AgentLab has no value for: the endpoint cannot be called without them
    unfillable: list[str] = Field(default_factory=list)


class OpenApiAnalysis(Model):
    title: str = ""
    version: str = ""
    servers: list[str] = Field(default_factory=list)
    security_schemes: dict[str, str] = Field(default_factory=dict)
    endpoints: list[Endpoint] = Field(default_factory=list)
    #: the document describes more operations than are analysed
    truncated: bool = False

    def chat_candidates(self) -> list[Endpoint]:
        """Endpoints that look like they answer a message, best first: ones AgentLab can call as they are (no path
        parameter, nothing required that it cannot fill) before ones it cannot, then by score, then by path so the choice
        never depends on the order of the document."""
        cands = [e for e in self.endpoints if e.chat_score > 0]
        return sorted(cands, key=lambda e: (_needs_more(e), -e.chat_score, len(e.path), e.path, e.method))

    def best_chat_endpoint(self) -> Endpoint | None:
        cands = self.chat_candidates()
        return cands[0] if cands else None

    def endpoint_at(self, path: str, method: str) -> Endpoint | None:
        """The operation whose path ends the request path (the server's own base path sits in front of it); the longest
        match wins."""
        request = "/" + path.strip("/")
        best: Endpoint | None = None
        for e in self.endpoints:
            if e.method != method.upper() or e.path.strip("/") == "":
                continue
            pattern = "/".join(re.escape(p) if "{" not in p else "[^/]+" for p in e.path.strip("/").split("/"))
            if re.search(r"(^|/)" + pattern + "$", request.lstrip("/")) and (
                best is None or len(e.path) > len(best.path)
            ):
                best = e
        return best


def _needs_more(e: Endpoint) -> bool:
    return "{" in e.path or bool(e.unfillable)


def _dict(x: Any) -> dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _list(x: Any) -> list[Any]:
    return x if isinstance(x, list) else []


def _resolve(spec: dict[str, Any], node: Any, depth: int = 0) -> Any:
    """Follow ``$ref`` pointers inside the document (``#/…`` only; nothing outside it is ever fetched)."""
    if depth > 8 or not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        cur: Any = spec
        for part in ref[2:].split("/"):
            cur = cur.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(cur, dict) else {}
        return _resolve(spec, cur, depth + 1)
    return node


def _properties(spec: dict[str, Any], schema: Any, depth: int = 0) -> dict[str, Any]:
    """The named properties of an object schema, including those it inherits through ``allOf``."""
    schema = _resolve(spec, schema)
    if not isinstance(schema, dict) or depth > 4:
        return {}
    props: dict[str, Any] = {}
    for member in _list(schema.get("allOf"))[:8]:
        props.update(_properties(spec, member, depth + 1))
    for name, value in list(_dict(schema.get("properties")).items())[:MAX_PROPERTIES]:
        props[str(name)] = _resolve(spec, value)
    return props


def _required(spec: dict[str, Any], schema: Any, depth: int = 0) -> list[str]:
    schema = _resolve(spec, schema)
    if not isinstance(schema, dict) or depth > 4:
        return []
    names: list[str] = []
    for member in _list(schema.get("allOf"))[:8]:
        names += _required(spec, member, depth + 1)
    names += [r for r in _list(schema.get("required")) if isinstance(r, str)]
    return list(dict.fromkeys(names))


def _is_text(prop: Any) -> bool:
    """A field that can carry a message: a string (or a schema that does not say what it is)."""
    kind = _dict(prop).get("type")
    return kind is None or kind == "string" or (isinstance(kind, list) and "string" in kind)


def _suggested_value(prop: Any) -> Any:
    p = _dict(prop)
    for key in ("default", "const", "example"):
        if key in p:
            return p[key]
    enum = _list(p.get("enum"))
    return enum[0] if enum else None


def _json_media(content: dict[str, Any]) -> dict[str, Any]:
    if "application/json" in content:
        return _dict(content["application/json"])
    for media, value in content.items():
        if "json" in str(media).lower():
            return _dict(value)
    return {}


def _request_schema(spec: dict[str, Any], op: dict[str, Any], swagger2: bool) -> Any:
    if swagger2:
        for param in _list(op.get("parameters")):
            p = _dict(_resolve(spec, param))
            if p.get("in") == "body":
                return p.get("schema", {})
        return {}
    body = _dict(_resolve(spec, op.get("requestBody")))
    return _json_media(_dict(body.get("content"))).get("schema", {})


def _success_response(spec: dict[str, Any], op: dict[str, Any], swagger2: bool) -> tuple[list[str], bool, bool]:
    """(property names of the success body, streams events, also offers JSON)"""
    for code, raw in _dict(op.get("responses")).items():
        if not str(code).startswith("2"):
            continue
        response = _dict(_resolve(spec, raw))
        if swagger2:
            produces = [str(m) for m in _list(op.get("produces")) or _list(spec.get("produces"))]
            streaming = "text/event-stream" in produces
            props = _properties(spec, response.get("schema", {}))
            return list(props), streaming, (not streaming) or any("json" in m for m in produces)
        content = _dict(response.get("content"))
        props = _properties(spec, _json_media(content).get("schema", {}))
        return list(props), "text/event-stream" in content, any("json" in str(m).lower() for m in content)
    return [], False, True


def _requirement_names(requirements: Any) -> list[str]:
    return [str(next(iter(r))) for r in _list(requirements) if isinstance(r, dict) and r]


def _security_schemes(spec: dict[str, Any]) -> dict[str, str]:
    raw = _dict(_dict(spec.get("components")).get("securitySchemes")) or _dict(spec.get("securityDefinitions"))
    out: dict[str, str] = {}
    for name, scheme in list(raw.items())[:50]:
        s = _dict(_resolve(spec, scheme))
        out[str(name)] = f"{s.get('type', '')}:{s.get('scheme') or s.get('in') or ''}".strip(":")
    return out


def _servers(spec: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for server in _list(spec.get("servers"))[:10]:
        s = _dict(server)
        url = s.get("url")
        if not isinstance(url, str) or not url:
            continue
        for name, var in _dict(s.get("variables")).items():
            url = url.replace("{" + str(name) + "}", str(_dict(var).get("default", "")))
        out.append(url)
    if "swagger" in spec and "openapi" not in spec:  # Swagger 2.0: host + basePath (+ schemes)
        host, base = spec.get("host"), spec.get("basePath")
        base = base if isinstance(base, str) else ""
        if isinstance(host, str) and host:
            schemes = [s for s in _list(spec.get("schemes")) if isinstance(s, str)]
            out.append(f"{schemes[0]}://{host}{base}" if schemes else f"//{host}{base}")
        elif base:
            out.append(base)
    return out


def analyze_openapi(document: Any) -> OpenApiAnalysis:
    """Read an OpenAPI 3.x or Swagger 2.0 document. Whatever its shape (it is data from outside), this returns an
    analysis and never raises: what cannot be read is left out."""
    spec = _dict(document)
    info = _dict(spec.get("info"))
    swagger2 = "swagger" in spec and "openapi" not in spec
    out = OpenApiAnalysis(
        title=str(info.get("title") or "")[:200],
        version=str(info.get("version") or "")[:50],
        servers=_servers(spec),
        security_schemes=_security_schemes(spec),
    )
    global_sec = _requirement_names(spec.get("security"))
    count = 0
    for path, item in _dict(spec.get("paths")).items():
        if not isinstance(path, str):
            continue
        for method, raw_op in _dict(_resolve(spec, item)).items():
            if str(method).lower() not in HTTP_METHODS or not isinstance(raw_op, dict):
                continue
            count += 1
            if count > MAX_OPERATIONS:
                out.truncated = True
                return out
            out.endpoints.append(_endpoint(spec, path, str(method).lower(), raw_op, swagger2, global_sec))
    return out


def _endpoint(
    spec: dict[str, Any], path: str, method: str, op: dict[str, Any], swagger2: bool, global_sec: list[str]
) -> Endpoint:
    body = _request_schema(spec, op, swagger2)
    props = _properties(spec, body)
    input_field = next((f for f in INPUT_FIELDS if f in props and _is_text(props[f])), None)
    session_field = next((f for f in SESSION_FIELDS if f in props), None)
    response_fields, streaming, json_response = _success_response(spec, op, swagger2)
    defaults: dict[str, Any] = {}
    unfillable: list[str] = []
    for name in _required(spec, body):
        if name in (input_field, session_field):
            continue
        value = _suggested_value(props.get(name))
        if value is None:
            unfillable.append(name)
        else:
            defaults[name] = value
    score = 0.0
    if method == "post" and input_field:
        score += 1.0
        if any(w in path.lower() for w in CHAT_WORDS):
            score += 1.0
        if any(f in response_fields for f in OUTPUT_FIELDS):
            score += 0.5
    summary = op.get("summary") or op.get("operationId") or ""
    return Endpoint(
        method=method.upper(),
        path=path,
        summary=str(summary)[:200],
        input_field=input_field,
        session_field=session_field,
        # only a likely chat endpoint keeps its schema: the profile stores the analysis and most operations are irrelevant
        request_schema=_dict(_resolve(spec, body)) if score > 0 else {},
        response_fields=response_fields,
        streaming=streaming,
        json_response=json_response,
        auth=_requirement_names(op.get("security")) or global_sec,
        chat_score=score,
        defaults=defaults,
        unfillable=unfillable,
    )


def parse_openapi_text(text: str) -> Any:
    """JSON or YAML text to a value. YAML goes through :mod:`agentlab.security.safeyaml`; the document is not trusted."""
    if text.lstrip()[:1] in ("{", "["):
        try:
            return json.loads(text)
        except (ValueError, RecursionError):
            pass  # not strict JSON: YAML's flow style may still read it
    return safeyaml.load(text)


# ----------------------------------------------------------------------------- from the document to the target
@dataclass
class ApiResolution:
    """``spec`` is the target with the ``api`` settings the document supplied; the original object when none applied."""

    spec: TargetSpec
    #: settings taken from the document (observed in the document, applied by AgentLab)
    notes: list[str] = field(default_factory=list)
    #: what could not be worked out, with what the owner can add
    warnings: list[str] = field(default_factory=list)


def _origin(url: str) -> tuple[str, str, int | None] | None:
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None


def _show(url: str) -> str:
    """A URL from a document, for messages: scheme, host, port and path only (a server may carry a user name or token)."""
    try:
        parts = urlsplit(url)
        if not parts.hostname:
            return url.split("?", 1)[0][:80]
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname}{port}{parts.path}"
    except ValueError:
        return url[:60]


def _server_on_document_host(servers: list[str], document_url: str) -> str | None:
    """The first server that is on the host the document was read from (a relative one always is); none when the
    document names only other hosts."""
    here = _origin(document_url)
    for server in servers or ["/"]:
        joined = urljoin(document_url, server)
        if here is not None and _origin(joined) == here:
            return joined
    return None


def _template(ep: Endpoint) -> dict[str, Any]:
    template: dict[str, Any] = {}
    if ep.input_field:
        template[ep.input_field] = "{{input}}"
    if ep.session_field:
        template[ep.session_field] = "{{session_id}}"
    template.update(ep.defaults)
    return template


def _settings_from(api: ApiConfig, ep: Endpoint) -> tuple[dict[str, Any], list[str]]:
    """What the document says about how to talk to ``ep``, limited to what the owner left at the default, and a note for
    each. "Left at the default" is decided by value, not by whether the field was written: a target that went through a
    job queue or the database has every field written."""
    updates: dict[str, Any] = {}
    notes: list[str] = []
    if api.request_template == DEFAULT_REQUEST_TEMPLATE and ep.input_field:
        template = _template(ep)
        if template != DEFAULT_REQUEST_TEMPLATE:
            updates["request_template"] = template
            notes.append(f"the request carries {json.dumps(template)}")
    if api.response == ResponseMapping():
        answer = next((f for f in OUTPUT_FIELDS if f in ep.response_fields), None)
        if answer:
            updates["response"] = api.response.model_copy(update={"output": f"$.{answer}"})
            notes.append(f"the answer is read from '{answer}'")
    if api.protocol == "rest" and ep.streaming and not ep.json_response:
        updates["protocol"] = "sse"
        notes.append("the endpoint answers with a stream of events, so it is read as SSE")
    return updates, notes


def resolve_api(
    spec: TargetSpec, analysis: OpenApiAnalysis | None, *, document_url: str | None = None
) -> ApiResolution:
    """Complete ``spec.api`` from the OpenAPI document, without overriding anything the owner wrote.

    * No ``api.url``: the chat endpoint is chosen from the document and its address built from the server the document
      names *when that server is the host the document was read from* (otherwise the owner is asked for ``--api-url``).
    * ``api.url`` given: it is where tests go, whatever the document says; the document only fills in the request
      template, the answer's location and the protocol the owner left at their defaults.
    """
    result = ApiResolution(spec=spec)
    api = spec.api
    if api is None:
        return result
    if api.url:
        ep = analysis.endpoint_at(urlsplit(api.url).path, api.method) if analysis else None
        if ep is not None:
            updates, notes = _settings_from(api, ep)
            if updates:
                result.notes = [f"OpenAPI: for {ep.method} {ep.path}, {n}" for n in notes]
                result.spec = spec.model_copy(update={"api": api.model_copy(update=updates)})
            if ep.unfillable and api.request_template == DEFAULT_REQUEST_TEMPLATE:
                result.warnings.append(f"OpenAPI: {_unfillable(ep)}")
        return result

    # ---- no address: find it in the document
    ask = "give --api-url (the address that answers a message) and, if its request differs, api.request_template"
    if analysis is None:
        result.warnings.append(f"OpenAPI: there is no address to test, because the document could not be read; {ask}")
        return result
    candidates = analysis.chat_candidates()
    if not candidates:
        shown = len(analysis.endpoints)
        result.warnings.append(
            f"OpenAPI: none of the {shown} operation(s) in the document takes a text message as JSON (a field named "
            f"{', '.join(INPUT_FIELDS[:5])}, …), so the endpoint cannot be chosen; {ask}"
        )
        return result
    ep = candidates[0]
    if "{" in ep.path:
        result.warnings.append(
            f"OpenAPI: the best match, {ep.method} {ep.path}, has a path parameter AgentLab has no value for; {ask}"
        )
        return result
    if ep.unfillable:
        result.warnings.append(f"OpenAPI: {_unfillable(ep)}; {ask}")
        return result
    base = _server_on_document_host(analysis.servers, document_url) if document_url else None
    if base is None:
        named = analysis.servers[0] if analysis.servers else "/"
        origin = f"the host the document was read from ({_show(document_url)})" if document_url else "a known host"
        result.warnings.append(
            f"OpenAPI: the document names the server {_show(named)}, which is not {origin}, and tests are not sent to a "
            f"host the target does not name. To test {ep.method} {ep.path} there, add --api-url "
            f"{_show(named).rstrip('/')}{ep.path}"
        )
        return result
    url = base.rstrip("/") + ep.path
    result.notes.append(
        f"OpenAPI: no api.url was given, so tests go to {ep.method} {url} ({ep.summary or 'no summary'})"
    )
    if len(candidates) > 1:
        others = ", ".join(f"{e.method} {e.path}" for e in candidates[1:4])
        result.notes.append(
            f"OpenAPI: {len(candidates)} endpoints look like chat; the best match was chosen. Others: {others}. "
            "Set api.url to test a different one"
        )
    updates, notes = _settings_from(api, ep)
    result.notes += [f"OpenAPI: {n}" for n in notes]
    result.spec = spec.model_copy(update={"api": api.model_copy(update={"url": url, "method": "POST", **updates})})
    return result


def _unfillable(ep: Endpoint) -> str:
    return (
        f"{ep.method} {ep.path} requires {', '.join(repr(n) for n in ep.unfillable)}, which AgentLab has no value for; "
        "set api.request_template with values for them"
    )
