"""A tiny MCP server over stdio that uses only the standard library, so it runs in a bare Python image.

It is the "untrusted repository" of the sandbox test: its ``where`` tool reports what the process can see, which is how
the test proves the server runs inside the sandbox and not on the evaluator host.
"""

import json
import os
import socket
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the text back",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "where",
        "description": "Report the environment this server runs in",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
]


def reply(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def reachable() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 80), timeout=2).close()
        return True
    except OSError:
        return False


def text(value, is_error=False):
    return {"content": [{"type": "text", "text": value}], "isError": is_error}


for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:
        continue
    if method == "initialize":
        reply(
            mid,
            {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stdio-fixture", "version": "0.1"},
            },
        )
    elif method == "tools/list":
        reply(mid, {"tools": TOOLS})
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name == "echo" and "text" in args:
            reply(mid, text("echo: " + str(args["text"])))
        elif name == "where":
            info = {
                "uid": os.getuid(),
                "in_container": os.path.exists("/.dockerenv"),
                "cwd": os.getcwd(),
                "network": reachable(),
            }
            reply(mid, text(json.dumps(info)))
        else:
            reply(mid, text("invalid call", True))
    elif method == "ping":
        reply(mid, {})
    else:
        reply(mid, error={"code": -32601, "message": "method not found: " + str(method)})
