"""McpAdapter: a Model Context Protocol server as the target (spec sections 19 and 20, taxonomy I).

An MCP server is not a chat partner: it is a set of tools that *other* agents call. AgentLab therefore tests it the way
an agent would use it: list its tools, read what it tells every client about them, and call them, valid and hostile.
A test's input is one tool call, written as JSON::

    {"tool": "read_file", "arguments": {"path": "notes.txt"}}

Transports: streamable HTTP and SSE connect to a server that is already running. stdio *starts* the owner's command, and
untrusted code is never started on the evaluator host: the command runs inside the sandbox from a copy of the target's
repository and the host only runs the container client that carries its standard streams.

The protocol client keeps task groups open for as long as the connection lives and anyio requires the task that entered
one to be the task that leaves it, so one background task owns the connection and every request is made through it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from agentlab import __version__
from agentlab.adapters.base import ADAPTERS, AdapterCapabilities, AdapterContext, AgentAdapter
from agentlab.core.errors import AgentLabError, PolicyBlocked, TargetError, UnsupportedCapability, UserError
from agentlab.core.models import AgentEvent, AgentRequest, AgentResponse, McpConfig, TargetSpec, ToolCall, ToolInfo
from agentlab.repository.signals import infer_side_effects
from agentlab.sandbox.base import Sandbox, SandboxSpec

log = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 200_000
AGENT_DIR = "/agent"
DEFAULT_IMAGE = SandboxSpec().image
INSTALL_HINT = "MCP targets need the optional 'mcp' package: pip install 'agentlab[mcp]'"


def parse_tool_call(text: str) -> tuple[str, dict[str, Any]]:
    """The tool name and arguments of a test input; anything else is a mistake in the test, not a finding."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise UserError(
            'an MCP test sends a tool call written as JSON, {"tool": "<name>", "arguments": {...}}; '
            f"this input is not JSON: {text[:60]!r}"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("tool"), str) or not data["tool"]:
        raise UserError('an MCP tool call needs a "tool" name, for example {"tool": "<name>", "arguments": {...}}')
    args = data.get("arguments", {})
    if not isinstance(args, dict):
        raise UserError('"arguments" of an MCP tool call must be an object')
    return data["tool"], args


def side_effects_of(name: str, description: str, annotations: Any) -> str:
    """What a tool does to the world: the server's own hints when it gives them, otherwise inferred from its name."""
    if annotations is not None:
        if getattr(annotations, "destructive_hint", None):
            return "destructive"
        if getattr(annotations, "read_only_hint", None):
            return "read"
    return infer_side_effects(name, description)


def flatten_content(blocks: list[Any]) -> str:
    """The text of a tool result. Non-text content is described, never decoded: it is data from the target."""
    parts: list[str] = []
    for block in blocks:
        kind = getattr(block, "type", "")
        if kind == "text":
            parts.append(str(getattr(block, "text", "")))
        elif kind == "resource":
            res = getattr(block, "resource", None)
            parts.append(str(getattr(res, "text", None) or f"[resource {getattr(res, 'uri', '')}]"))
        elif kind == "resource_link":
            parts.append(f"[resource link {getattr(block, 'uri', '')}]")
        else:
            parts.append(f"[{kind or 'unknown'} content: {getattr(block, 'mime_type', 'binary')}]")
    return "\n".join(parts)


def _clip(text: str) -> str:
    return (
        text if len(text) <= MAX_OUTPUT_CHARS else text[:MAX_OUTPUT_CHARS] + f"...[{len(text) - MAX_OUTPUT_CHARS} more]"
    )


class McpAdapter(AgentAdapter):
    kind = "mcp"

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        super().__init__(spec, ctx)
        if spec.mcp is None:
            raise TargetError("mcp adapter requires target.mcp")
        self.cfg: McpConfig = spec.mcp
        if self.cfg.transport in {"streamable_http", "sse"} and not self.cfg.url:
            raise UserError(f"mcp.url is required for the {self.cfg.transport} transport")
        if self.cfg.transport == "stdio" and not self.cfg.command:
            raise UserError("mcp.command is required for the stdio transport")
        self.capabilities = AdapterCapabilities(
            conversational=False,
            sessions=False,
            reports_tool_calls=True,
            reports_events=True,
            notes=["tests send one JSON tool call each; this interface cannot be asked questions"],
        )
        self._client: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._failure: str | None = None
        self._sandbox: Sandbox | None = None
        self.server_info: dict[str, Any] = {}

    # ------------------------------------------------------------------------------------------------- connection
    async def open(self) -> None:
        try:
            import mcp  # noqa: F401  (availability check only)
        except ImportError as exc:
            raise UnsupportedCapability(INSTALL_HINT) from exc
        if self.cfg.transport != "stdio":
            assert self.cfg.url is not None
            self.ctx.egress.check(self.cfg.url)
        loop = asyncio.get_running_loop()
        opened: asyncio.Future[None] = loop.create_future()
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._own_connection(opened), name="agentlab-mcp-connection")
        try:
            await asyncio.wait_for(asyncio.shield(opened), timeout=self.cfg.timeout_seconds + 30)
        except BaseException:
            await self.close()
            raise

    async def _own_connection(self, opened: asyncio.Future[None]) -> None:
        from mcp import Client
        from mcp.types import Implementation

        assert self._stop is not None
        try:
            async with contextlib.AsyncExitStack() as stack:
                transport = await self._transport(stack)
                client = await stack.enter_async_context(
                    Client(
                        transport,
                        read_timeout_seconds=self.cfg.timeout_seconds,
                        client_info=Implementation(name="agentlab", version=__version__),
                    )
                )
                info = getattr(client, "server_info", None)
                self.server_info = {
                    "name": getattr(info, "name", None),
                    "version": getattr(info, "version", None),
                    "instructions": (getattr(client, "instructions", None) or "")[:500],
                }
                self._client = client
                if not opened.done():
                    opened.set_result(None)
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # connection refused, protocol mismatch, the server going away mid-run
            self._failure = f"{type(exc).__name__}: {str(exc)[:200]}"
            if not opened.done():
                # a refusal by AgentLab's own policy (no sandbox, a blocked address, a missing credential) keeps its type
                opened.set_exception(
                    exc
                    if isinstance(exc, AgentLabError)
                    else TargetError(f"cannot connect to the MCP server: {self._failure}")
                )
            else:
                log.warning("MCP connection ended: %s", self._failure)
        finally:
            self._client = None

    async def _transport(self, stack: contextlib.AsyncExitStack) -> Any:
        import httpx2
        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import streamable_http_client

        headers = self._headers()
        if self.cfg.transport == "streamable_http":
            http = await stack.enter_async_context(
                httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(self.cfg.timeout_seconds, read=300.0))
            )
            assert self.cfg.url is not None
            return streamable_http_client(self.cfg.url, http_client=http)
        if self.cfg.transport == "sse":
            assert self.cfg.url is not None
            return sse_client(self.cfg.url, headers=headers, timeout=self.cfg.timeout_seconds)
        return await self._stdio_transport(stack)

    def _headers(self) -> dict[str, str]:
        headers = dict(self.cfg.headers)
        if self.cfg.auth_credential:
            if self.ctx.credentials is None:
                raise TargetError("credential requested but no CredentialManager is configured")
            headers.update(self.ctx.credentials.auth_headers(self.cfg.auth_credential, self.cfg.url))
        return headers

    async def _stdio_transport(self, stack: contextlib.AsyncExitStack) -> Any:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        provider = self.ctx.sandbox
        if provider is None:
            raise PolicyBlocked("an MCP server over stdio runs in the sandbox, and no sandbox provider is configured")
        assert self.cfg.command is not None
        sandbox = await provider.create(
            SandboxSpec(
                image=self.cfg.image or DEFAULT_IMAGE,
                workdir=AGENT_DIR,
                env=dict(self.cfg.env),
                timeout_seconds=3600,
                network="none",
                extra_tmpfs=[],
                labels={"run": self.ctx.run_id or ""},
            )
        )
        self._sandbox = sandbox
        stack.push_async_callback(self._release_sandbox)
        repo = self.ctx.extras.get("repo_path") or (self.spec.repository.path if self.spec.repository else None)
        if repo and Path(str(repo)).is_dir():
            await sandbox.put_dir(Path(str(repo)), AGENT_DIR)
        argv = sandbox.attach_argv(self.cfg.command, workdir=AGENT_DIR, timeout=3600)
        host_env = {
            k: os.environ[k]
            for k in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT")
            if k in os.environ
        }
        params = StdioServerParameters(command=argv[0], args=argv[1:], env=host_env)
        return stdio_client(params)

    async def _release_sandbox(self) -> None:
        if self._sandbox is not None:
            with contextlib.suppress(Exception):
                await self._sandbox.close()
            self._sandbox = None

    async def close(self) -> None:
        if self._stop is not None:
            self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=20)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            except Exception:  # noqa: S110 - the connection is being torn down anyway
                pass
        await self._release_sandbox()

    # ------------------------------------------------------------------------------------------------- operations
    def _connected(self) -> Any:
        if self._client is None:
            raise TargetError(f"not connected to the MCP server ({self._failure or 'connection closed'})")
        return self._client

    async def probe(self) -> dict[str, Any]:
        """Reachable means the server answers a request for its tool list (a cheap, side-effect-free call)."""
        try:
            page = await self._connected().list_tools()
        except Exception as exc:
            return {"reachable": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
        return {"reachable": True, "server": self.server_info, "tools": len(page.tools)}

    async def discover_tools(self) -> list[ToolInfo]:
        """The tools the server lists, with their own descriptions and schemas (the very text an agent would read)."""
        client = self._connected()
        tools: list[ToolInfo] = []
        cursor: str | None = None
        for _ in range(50):  # a server cannot keep a client paging forever
            page = await client.list_tools(cursor=cursor) if cursor else await client.list_tools()
            for t in page.tools:
                desc = t.description or ""
                tools.append(
                    ToolInfo(
                        name=t.name,
                        description=desc,
                        parameters=dict(t.input_schema or {}),
                        source=f"mcp:{self.server_info.get('name') or self.cfg.url or 'server'}",
                        side_effects=side_effects_of(t.name, desc, getattr(t, "annotations", None)),
                    )
                )
            cursor = getattr(page, "next_cursor", None)
            if not cursor:
                break
        return tools

    async def send(self, request: AgentRequest) -> AgentResponse:
        name, args = parse_tool_call(request.input)
        client = self._connected()
        t0 = time.perf_counter()
        try:
            result = await client.call_tool(name, args, read_timeout_seconds=self.cfg.timeout_seconds)
        except TimeoutError:
            return AgentResponse(
                error=f"timeout after {self.cfg.timeout_seconds:g}s",
                latency_ms=(time.perf_counter() - t0) * 1000,
                tool_calls=[ToolCall(name=name, arguments=args, status="error")],
            )
        except Exception as exc:
            code = getattr(exc, "code", None)
            latency = (time.perf_counter() - t0) * 1000
            if code is not None and hasattr(exc, "message"):  # the server answered with a protocol error
                message = str(getattr(exc, "message", ""))[:300]
                return AgentResponse(
                    output=message,
                    error=f"MCP error {code}: {message}",
                    latency_ms=latency,
                    tool_calls=[
                        ToolCall(name=name, arguments=args, result=message, status="error", latency_ms=latency)
                    ],
                    events=[AgentEvent(type="mcp_result", data={"tool": name, "is_error": True, "code": code})],
                )
            return AgentResponse(error=f"connection error: {type(exc).__name__}: {str(exc)[:120]}", latency_ms=latency)
        latency = (time.perf_counter() - t0) * 1000
        text = _clip(flatten_content(list(result.content or [])))
        is_error = bool(result.is_error)
        structured = result.structured_content
        return AgentResponse(
            output=text,
            latency_ms=latency,
            tool_calls=[
                ToolCall(
                    name=name,
                    arguments=args,
                    result=structured if structured is not None else text,
                    status="error" if is_error else "success",
                    latency_ms=latency,
                )
            ],
            events=[AgentEvent(type="mcp_result", data={"tool": name, "is_error": is_error})],
        )

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "transport": self.cfg.transport, "server": self.server_info}


ADAPTERS.register("mcp", McpAdapter, replace=True)
