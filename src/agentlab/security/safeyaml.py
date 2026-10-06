"""Reading YAML: the one place AgentLab turns YAML text into values.

``yaml.safe_load`` cannot run code, but an alias makes the loader *share* a structure instead of copying it, so a document of
a few hundred bytes can describe a value with billions of items once anything walks it ("billion laughs"). Reading it is
instant; the harm comes when a parser, a validator or a copy visits every item. This module counts how large the document
would be with every alias expanded *before* any value is built, and refuses it when that is far more than the text could hold.

An alias that points at the structure containing it (a cycle) is refused for the same reason: nothing can walk it.

Everything in AgentLab reads YAML through :func:`load`, whoever wrote the file; ``tests/unit/test_safeyaml.py`` fails when
a module calls ``yaml.safe_load`` directly.
"""

from __future__ import annotations

from typing import Any

import yaml

MAX_CHARS = 2_000_000
#: expanded values allowed per character of text. Plain YAML never has more values than characters, and aliases that
#: re-use a few definitions stay far below this; an alias bomb exceeds it after a handful of levels.
EXPANSION_FACTOR = 20
MIN_NODES = 10_000
MAX_NODES = 5_000_000


class YamlRefused(yaml.YAMLError):
    """The document is valid YAML that AgentLab will not read (too large, or aliases that expand without bound)."""


def _children(node: yaml.Node) -> list[yaml.Node]:
    if isinstance(node, yaml.SequenceNode):
        return list(node.value)
    if isinstance(node, yaml.MappingNode):
        return [part for pair in node.value for part in pair]
    return []


def expanded_size(root: yaml.Node, limit: int) -> int:
    """The number of values in ``root`` with every alias expanded, counted without expanding them.

    Raises :class:`YamlRefused` as soon as the count passes ``limit`` or an alias refers to its own container."""
    sizes: dict[int, int] = {}
    on_path: set[int] = set()
    stack: list[tuple[yaml.Node, bool]] = [(root, False)]
    while stack:
        node, finished = stack.pop()
        key = id(node)
        if finished:
            on_path.discard(key)
            sizes[key] = 1 + sum(sizes[id(child)] for child in _children(node))
            if sizes[key] > limit:
                raise YamlRefused(
                    f"the document's aliases expand to more than {limit:,} values (an alias bomb?), so it is not read"
                )
            continue
        if key in sizes:
            continue
        on_path.add(key)
        stack.append((node, True))
        for child in _children(node):
            if id(child) in on_path:
                raise YamlRefused("an alias refers to the structure that contains it, so the document is not read")
            if id(child) not in sizes:
                stack.append((child, False))
    return sizes[id(root)]


def load(text: str | bytes, *, max_chars: int = MAX_CHARS) -> Any:
    """``yaml.safe_load`` for any YAML: the same value for an ordinary document, and :class:`YamlRefused` (a
    ``yaml.YAMLError``, so the handlers that already catch YAML errors catch it) for one that is too large to read safely.

    Parsing happens once: the node graph is composed first (aliases share nodes there, so nothing is expanded yet), measured,
    and only then turned into values."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        raise YamlRefused(f"the YAML is {len(text):,} characters long; at most {max_chars:,} are read")
    loader = yaml.SafeLoader(text)
    try:
        node = loader.get_single_node()
        if node is None:
            return None
        expanded_size(node, min(MAX_NODES, max(MIN_NODES, EXPANSION_FACTOR * len(text))))
        return loader.construct_document(node)
    except RecursionError as exc:  # the parser follows nesting recursively; a document nested beyond that is not read
        raise YamlRefused("the YAML is nested too deeply to read") from exc
    finally:
        loader.dispose()
