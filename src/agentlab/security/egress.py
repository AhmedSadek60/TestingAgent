"""Evaluator-side egress policy.

Protects the evaluator host itself from SSRF-style misuse: a target URL, an OpenAPI
``servers`` entry or a redirect must never steer AgentLab to cloud metadata services,
and (optionally) private networks.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from agentlab.core.errors import PolicyBlocked

METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal", "metadata", "fd00:ec2::254", "100.100.100.200"}


class EgressPolicy:
    def __init__(
        self,
        *,
        block_metadata: bool = True,
        allow_private: bool = True,
        allowed_schemes: tuple[str, ...] = ("http", "https", "ws", "wss"),
    ) -> None:
        self.block_metadata = block_metadata
        self.allow_private = allow_private
        self.allowed_schemes = allowed_schemes

    def check(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in self.allowed_schemes:
            raise PolicyBlocked(f"scheme '{parsed.scheme}' is not allowed for evaluator egress")
        host = parsed.hostname or ""
        if not host:
            raise PolicyBlocked("URL has no host")
        if self.block_metadata and host.lower() in METADATA_HOSTS:
            raise PolicyBlocked(f"egress to metadata endpoint {host} is blocked")
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return  # unresolvable: the HTTP call will fail with a target error
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            if self.block_metadata and (str(addr) in METADATA_HOSTS or addr.is_link_local):
                raise PolicyBlocked(f"{host} resolves to link-local/metadata address {addr}")
            if not self.allow_private and (addr.is_private or addr.is_loopback):
                raise PolicyBlocked(f"{host} resolves to private address {addr}; private networks disallowed")
