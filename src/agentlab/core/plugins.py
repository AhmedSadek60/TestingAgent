"""Plug-in registry (spec section 43).

Providers, agent adapters, assertion evaluators, artifact stores, browser engines,
document parsers, sandbox providers, report renderers and test generators are all
looked up by name through a :class:`Registry`. Third-party packages can contribute
plug-ins through Python entry points named ``agentlab.<kind>`` without any change
to orchestration code.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from importlib.metadata import entry_points
from typing import Generic, TypeVar

T = TypeVar("T")
log = logging.getLogger(__name__)


class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, T] = {}
        self._loaded_entry_points = False

    def register(self, name: str, item: T | None = None, *, replace: bool = False) -> Callable[[T], T] | T:
        """Register ``item`` under ``name``. Usable as a decorator when ``item`` is omitted."""

        def _do(obj: T) -> T:
            if name in self._items and not replace:
                raise ValueError(f"{self.kind} plug-in '{name}' is already registered")
            self._items[name] = obj
            return obj

        if item is None:
            return _do
        return _do(item)

    def unregister(self, name: str) -> None:
        """Remove ``name`` (a host application that unloads a plug-in, a test that cleans up). Unknown names are ignored."""
        self._items.pop(name, None)

    def _load_entry_points(self) -> None:
        if self._loaded_entry_points:
            return
        self._loaded_entry_points = True
        for ep in entry_points(group=f"agentlab.{self.kind}"):
            if ep.name in self._items:
                continue
            try:
                self._items[ep.name] = ep.load()
            except Exception as exc:  # a broken third-party plug-in must not break AgentLab
                log.warning("failed to load %s plug-in %s: %s", self.kind, ep.name, exc)

    def get(self, name: str) -> T:
        self._load_entry_points()
        try:
            return self._items[name]
        except KeyError as exc:
            known = ", ".join(sorted(self._items)) or "none"
            raise KeyError(f"unknown {self.kind} plug-in '{name}' (known: {known})") from exc

    def __contains__(self, name: object) -> bool:
        self._load_entry_points()
        return name in self._items

    def names(self) -> list[str]:
        self._load_entry_points()
        return sorted(self._items)

    def items(self) -> Iterator[tuple[str, T]]:
        self._load_entry_points()
        yield from sorted(self._items.items())
