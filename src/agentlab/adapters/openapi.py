"""OpenAPI analysis: find chat-like endpoints, request fields and auth schemes (spec section 19)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agentlab.core.models import ApiConfig
from agentlab.core.models.base import Model

INPUT_FIELDS = ("input", "message", "prompt", "query", "question", "text", "content", "user_input")
SESSION_FIELDS = ("session_id", "sessionId", "conversation_id", "thread_id", "chat_id")
OUTPUT_FIELDS = ("output", "response", "answer", "message", "text", "reply", "content", "result")


class Endpoint(Model):
    method: str
    path: str
    summary: str = ""
    input_field: str | None = None
    session_field: str | None = None
    request_schema: dict[str, Any] = Field(default_factory=dict)
    response_fields: list[str] = Field(default_factory=list)
    streaming: bool = False
    auth: list[str] = Field(default_factory=list)
    chat_score: float = 0.0


class OpenApiAnalysis(Model):
    title: str = ""
    version: str = ""
    servers: list[str] = Field(default_factory=list)
    security_schemes: dict[str, str] = Field(default_factory=dict)
    endpoints: list[Endpoint] = Field(default_factory=list)

    def best_chat_endpoint(self) -> Endpoint | None:
        cands = [e for e in self.endpoints if e.chat_score > 0]
        return max(cands, key=lambda e: e.chat_score, default=None)


def _resolve(spec: dict[str, Any], node: Any, depth: int = 0) -> Any:
    if depth > 8 or not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        cur: Any = spec
        for part in ref[2:].split("/"):
            cur = cur.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(cur, dict) else {}
        return _resolve(spec, cur, depth + 1)
    return node


def analyze_openapi(spec: dict[str, Any]) -> OpenApiAnalysis:
    schemes = {}
    for name, sch in ((spec.get("components") or {}).get("securitySchemes") or {}).items():
        sch = _resolve(spec, sch)
        t = sch.get("type", "")
        schemes[name] = f"{t}:{sch.get('scheme') or sch.get('in') or ''}".strip(":")
    out = OpenApiAnalysis(
        title=(spec.get("info") or {}).get("title", ""),
        version=(spec.get("info") or {}).get("version", ""),
        servers=[s.get("url", "") for s in spec.get("servers") or []],
        security_schemes=schemes,
    )
    global_sec = [next(iter(r)) for r in spec.get("security") or [] if r]
    for path, item in (spec.get("paths") or {}).items():
        for method, op in (item or {}).items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"} or not isinstance(op, dict):
                continue
            body = _resolve(
                spec,
                (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema", {}),
            )
            props = (body or {}).get("properties", {}) or {}
            props = {k: _resolve(spec, v) for k, v in props.items()}
            input_field = next((f for f in INPUT_FIELDS if f in props), None)
            session_field = next((f for f in SESSION_FIELDS if f in props), None)
            resp_fields: list[str] = []
            for code, r in (op.get("responses") or {}).items():
                if str(code).startswith("2"):
                    content = _resolve(spec, r).get("content") or {}
                    sc = _resolve(spec, (content.get("application/json") or {}).get("schema", {}))
                    resp_fields = list((sc.get("properties") or {}).keys()) if isinstance(sc, dict) else []
                    streaming = "text/event-stream" in content
                    break
            else:
                streaming = False
            sec = [next(iter(r)) for r in op.get("security") or [] if r] or global_sec
            score = 0.0
            if method.lower() == "post" and input_field:
                score += 1.0
                if any(w in path.lower() for w in ("chat", "ask", "agent", "message", "completion", "query", "run")):
                    score += 1.0
                if any(f in resp_fields for f in OUTPUT_FIELDS):
                    score += 0.5
            out.endpoints.append(
                Endpoint(
                    method=method.upper(),
                    path=path,
                    summary=op.get("summary", "") or op.get("operationId", ""),
                    input_field=input_field,
                    session_field=session_field,
                    request_schema=body or {},
                    response_fields=resp_fields,
                    streaming=streaming,
                    auth=sec,
                    chat_score=score,
                )
            )
    return out


def suggest_api_config(analysis: OpenApiAnalysis, base_url: str) -> ApiConfig | None:
    ep = analysis.best_chat_endpoint()
    if ep is None or ep.input_field is None:
        return None
    template: dict[str, Any] = {ep.input_field: "{{input}}"}
    if ep.session_field:
        template[ep.session_field] = "{{session_id}}"
    cfg = ApiConfig(
        url=base_url.rstrip("/") + ep.path,
        method="POST",
        request_template=template,
        protocol="sse" if ep.streaming else "rest",
    )
    out_field = next((f for f in OUTPUT_FIELDS if f in ep.response_fields), None)
    if out_field:
        cfg.response.output = f"$.{out_field}"
    return cfg
