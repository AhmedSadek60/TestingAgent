"""SkillRegistry: discovery, applicability matching and test generation for skills.

Skills come from three places: the built-in library shipped in the package, user directories listed in
``skill_dirs`` (trust ``local``) and installed plug-ins that expose the ``agentlab.skills`` entry point
(trust ``plugin``). A skill that failed validation, is a draft, or is untrusted (imported/generated) is
listed but never selected.
"""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Callable, Iterable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from agentlab.core.errors import UserError
from agentlab.skills.context import Draft, IdAllocator, SkillContext, SkillRun
from agentlab.skills.loader import TRUSTED_GENERATOR_PREFIX, discover_skill_dirs, load_skill_dir
from agentlab.skills.model import Skill, SkillMatch
from agentlab.skills.templating import TemplateRenderer

log = logging.getLogger(__name__)
BUILTIN_DIR = Path(__file__).parent / "library"
Generator = Callable[[SkillRun], None]


def noisy_or(scores: Iterable[float]) -> float:
    p = 1.0
    for s in scores:
        p *= 1 - min(0.99, max(0.0, s))
    return round(min(0.99, 1 - p), 3)


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self.problems: list[str] = []

    # ------------------------------------------------------------------ loading
    @classmethod
    def default(cls, skill_dirs: Iterable[str | Path] = (), *, load_plugins: bool = True) -> SkillRegistry:
        reg = cls()
        reg.load_dir(BUILTIN_DIR, trust="builtin")
        for d in skill_dirs:
            reg.load_dir(Path(d), trust="local")
        if load_plugins:
            reg.load_entry_points()
        return reg

    def add(self, skill: Skill, *, replace: bool = False) -> None:
        if skill.problems:
            self.problems += [f"{skill.manifest.name or skill.source}: {p}" for p in skill.problems]
        existing = self._skills.get(skill.name)
        if existing and not replace and existing.manifest.trust == "builtin":
            # a local skill never silently shadows a built-in one
            self.problems.append(f"skill '{skill.name}' from {skill.source} ignored: a built-in skill has that name")
            return
        self._skills[skill.name] = skill

    def load_dir(self, root: Path, *, trust: str = "local") -> list[Skill]:
        out: list[Skill] = []
        if not Path(root).exists():
            if trust != "builtin":
                self.problems.append(f"skill directory not found: {root}")
            return out
        for d in discover_skill_dirs(Path(root)):
            skill = load_skill_dir(d, trust=trust)
            self.add(skill)
            out.append(skill)
        return out

    def load_entry_points(self) -> None:
        for ep in entry_points(group="agentlab.skills"):
            try:
                target = ep.load()
                paths = target() if callable(target) else target
                for p in [paths] if isinstance(paths, (str, Path)) else list(paths):
                    self.load_dir(Path(p), trust="plugin")
            except Exception as exc:  # a broken plug-in must not break AgentLab
                self.problems.append(f"skill plug-in '{ep.name}' failed to load: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ access
    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise UserError(f"unknown skill '{name}' (known: {', '.join(sorted(self._skills))})") from exc

    def __contains__(self, name: object) -> bool:
        return name in self._skills

    def names(self) -> list[str]:
        return sorted(self._skills)

    def all(self, *, include_drafts: bool = True) -> list[Skill]:
        return [s for _, s in sorted(self._skills.items()) if include_drafts or not s.draft]

    # ------------------------------------------------------------------ matching
    def match(self, skill: Skill, ctx: SkillContext) -> SkillMatch:
        m = skill.manifest
        base = SkillMatch(
            skill=m.name,
            version=m.version,
            selected=False,
            kind=m.kind,
            taxonomy=m.taxonomy,
            trust=m.trust,
        )
        if skill.problems:
            base.skipped_reason = "invalid skill: " + "; ".join(skill.problems[:2])
            return base
        if m.status == "deprecated":
            base.skipped_reason = "deprecated"
            return base
        if skill.draft:
            base.skipped_reason = f"{m.trust} skill is an untrusted draft until a human promotes it"
            return base
        a = m.applicability
        scores: list[float] = []
        reasons: list[str] = []
        if a.always:
            scores.append(0.5)
            reasons.append("applies to every target")
        for t in a.types:
            c = ctx.type_confidence(t)
            if c >= a.min_confidence:
                scores.append(c)
                ev = next((s.evidence[0].detail for s in ctx.profile.types if s.type.value == t and s.evidence), "")
                reasons.append(f"target classified as '{t}' (confidence {c:.2f}{'; ' + ev if ev else ''})")
        for cap in a.capabilities:
            if ctx.capability(cap):
                scores.append(0.8)
                reasons.append(f"capability '{cap}' detected")
        for iface in a.interfaces:
            if iface in ctx.interfaces:
                scores.append(0.6)
                reasons.append(f"target exposes the '{iface}' interface")
        if a.tool_patterns:
            hits = [t.name for t in ctx.tools if any(re.search(p, t.name, re.I) for p in a.tool_patterns)]
            if hits:
                scores.append(0.7)
                reasons.append(f"tool(s) {hits[:4]} match this skill's patterns")
        if a.expression:
            try:
                if TemplateRenderer(_view(ctx)).truthy(a.expression):
                    scores.append(0.6)
                    reasons.append(f"condition `{a.expression}` holds")
            except UserError as exc:
                base.skipped_reason = f"applicability expression failed: {exc}"
                return base
        for t in a.exclude_types:
            if ctx.has_type(t, a.min_confidence):
                base.skipped_reason = f"excluded because the target is also classified as '{t}'"
                return base
        if a.needs_documents and not ctx.documents:
            base.skipped_reason = "no documents were supplied"
            return base
        if a.needs_repository and ctx.repo is None:
            base.skipped_reason = "no repository was analysed"
            return base
        if a.needs_tools and not ctx.tools:
            base.skipped_reason = "no tools were discovered"
            return base
        if not scores:
            base.skipped_reason = "not applicable: " + (
                _inapplicable_reason(m.name, a) or "no matching signal in the profile"
            )
            return base
        base.selected = True
        base.score = noisy_or(scores)
        base.reasons = reasons
        return base

    def select(
        self, ctx: SkillContext, *, include: Iterable[str] | None = None, exclude: Iterable[str] | None = None
    ) -> list[tuple[Skill, SkillMatch]]:
        """Match every skill, honour include/exclude lists, then add dependencies of selected skills."""
        inc, exc = set(include or []), set(exclude or [])
        for name in inc | exc:
            if name not in self._skills:
                raise UserError(f"unknown skill '{name}' (known: {', '.join(sorted(self._skills))})")
        results: dict[str, SkillMatch] = {}
        for skill in self.all():
            mt = self.match(skill, ctx)
            if exc and skill.name in exc and mt.selected:
                mt.selected, mt.skipped_reason = False, "excluded by the user"
            if inc and skill.name not in inc and mt.selected:
                mt.selected, mt.skipped_reason = False, "not in the requested skill list"
            if (
                inc
                and skill.name in inc
                and not mt.selected
                and mt.skipped_reason
                and mt.skipped_reason.startswith("not applicable")
            ):
                # the user explicitly asked for it: run it, but say it was not detected as applicable
                mt.selected = True
                mt.score = 0.3
                mt.reasons = ["explicitly requested by the user (not detected as applicable from the profile)"]
                mt.skipped_reason = None
            results[skill.name] = mt
        changed = True
        while changed:  # dependencies of selected skills
            changed = False
            for name, mt in list(results.items()):
                if not mt.selected:
                    continue
                for dep in self._skills[name].manifest.dependencies:
                    d = results.get(dep)
                    if d and not d.selected and self._skills[dep].usable and dep not in exc:
                        d.selected, d.score = True, 0.3
                        d.reasons = [f"dependency of '{name}'"]
                        d.skipped_reason = None
                        changed = True
        return [(self._skills[n], results[n]) for n in sorted(results)]

    # ------------------------------------------------------------------ generation
    def generate(self, skill: Skill, ctx: SkillContext, ids: IdAllocator, wave: int = 1) -> SkillRun:
        """Run a skill's declarative templates and (trusted) generator against the context."""
        run = SkillRun(skill.manifest, ctx, ids, wave)
        if skill.manifest.kind != "tests":
            return run
        if skill.manifest.generator:
            fn = resolve_generator(skill.manifest.generator, skill.manifest.trust)
            fn(run)
        if skill.manifest.templates:
            render_templates(skill, run)
        return run


def resolve_generator(path: str, trust: str) -> Generator:
    module, _, func = path.partition(":")
    if trust == "builtin" and not module.startswith(TRUSTED_GENERATOR_PREFIX):
        raise UserError(f"generator '{path}' is not under {TRUSTED_GENERATOR_PREFIX}*")
    if trust not in {"builtin", "plugin"}:
        raise UserError(f"generators are not allowed for {trust} skills")
    fn = getattr(importlib.import_module(module), func, None)
    if not callable(fn):
        raise UserError(f"generator '{path}' was not found")
    return fn  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------------- declarative templates
_FOREACH = {
    "tools": lambda c: c.tools,
    "facts": lambda c: c.facts,
    "documents": lambda c: c.documents,
    "requirements": lambda c: c.requirements,
    "conflicts": lambda c: c.conflicts,
    "side_effect_tools": lambda c: c.side_effect_tools,
    "outbound_tools": lambda c: c.outbound_tools,
    "destructive_tools": lambda c: c.destructive_tools,
    "read_tools": lambda c: c.read_tools,
}
_KEY_MAP = {
    "severity_on_failure": "severity",
    "risk_level": "risk",
    "required_interfaces": "interfaces",
    "required_credentials": "credentials",
    "forbidden_behavior": "forbidden",
    "applicable_agent_types": "applicable",
    "cleanup_strategy": "cleanup",
    "evaluation_metrics": "metrics",
}
_ADD_KEYS = {
    "input",
    "turns",
    "assertions",
    "judge",
    "expected_behavior",
    "expected_output",
    "expected_tool_calls",
    "forbidden",
    "severity",
    "risk",
    "metrics",
    "tags",
    "subcategory",
    "interfaces",
    "credentials",
    "context",
    "repetitions",
    "timeout",
    "max_steps",
    "max_cost",
    "max_tokens",
    "preconditions",
    "applicable",
    "isolation_key",
    "score_category",
    "category",
    "cleanup",
    "evidence_requirements",
    "browser_steps",
}


def _view(ctx: SkillContext) -> dict[str, Any]:
    """The only data templates may see: no config, no target credentials, no secrets."""
    return {
        "profile": ctx.profile,
        "tools": ctx.tools,
        "facts": ctx.facts,
        "documents": ctx.documents,
        "requirements": ctx.requirements,
        "conflicts": ctx.conflicts,
        "side_effect_tools": ctx.side_effect_tools,
        "outbound_tools": ctx.outbound_tools,
        "destructive_tools": ctx.destructive_tools,
        "read_tools": ctx.read_tools,
        "target_name": ctx.profile.target_name,
        "interfaces": ctx.interfaces,
        "intensity": ctx.intensity,
        "judge_available": ctx.judge_available,
        "has_documents": bool(ctx.documents),
        "authenticated": ctx.authenticated,
    }


def _inapplicable_reason(name: str, a: Any) -> str:
    want = [*a.types, *a.capabilities, *a.interfaces]
    return f"none of {want} was detected" if want else ""


def render_templates(skill: Skill, run: SkillRun) -> None:
    base = TemplateRenderer(_view(run.ctx))
    for tpl in skill.manifest.templates:
        items: list[Any] = [None]
        if tpl.foreach:
            items = list(_FOREACH[tpl.foreach](run.ctx))[: max(1, tpl.limit)]
            if not items:
                continue
        for idx, item in enumerate(items):
            r = base.with_vars(item=item, index=idx)
            try:
                if not r.truthy(tpl.when):
                    continue
                spec = r.render(dict(tpl.test))
                reasons = [str(x) for x in r.render(list(tpl.reasons))]
            except UserError as exc:
                run.note(f"template '{tpl.id}' skipped: {exc}")
                continue
            topic = tpl.id
            if tpl.foreach and item is not None:
                label = getattr(item, "name", None) or getattr(item, "subject", None) or str(idx + 1)
                topic = f"{tpl.id}-{re.sub(r'[^A-Za-z0-9]+', '-', str(label)).strip('-')[:14]}"
            kwargs: dict[str, Any] = {}
            for key, value in spec.items():
                key = _KEY_MAP.get(key, key)
                if key in {"name", "objective"}:
                    continue
                if key in _ADD_KEYS:
                    kwargs[key] = value
            _coerce(kwargs)
            run.add(topic, str(spec.get("name", tpl.id)), str(spec.get("objective", "")), why=reasons, **kwargs)


def _coerce(kwargs: dict[str, Any]) -> None:
    from agentlab.core.enums import RiskClass, Severity

    if isinstance(kwargs.get("severity"), str):
        kwargs["severity"] = Severity(kwargs["severity"])
    if isinstance(kwargs.get("risk"), str):
        kwargs["risk"] = RiskClass(kwargs["risk"])
    for k in ("timeout", "max_cost"):
        if k in kwargs and kwargs[k] is not None:
            kwargs[k] = float(kwargs[k])
    for k in ("max_steps", "max_tokens", "repetitions"):
        if k in kwargs and kwargs[k] is not None:
            kwargs[k] = int(kwargs[k])


__all__ = ["BUILTIN_DIR", "Draft", "SkillRegistry", "noisy_or", "render_templates", "resolve_generator"]
