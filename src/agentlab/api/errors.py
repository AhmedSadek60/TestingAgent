"""How failures reach the person calling the API: one JSON shape, the right status, and never a stack trace or a secret."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from agentlab.core.errors import (
    AgentLabError,
    CredentialError,
    InfrastructureError,
    NotFoundError,
    PolicyBlocked,
    UnsupportedCapability,
    UserError,
)
from agentlab.security.redactor import redact

log = logging.getLogger(__name__)


class ConflictError(UserError):
    """The request is valid but the thing is in a state that does not allow it (cancelling a finished run)."""


def _body(kind: str, message: str, details: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"kind": kind, "message": redact(message)}
    if details:
        error["details"] = details
    return {"error": error}


def status_for(exc: AgentLabError) -> tuple[int, str]:
    if isinstance(exc, NotFoundError):
        return 404, "not_found"
    if isinstance(exc, ConflictError):
        return 409, "conflict"
    if isinstance(exc, PolicyBlocked):
        return 403, "policy_blocked"
    if isinstance(exc, CredentialError):
        return 422, "credential_error"
    if isinstance(exc, UnsupportedCapability):
        return 422, "unsupported"
    if isinstance(exc, UserError):
        return 422, "user_error"
    if isinstance(exc, InfrastructureError):
        return 503, "unavailable"
    return 500, exc.kind.value.lower()


def install(app: FastAPI) -> None:
    @app.exception_handler(AgentLabError)
    async def agentlab_error(_: Request, exc: AgentLabError) -> JSONResponse:
        status, kind = status_for(exc)
        if status >= 500:
            log.warning("request failed: %s", redact(str(exc)))
        return JSONResponse(_body(kind, str(exc)), status_code=status)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        kinds = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            413: "too_large",
            429: "rate_limited",
        }
        return JSONResponse(
            _body(kinds.get(exc.status_code, "error"), str(exc.detail)),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in e.get("loc", ())), "problem": redact(str(e.get("msg", "")))}
            for e in exc.errors()[:20]
        ]
        first = details[0] if details else {"field": "", "problem": "invalid request"}
        return JSONResponse(
            _body("invalid_request", f"{first['field']}: {first['problem']}".strip(": "), details), status_code=422
        )

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error in a request")  # the log filter redacts; the person gets no internals
        return JSONResponse(_body("internal_error", "the server could not complete the request"), status_code=500)
