"""A request to the agent is sent on a connection of its own.

A server closes an idle keep-alive connection whenever it likes, and a request that is sent on a connection in the instant
it is closed fails with a ReadError and no reply. Such a failure cannot be repeated safely (the request may have arrived),
so AgentLab recorded it as a failure of the agent. Under load it turned up as correct fixture agents failing a test or two
in a run. Not keeping a connection to reuse leaves nothing to race with.
"""

from __future__ import annotations

import asyncio
import re

from agentlab.adapters.base import AdapterContext
from agentlab.adapters.http import AgentApiAdapter
from agentlab.core.config import AgentLabConfig
from agentlab.core.models import AgentRequest, ApiConfig, TargetSpec


async def test_every_request_uses_a_new_connection_even_when_the_server_would_keep_it() -> None:
    accepted = 0

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal accepted
        accepted += 1
        try:
            while True:  # an HTTP/1.1 server that offers to keep the connection open
                head = await reader.readuntil(b"\r\n\r\n")
                length = re.search(rb"(?i)content-length:\s*(\d+)", head)
                if length:
                    await reader.readexactly(int(length.group(1)))
                body = b'{"output": "hi"}'
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: keep-alive\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    spec = TargetSpec(name="agent", api=ApiConfig(url=f"http://127.0.0.1:{port}/chat"))
    made = AgentApiAdapter(spec, AdapterContext(config=AgentLabConfig()))
    await made.open()
    try:
        for _ in range(4):
            reply = await made.send(AgentRequest(input="hello", session_id="s1"))
            assert reply.error is None and reply.output == "hi"
    finally:
        await made.close()
        server.close()
        await server.wait_closed()
    assert accepted == 4, f"{accepted} connections were used for 4 requests: a connection was kept and reused"
