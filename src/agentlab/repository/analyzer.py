"""RepositoryAnalyzer: static, language-agnostic analysis of an *ingested* repository.

Nothing is executed. Signals come from dependency manifests, configuration files, MCP/OpenAPI
definitions, agent-guidance files, and regular-expression / ``ast`` scans of source files. Every
finding carries provenance (``path:line``) so test plans and reports can cite *why* a conclusion
was drawn. All repository text is treated as untrusted data: guidance files such as AGENTS.md or
CLAUDE.md are recorded and scanned for injection indicators but never obeyed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from agentlab.core.models import ArchitectureGraph
from agentlab.repository import signals as S
from agentlab.repository.ingest import IngestedRepo
from agentlab.repository.languages import (
    BINARY_EXT,
    CONFIG_EXT,
    DOC_EXT,
    SKIP_DIRS,
    is_text_candidate,
    language_of,
)
from agentlab.repository.models import (
    AgentDefinition,
    ApiRoute,
    DependencyInfo,
    Located,
    McpServerConfig,
    PromptItem,
    RepositoryAnalysis,
    Signal,
    ToolDefinition,
)
from agentlab.security.redactor import SecretRedactor
from agentlab.security.untrusted import injection_indicators

MAX_READ = 400_000
MAX_FILES_SCANNED = 4000
GUIDANCE = {"agents.md", "claude.md", "gemini.md", ".cursorrules", ".windsurfrules", "copilot-instructions.md"}
ENTRY_NAMES = {
    "main.py",
    "app.py",
    "server.py",
    "run.py",
    "agent.py",
    "__main__.py",
    "manage.py",
    "cli.py",
    "index.js",
    "index.ts",
    "server.js",
    "server.ts",
    "app.js",
    "app.ts",
    "main.ts",
    "main.js",
    "main.go",
    "main.rs",
    "Main.java",
    "Application.java",
    "Program.cs",
    "main.kt",
    "index.php",
    "main.rb",
    "app.rb",
    "main.swift",
}
DEPLOY_FILES = {
    "dockerfile": "Docker",
    "docker-compose.yml": "docker-compose",
    "docker-compose.yaml": "docker-compose",
    "compose.yaml": "docker-compose",
    "compose.yml": "docker-compose",
    "procfile": "Procfile (Heroku-style)",
    "vercel.json": "Vercel",
    "fly.toml": "Fly.io",
    "serverless.yml": "Serverless Framework",
    "netlify.toml": "Netlify",
    "chart.yaml": "Helm",
    "skaffold.yaml": "Skaffold",
    "app.yaml": "App Engine",
    "render.yaml": "Render",
}
TEST_PATTERNS = (
    re.compile(r"(^|/)(tests?|__tests__|spec|specs)/"),
    re.compile(
        r"(_test\.go|_spec\.rb|Test\.java|Tests\.cs|"
        r"\.test\.[jt]sx?|\.spec\.[jt]sx?|test_[\w]+\.py|_test\.py|Test\.kt|Tests\.swift)$"
    ),
)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _py_type_to_json(annotation: ast.expr | None) -> str:
    if annotation is None:
        return "string"
    src = ast.unparse(annotation).lower()
    for key, val in (
        ("int", "integer"),
        ("float", "number"),
        ("bool", "boolean"),
        ("list", "array"),
        ("dict", "object"),
    ):
        if key in src:
            return val
    return "string"


class RepositoryAnalyzer:
    def __init__(self, extra_secret_patterns: list[str] | None = None) -> None:
        self.redactor = SecretRedactor(extra_patterns=extra_secret_patterns or [])

    # ------------------------------------------------------------------ public
    def analyze(self, repo: IngestedRepo) -> RepositoryAnalysis:
        root = repo.path
        a = RepositoryAnalysis(
            root_name=repo.name,
            commit=repo.commit,
            ref=repo.ref,
            url=repo.url,
            file_count=repo.file_count,
            total_bytes=repo.total_bytes,
            skipped=dict(repo.skipped),
            warnings=list(repo.warnings),
        )
        files = self._list_files(root)
        lang_bytes: Counter[str] = Counter()
        cap_hits: dict[str, list[Located]] = {}
        provider_set: set[str] = set()
        model_set: set[str] = set()
        env_keys: set[str] = set()
        db_set: set[str] = set()
        vec_set: set[str] = set()
        mem_set: set[str] = set()
        browser_set: set[str] = set()
        deploy: set[str] = set()
        frameworks: set[str] = set()
        seen_tools: set[tuple[str, str]] = set()
        scanned = 0
        for rel in files:
            path = root / rel
            lang = language_of(rel)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if lang:
                lang_bytes[lang] += size
            base = PurePosixPath(rel).name.lower()
            if base in DEPLOY_FILES:
                deploy.add(DEPLOY_FILES[base])
            if rel.startswith(".github/workflows/"):
                deploy.add("GitHub Actions")
            if base == ".gitlab-ci.yml":
                deploy.add("GitLab CI")
            if any(p.search(rel) for p in TEST_PATTERNS) and len(a.tests) < 200:
                a.tests.append(Located(path=rel))
            if base in GUIDANCE or rel.startswith((".cursor/rules/", ".claude/")) or base == "skill.md":
                a.guidance_files.append(Located(path=rel))
            if base == "skill.md" or "/skills/" in f"/{rel}" and base.endswith(".md"):
                a.skills.append(Located(path=rel))
            if not is_text_candidate(rel) or size > MAX_READ * 4:
                continue
            if scanned >= MAX_FILES_SCANNED:
                a.warnings.append(f"analysis stopped after {MAX_FILES_SCANNED} files; repository is larger")
                break
            text = self._read(path)
            if text is None:
                continue
            scanned += 1
            self._manifest(rel, base, text, a)
            self._secrets(rel, text, a)
            self._injection(rel, text, a)
            if base in ENTRY_NAMES and len(a.entry_points) < 20:
                a.entry_points.append(Located(path=rel))
            if base.startswith(".env") or base.endswith(".env.example") or base == "env.example":
                for m in S.ENV_KEY.finditer(text):
                    env_keys.add(m.group(1))
            ext = PurePosixPath(rel).suffix.lower()
            if base in {
                "openapi.json",
                "openapi.yaml",
                "openapi.yml",
                "swagger.json",
                "swagger.yaml",
                "swagger.yml",
            } or (
                ext in {".json", ".yaml", ".yml"}
                and re.search(r'^\s*["\']?(openapi|swagger)["\']?\s*:', text[:2000], re.M)
            ):
                a.openapi_specs.append(Located(path=rel))
            if base in {".mcp.json", "mcp.json", "claude_desktop_config.json"} or rel.endswith(
                (".cursor/mcp.json", ".vscode/mcp.json")
            ):
                self._mcp_config(rel, text, a)
            if ext in CONFIG_EXT or ext in {".json", ".yaml", ".yml"}:
                self._tools_from_structured(rel, text, a, seen_tools)
            if lang or ext in DOC_EXT or ext in CONFIG_EXT:
                self._model_names(text, model_set)
            if lang:
                self._code(rel, lang, text, a, cap_hits, seen_tools)

        a.languages = dict(lang_bytes.most_common())
        self._dependencies_to_facts(a, frameworks, provider_set, db_set, vec_set, mem_set, browser_set, cap_hits)
        for k in env_keys:
            if k in S.PROVIDER_ENV:
                provider_set.add(S.PROVIDER_ENV[k])
        a.env_requirements = sorted(env_keys)
        a.frameworks = sorted(frameworks)
        a.model_providers = sorted(provider_set)
        a.models = sorted(model_set)[:30]
        a.databases = sorted(db_set)
        a.vector_databases = sorted(vec_set)
        a.memory_systems = sorted(mem_set)
        a.browser_frameworks = sorted(browser_set)
        a.deployment = sorted(deploy)
        a.capabilities = self._capabilities(a, cap_hits)
        self._build_graph(a)
        return a

    # ------------------------------------------------------------------ files
    @staticmethod
    def _list_files(root: Path) -> list[str]:
        out: list[str] = []
        for p in sorted(root.rglob("*")):
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part in SKIP_DIRS for part in rel.parts[:-1]):
                continue
            out.append(rel.as_posix())
        return out

    @staticmethod
    def _read(path: Path) -> str | None:
        try:
            raw = path.read_bytes()[:MAX_READ]
        except OSError:
            return None
        if b"\x00" in raw[:2048]:
            return None
        return raw.decode("utf-8", "replace")

    # ------------------------------------------------------------------ manifests
    def _manifest(self, rel: str, base: str, text: str, a: RepositoryAnalysis) -> None:
        def dep(name: str, version: str | None, eco: str, dev: bool = False) -> None:
            a.dependencies.append(
                DependencyInfo(
                    name=name,
                    version=version,
                    ecosystem=eco,
                    source=rel,
                    dev=dev or bool(re.search(r"(dev|test)", base)),
                )
            )

        try:
            if base.startswith("requirements") and base.endswith(".txt"):
                for line in text.splitlines():
                    line = line.split("#")[0].strip()
                    m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)\s*(?:([=<>~!]=?[^;\s]*))?", line)
                    if m and not line.startswith(("-", "git+", "http")):
                        dep(re.sub(r"\[.*\]", "", m.group(1)), m.group(2), "pypi")
            elif base == "pyproject.toml":
                data = tomllib.loads(text)
                proj = data.get("project", {})
                for d in proj.get("dependencies", []):
                    m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", d)
                    if m:
                        dep(m.group(1), m.group(2) or None, "pypi")
                for gname, group in proj.get("optional-dependencies", {}).items():
                    for d in group:
                        m = re.match(r"^([A-Za-z0-9_.\-]+)", d)
                        if m:
                            dep(m.group(1), None, "pypi", dev=gname in {"dev", "test", "tests", "lint", "docs"})
                for k, v in data.get("tool", {}).get("poetry", {}).get("dependencies", {}).items():
                    if k.lower() != "python":
                        dep(k, v if isinstance(v, str) else None, "pypi")
                for entry, target in proj.get("scripts", {}).items():
                    a.entry_points.append(Located(path=f"{rel}#scripts.{entry}={target}"))
            elif base == "package.json":
                data = json.loads(text)
                for sect in ("dependencies", "devDependencies", "peerDependencies"):
                    for k, v in (data.get(sect) or {}).items():
                        dep(k, str(v), "npm", dev=sect == "devDependencies")
                if data.get("main"):
                    a.entry_points.append(Located(path=f"{rel}#main={data['main']}"))
                for k in ("start", "dev", "serve"):
                    if data.get("scripts", {}).get(k):
                        a.entry_points.append(Located(path=f"{rel}#scripts.{k}={data['scripts'][k]}"))
            elif base == "pom.xml":
                for m in re.finditer(r"<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>", text):
                    dep(m.group(2), None, "maven")
                    dep(m.group(1), None, "maven")
            elif base in {"build.gradle", "build.gradle.kts"}:
                for m in re.finditer(
                    r"""(?:implementation|api|compile|runtimeOnly)\s*\(?\s*["']([^:"']+):([^:"']+)(?::([^"']+))?["']""",
                    text,
                ):
                    dep(m.group(2), m.group(3), "maven")
                    dep(m.group(1), None, "maven")
            elif base.endswith(".csproj"):
                for m in re.finditer(r'PackageReference\s+Include="([^"]+)"(?:\s+Version="([^"]+)")?', text):
                    dep(m.group(1), m.group(2), "nuget")
            elif base == "go.mod":
                for m in re.finditer(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-]+/[\w./\-]+)\s+(v[\w.\-+]+)", text, re.M):
                    dep(m.group(1), m.group(2), "go")
            elif base == "cargo.toml":
                data = tomllib.loads(text)
                for sect in ("dependencies", "dev-dependencies"):
                    for k, v in data.get(sect, {}).items():
                        dep(
                            k,
                            v if isinstance(v, str) else (v.get("version") if isinstance(v, dict) else None),
                            "cargo",
                            dev=sect == "dev-dependencies",
                        )
            elif base == "composer.json":
                data = json.loads(text)
                for sect in ("require", "require-dev"):
                    for k, v in data.get(sect, {}).items():
                        dep(k, str(v), "composer", dev=sect == "require-dev")
            elif base == "gemfile":
                for m in re.finditer(r"""^\s*gem\s+["']([^"']+)["'](?:\s*,\s*["']([^"']+)["'])?""", text, re.M):
                    dep(m.group(1), m.group(2), "rubygems")
            elif base == "package.swift":
                for m in re.finditer(r'\.package\(\s*url:\s*"([^"]+)"', text):
                    dep(m.group(1).rstrip("/").split("/")[-1].removesuffix(".git"), None, "swiftpm")
            elif base == "pipfile":
                for m in re.finditer(r'^([A-Za-z0-9_.\-]+)\s*=\s*["\'{]', text, re.M):
                    dep(m.group(1), None, "pypi")
        except (ValueError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
            a.warnings.append(f"could not parse {rel}: {type(exc).__name__}")

    def _dependencies_to_facts(
        self,
        a: RepositoryAnalysis,
        frameworks: set[str],
        providers: set[str],
        dbs: set[str],
        vecs: set[str],
        mems: set[str],
        browsers: set[str],
        cap_hits: dict[str, list[Located]],
    ) -> None:
        for d in a.dependencies:
            if d.dev:
                continue
            key = S.normalise_dep(d.name)
            hit = (
                S.DEPENDENCIES.get(key)
                or S.DEPENDENCIES.get(key.replace("-", "_"))
                or S.DEPENDENCIES.get(d.name.lower())
            )
            if not hit:
                for k, v in S.DEPENDENCIES.items():
                    if "/" in k and d.name.lower().endswith(k):
                        hit = v
                        break
            if not hit:
                continue
            kind, label, cap = hit
            {
                "framework": frameworks,
                "provider": providers,
                "database": dbs,
                "vectordb": vecs,
                "memory": mems,
                "browser": browsers,
            }.get(kind, set()).add(label)
            if kind == "vectordb":
                vecs.add(label)
            if cap:
                loc = Located(path=d.source)
                cap_hits.setdefault(cap, []).append(loc)
                a.signals.append(
                    Signal(kind="dependency", detail=f"{d.name} -> {label} ({cap})", location=loc, weight=0.7)
                )
            elif kind == "framework":
                a.signals.append(
                    Signal(
                        kind="dependency", detail=f"{d.name} -> {label}", location=Located(path=d.source), weight=0.5
                    )
                )

    # ------------------------------------------------------------------ source scanning
    def _code(
        self,
        rel: str,
        lang: str,
        text: str,
        a: RepositoryAnalysis,
        cap_hits: dict[str, list[Located]],
        seen_tools: set[tuple[str, str]],
    ) -> None:
        for kind, rx, cap, detail in S.CODE_SIGNALS:
            m = rx.search(text)
            if m and cap and (rel.rsplit("/", 1)[-1].lower() not in {"readme.md"}):
                loc = Located(path=rel, line=_line_of(text, m.start()))
                hits = cap_hits.setdefault(cap, [])
                if len(hits) < 12:
                    hits.append(loc)
                if len([s for s in a.signals if s.kind == kind]) < 6:
                    a.signals.append(
                        Signal(kind=kind, detail=f"{detail}: '{m.group(0)[:40]}'", location=loc, weight=0.4)
                    )
        if lang == "Python":
            self._python_tools(rel, text, a, seen_tools)
        for framework, rx, _lang in S.TOOL_PATTERNS:
            if lang == "Python" and framework in {"LangChain @tool", "MCP (FastMCP) @tool", "function_tool"}:
                continue  # handled precisely by ast
            for m in rx.finditer(text):
                name = (m.groupdict().get("name2") or m.groupdict().get("name") or "").strip()
                if not name or (framework, name) in seen_tools or name in {"tool", "function", "name"}:
                    continue
                desc = re.sub(r"\s+", " ", (m.groupdict().get("desc") or "")).strip()
                seen_tools.add((framework, name))
                a.tools.append(
                    ToolDefinition(
                        name=name,
                        description=desc[:300],
                        framework=framework,
                        location=Located(path=rel, line=_line_of(text, m.start())),
                        side_effects=S.infer_side_effects(name, desc),
                    )
                )
        for framework, rx in S.AGENT_DEF_PATTERNS:
            for m in rx.finditer(text):
                nm = m.groupdict().get("name") or m.groupdict().get("role") or "agent"
                if len(a.agent_definitions) < 60 and not any(
                    d.name == nm and d.framework == framework for d in a.agent_definitions
                ):
                    a.agent_definitions.append(
                        AgentDefinition(
                            name=nm,
                            framework=framework,
                            location=Located(path=rel, line=_line_of(text, m.start())),
                            role=m.groupdict().get("role"),
                        )
                    )
        for framework, rx in S.ROUTE_PATTERNS:
            for m in rx.finditer(text):
                line = _line_of(text, m.start())
                if len(a.apis) < 150 and not any(r.location.path == rel and r.location.line == line for r in a.apis):
                    a.apis.append(
                        ApiRoute(
                            method=m.group("method").upper(),
                            path=m.group("path"),
                            framework=framework,
                            location=Located(path=rel, line=line),
                        )
                    )
        self._prompts(rel, text, a)

    def _python_tools(self, rel: str, text: str, a: RepositoryAnalysis, seen: set[tuple[str, str]]) -> None:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return
        deco_names = {"tool", "function_tool", "kernel_function", "mcp_tool"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            hit = None
            for d in node.decorator_list:
                target = d.func if isinstance(d, ast.Call) else d
                nm = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                if nm in deco_names:
                    hit = nm
                    kw = {k.arg: k.value for k in d.keywords} if isinstance(d, ast.Call) else {}
                    break
            if not hit:
                continue
            if (hit, node.name) in seen:
                continue
            seen.add((hit, node.name))
            doc = ast.get_docstring(node) or ""
            props: dict[str, Any] = {}
            required = []
            args = node.args
            defaults = [None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
            for arg, default in zip(args.args, defaults, strict=False):
                if arg.arg in {"self", "cls"}:
                    continue
                props[arg.arg] = {"type": _py_type_to_json(arg.annotation)}
                if default is None:
                    required.append(arg.arg)
            confirm = any(p in {"confirm", "confirmed", "approved", "user_confirmed"} for p in props)
            fw = (
                "MCP (FastMCP)"
                if any(
                    isinstance(d, ast.Call | ast.Attribute)
                    and "tool" in ast.dump(d)[:80]
                    and "attr='tool'" in ast.dump(d)[:200]
                    for d in node.decorator_list
                )
                else "Python @tool"
            )
            name = node.name
            description = doc.split("\n\n")[0].replace("\n", " ").strip()
            if kw.get("name") is not None and isinstance(kw["name"], ast.Constant):
                name = str(kw["name"].value)
            if kw.get("description") is not None and isinstance(kw["description"], ast.Constant):
                description = str(kw["description"].value)
            a.tools.append(
                ToolDefinition(
                    name=name,
                    description=description[:300],
                    framework=fw,
                    location=Located(path=rel, line=node.lineno),
                    parameters={"type": "object", "properties": props, "required": required},
                    side_effects=S.infer_side_effects(name, description),
                    requires_confirmation=True if confirm else None,
                )
            )

    def _prompts(self, rel: str, text: str, a: RepositoryAnalysis) -> None:
        for m in S.PROMPT_ASSIGN.finditer(text):
            body = m.group("body").strip()
            if len(body) < 20 or "{" in body[:2] and "}" in body[-2:] and "\n" not in body:
                continue
            if len(a.prompts) >= 40:
                return
            a.prompts.append(
                PromptItem(
                    kind="system_prompt" if "system" in m.group("name").lower() else "instructions",
                    excerpt=body[:400],
                    sha256=hashlib.sha256(body.encode()).hexdigest()[:16],
                    chars=len(body),
                    location=Located(path=rel, line=_line_of(text, m.start())),
                    injection_indicators=injection_indicators(body),
                )
            )

    def _tools_from_structured(self, rel: str, text: str, a: RepositoryAnalysis, seen: set[tuple[str, str]]) -> None:
        try:
            data = json.loads(text) if rel.endswith(".json") else yaml.safe_load(text)
        except Exception:
            return
        found = 0

        def walk(node: Any, depth: int = 0) -> None:
            nonlocal found
            if depth > 6 or found > 60:
                return
            if isinstance(node, dict):
                schema = node.get("parameters") or node.get("input_schema") or node.get("inputSchema")
                nm = node.get("name")
                if isinstance(node.get("function"), dict) and node.get("type") == "function":
                    node = node["function"]
                    schema, nm = node.get("parameters"), node.get("name")
                if (
                    isinstance(nm, str)
                    and isinstance(schema, dict)
                    and (node.get("description") or schema.get("properties"))
                    and ("config", nm) not in seen
                ):
                    seen.add(("config", nm))
                    desc = str(node.get("description") or "")
                    a.tools.append(
                        ToolDefinition(
                            name=nm,
                            description=desc[:300],
                            parameters=schema,
                            framework="config/JSON schema",
                            location=Located(path=rel),
                            side_effects=S.infer_side_effects(nm, desc),
                        )
                    )
                    found += 1
                for v in node.values():
                    walk(v, depth + 1)
            elif isinstance(node, list):
                for v in node[:100]:
                    walk(v, depth + 1)

        walk(data)

    def _mcp_config(self, rel: str, text: str, a: RepositoryAnalysis) -> None:
        try:
            data = json.loads(text)
        except ValueError:
            return
        servers = data.get("mcpServers") or data.get("servers") or {}
        if isinstance(servers, dict):
            for name, cfg in servers.items():
                if not isinstance(cfg, dict):
                    continue
                transport = cfg.get("type") or (
                    "stdio" if cfg.get("command") else "http" if cfg.get("url") else "unknown"
                )
                a.mcp.append(
                    McpServerConfig(
                        name=name,
                        transport=str(transport),
                        command=cfg.get("command"),
                        url=cfg.get("url"),
                        location=Located(path=rel),
                        env_keys=sorted((cfg.get("env") or {}).keys()),
                    )
                )

    @staticmethod
    def _model_names(text: str, models: set[str]) -> None:
        for m in S.MODEL_NAME.finditer(text[:60_000]):
            name = m.group(1).lower().rstrip(".-")
            if len(name) > 3 and not name.endswith("-api"):
                models.add(name)

    @staticmethod
    def _injection(rel: str, text: str, a: RepositoryAnalysis) -> None:
        """Instruction-like text in repository files is *recorded as data*, never followed."""
        if rel.endswith(".lock") or sum(1 for s in a.signals if s.kind == "injection_indicator") >= 20:
            return
        found = injection_indicators(text[:60_000])
        if found:
            m = re.search(found[0], text[:60_000], re.I)
            a.signals.append(
                Signal(
                    kind="injection_indicator",
                    detail=f"instruction-like text matching /{found[0][:50]}/",
                    location=Located(path=rel, line=_line_of(text, m.start()) if m else None),
                    weight=0.9,
                )
            )
            a.warnings.append(
                f"{rel} contains instruction-like text aimed at AI assistants; it is treated as untrusted data and never obeyed"
            )

    def _secrets(self, rel: str, text: str, a: RepositoryAnalysis) -> None:
        if rel.endswith((".example", ".sample", ".md", ".lock")) or "/tests/" in f"/{rel}" or "fixtures" in rel:
            return
        if len(a.secrets_found) >= 25:
            return
        _t, counts = self.redactor.redact_text(text[:100_000])
        if counts:
            m = re.search(
                r"(?i)(api[_-]?key|secret|token|passwd|password)\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{12,}", text
            )
            line = _line_of(text, m.start()) if m else None
            a.secrets_found.append(Located(path=rel, line=line))
            a.warnings.append(f"possible hard-coded secret in {rel}{f':{line}' if line else ''} (value not recorded)")

    # ------------------------------------------------------------------ synthesis
    def _capabilities(self, a: RepositoryAnalysis, hits: dict[str, list[Located]]) -> dict[str, bool]:
        mcp_deps = [
            d
            for d in a.dependencies
            if not d.dev and S.DEPENDENCIES.get(S.normalise_dep(d.name), ("", "", ""))[0] == "mcp"
        ]
        mcp_tools = [t for t in a.tools if t.framework.startswith("MCP")]
        if not a.mcp and mcp_deps and (mcp_tools or not a.apis):
            a.mcp.append(
                McpServerConfig(
                    name=a.root_name, transport="stdio/http", role="server", location=Located(path=mcp_deps[0].source)
                )
            )

        def strong(cap: str, minimum: int = 1) -> bool:
            return len(hits.get(cap, [])) >= minimum

        caps = {
            "tools": bool(a.tools) or strong("tools"),
            "rag": strong("rag", 2) or bool(a.vector_databases),
            "memory": strong("memory", 2) or bool(a.memory_systems),
            "planning": strong("planning", 2),
            "multi_agent": strong("multi_agent", 2)
            or len(
                {
                    d.name
                    for d in a.agent_definitions
                    if d.framework in {"CrewAI", "OpenAI Agents SDK", "AutoGen", "LangGraph node"}
                }
            )
            >= 2,
            "browser": bool(a.browser_frameworks) or strong("browser", 2),
            "computer_use": strong("computer_use"),
            "coding": strong("coding", 3)
            and any(
                h.path.endswith((".py", ".ts", ".js", ".go", ".rs", ".java", ".kt")) for h in hits.get("coding", [])
            ),
            "mcp": bool(a.mcp) or strong("mcp") or any(t.framework.startswith("MCP") for t in a.tools),
            "human_approval": strong("human_approval"),
            "guardrails": strong("guardrails"),
            "streaming": strong("streaming"),
            "multimodal": strong("multimodal", 2),
            "voice": strong("voice", 2),
            "long_running": strong("long_running", 2),
            "event_driven": strong("event_driven", 2),
            "api": bool(a.apis) or bool(a.openapi_specs) or strong("api"),
            "ui": strong("ui"),
            "documents": strong("documents"),
            "state": strong("state") or bool(a.databases),
        }
        if caps["coding"] and not (a.tools or any("agent" in p.path.lower() for p in hits.get("coding", []))):
            caps["coding"] = False
        return caps

    def _build_graph(self, a: RepositoryAnalysis) -> None:
        g: ArchitectureGraph = a.architecture
        g.add_node("user", "User", "actor")
        entry = "interface"
        has_ui = a.capabilities.get("ui")
        has_api = a.capabilities.get("api")
        g.add_node(entry, "Web UI" if has_ui else "HTTP API" if has_api else "Entry point", "interface")
        g.add_edge("user", entry)
        g.add_node("agent", (a.frameworks[0] + " agent") if a.frameworks else "Agent core", "orchestrator")
        g.add_edge(entry, "agent")
        for prov in a.model_providers or []:
            g.add_node(f"llm:{prov}", prov, "llm")
            g.add_edge("agent", f"llm:{prov}", "calls")
        if a.capabilities.get("rag") or a.vector_databases:
            g.add_node("retriever", "Retriever", "retrieval")
            g.add_edge("agent", "retriever", "retrieves")
            for v in a.vector_databases:
                g.add_node(f"vec:{v}", v, "vectordb")
                g.add_edge("retriever", f"vec:{v}")
        for t in a.tools[:25]:
            g.add_node(f"tool:{t.name}", t.name, "tool")
            g.add_edge("agent", f"tool:{t.name}", t.side_effects if t.side_effects != "unknown" else "")
        for m in a.mcp:
            g.add_node(f"mcp:{m.name}", f"MCP: {m.name}", "mcp")
            g.add_edge("agent", f"mcp:{m.name}", m.transport)
        if a.capabilities.get("memory") or a.memory_systems:
            g.add_node("memory", ", ".join(a.memory_systems) or "Memory", "memory")
            g.add_edge("agent", "memory")
        for b in a.browser_frameworks:
            g.add_node(f"browser:{b}", b, "browser")
            g.add_edge("agent", f"browser:{b}")
        if a.capabilities.get("multi_agent"):
            names = []
            for d in a.agent_definitions:
                if d.name not in names:
                    names.append(d.name)
            for n in names[:12]:
                g.add_node(f"agent:{n}", n, "agent")
                g.add_edge("agent", f"agent:{n}", "delegates")
        for db in a.databases:
            g.add_node(f"db:{db}", db, "database")
            g.add_edge("agent", f"db:{db}")


__all__ = ["RepositoryAnalyzer", "BINARY_EXT"]
