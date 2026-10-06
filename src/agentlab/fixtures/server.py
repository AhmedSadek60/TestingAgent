"""Serving fixture apps: on an ephemeral port in a background thread (self-tests) or in the foreground (``agentlab
fixtures serve``). The implementation is shared with the instrumented test site (:mod:`agentlab.netserve`)."""

from agentlab.netserve import ThreadedServer, serve, serve_forever

__all__ = ["ThreadedServer", "serve", "serve_forever"]
