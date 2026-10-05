"""Sandboxed rendering of declarative skill templates.

Third-party skills may only contain data and Jinja templates evaluated in a ``SandboxedEnvironment``
with unusual delimiters (``[[ ]]`` / ``[% %]``) so that AgentLab's own runtime placeholders such as
``{{canary:name}}`` pass through untouched. Templates cannot import modules, call dunder attributes or
reach the host; a single expression such as ``[[ item.name ]]`` keeps its native type (int, list...).
"""

from __future__ import annotations

import re
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import ImmutableSandboxedEnvironment

from agentlab.core.errors import UserError

MAX_RENDER_CHARS = 20_000
_SINGLE = re.compile(r"^\s*\[\[\s*(.+?)\s*\]\]\s*$", re.S)


def _env() -> ImmutableSandboxedEnvironment:
    env = ImmutableSandboxedEnvironment(
        block_start_string="[%",
        block_end_string="%]",
        variable_start_string="[[",
        variable_end_string="]]",
        comment_start_string="[#",
        comment_end_string="#]",
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=False,
    )
    env.filters["slug"] = lambda s: re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")
    env.filters["upper_id"] = lambda s: re.sub(r"[^A-Z0-9]+", "_", str(s).upper()).strip("_")
    env.filters["truncate_words"] = lambda s, n=12: " ".join(str(s).split()[: int(n)])
    env.filters["json"] = lambda s: __import__("json").dumps(s, ensure_ascii=False)
    return env


_ENV = _env()


class TemplateRenderer:
    def __init__(self, variables: dict[str, Any]) -> None:
        self.vars = variables

    def with_vars(self, **extra: Any) -> TemplateRenderer:
        return TemplateRenderer({**self.vars, **extra})

    def expression(self, expr: str) -> Any:
        try:
            return _ENV.compile_expression(expr, undefined_to_none=False)(**self.vars)
        except TemplateError as exc:
            raise UserError(f"template expression {expr!r} failed: {exc}") from exc

    def truthy(self, expr: str | None) -> bool:
        if not expr:
            return True
        return bool(self.expression(expr.strip().removeprefix("[[").removesuffix("]]").strip()))

    def render(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._render_str(value)
        if isinstance(value, dict):
            return {k: self.render(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.render(v) for v in value]
        return value

    def _render_str(self, text: str) -> Any:
        if "[[" not in text and "[%" not in text and "[#" not in text:
            return text
        m = _SINGLE.match(text)
        if m and "[[" not in m.group(1) and "[%" not in text:
            value = self.expression(m.group(1))
            return value if isinstance(value, (int, float, bool, list, dict)) or value is None else str(value)
        try:
            out = _ENV.from_string(text).render(**self.vars)
        except TemplateError as exc:
            raise UserError(f"template failed to render: {exc}") from exc
        if len(out) > MAX_RENDER_CHARS:
            raise UserError(f"rendered template is longer than {MAX_RENDER_CHARS} characters")
        return out
