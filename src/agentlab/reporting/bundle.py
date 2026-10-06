"""Report bundles: render, scrub, version, checksum and store (spec sections 29 and 49).

``generate_report`` is the one entry point the CLI, the REST API and the orchestrator share. It builds the report from
the stored run, renders the requested formats, writes a bundle folder and records each rendering as an artifact and a new
*version* of the run's report. Nothing in a bundle is ever edited in place: a later report of the same run is version 2,
next to version 1, and ``checksums.json`` lets anyone verify that a bundle is the one that was produced.

    reports/<run id>/v<n>/
        report.json  report.md  report.html  report.pdf   the same data in four renderings
        run-manifest.json                                  what the run was made of (skills, judges, plan, config)
        checksums.json                                     SHA-256 and size of every other file
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentlab import __version__
from agentlab.core.errors import UserError
from agentlab.reporting import model as m
from agentlab.reporting.build import build_report
from agentlab.reporting.compare import Comparison, compare_runs
from agentlab.reporting.material import load_material
from agentlab.reporting.render_html import render_html
from agentlab.reporting.render_md import render_markdown
from agentlab.reporting.render_pdf import render_pdf
from agentlab.security.redactor import get_redactor
from agentlab.services import Services

log = logging.getLogger(__name__)

FORMATS = ("json", "md", "html", "pdf")
ALIASES = {"markdown": "md", "htm": "html"}
FILE_NAMES = {"json": "report.json", "md": "report.md", "html": "report.html", "pdf": "report.pdf"}
MEDIA_TYPES = {
    "json": "application/json",
    "md": "text/markdown",
    "html": "text/html",
    "pdf": "application/pdf",
}
CHECKSUMS = "checksums.json"
RUN_MANIFEST = "run-manifest.json"
BUNDLE_SCHEMA = "agentlab.report-bundle"


def normalise_formats(formats: Sequence[str] | str | None) -> list[str]:
    """``None`` or ``all`` is every format; names are case-insensitive; an unknown name is an error, not ignored."""
    if formats is None:
        return list(FORMATS)
    raw = [formats] if isinstance(formats, str) else list(formats)
    wanted: list[str] = []
    for item in raw:
        for part in str(item).replace(",", " ").split():
            name = ALIASES.get(part.lower(), part.lower())
            if name == "all":
                return list(FORMATS)
            if name not in FORMATS:
                raise UserError(f"unknown report format '{part}' (use {', '.join(FORMATS)} or all)")
            if name not in wanted:
                wanted.append(name)
    return [f for f in FORMATS if f in wanted] or list(FORMATS)


@dataclass
class ReportBundle:
    """What ``generate_report`` produced. ``formats`` maps a format to its artifact id, ``paths`` to its file."""

    run_id: str
    report_id: str | None
    report_version: int
    formats: dict[str, str] = field(default_factory=dict)
    paths: dict[str, Path] = field(default_factory=dict)
    directory: Path | None = None
    bundle_id: str = ""
    checksums: dict[str, str] = field(default_factory=dict)
    redactions: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    data: m.ReportData | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "report_id": self.report_id,
            "report_version": self.report_version,
            "bundle_id": self.bundle_id,
            "directory": str(self.directory) if self.directory else None,
            "formats": sorted(self.formats),
            "files": {k: str(v) for k, v in self.paths.items()},
            "redactions": self.redactions,
            "warnings": self.warnings,
        }


# ===================================================================================================== scrubbing
def scrub(report: m.ReportData) -> tuple[m.ReportData, dict[str, int]]:
    """Run every string of the report through the secret redactor, once, before any format is rendered, so a secret
    that slipped past the builders can appear in none of the four files. Returns the report and what was masked."""
    redactor = get_redactor()
    hits: dict[str, int] = {}

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            clean, found = redactor.redact_text(value)
            for label, n in found.items():
                hits[label] = hits.get(label, 0) + n
            return clean
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    data = walk(report.to_json_dict())
    return (m.ReportData.model_validate(data) if hits else report), hits


# ===================================================================================================== blobs
def blob_loader(sv: Services, run_id: str, *, include_sensitive: bool = False) -> Callable[[str], bytes | None]:
    """Reads an artifact of this run for embedding (screenshots). A restricted artifact (taken while signed in, or raw
    browser state) is never embedded unless the caller explicitly asked for it; a missing one is simply not embedded."""
    allowed = {
        f"sha256-{row['sha256']}"
        for row in sv.store.list_artifacts(run_id)
        if include_sensitive or row.get("sensitivity", "normal") != "restricted"
    }

    def load(artifact_id: str) -> bytes | None:
        if artifact_id not in allowed:
            return None
        try:
            return sv.artifacts.get(artifact_id)
        except Exception as exc:  # a damaged or purged artifact degrades the report, it does not stop it
            log.debug("artifact %s could not be read for the report: %s", artifact_id, exc)
            return None

    return load


# ===================================================================================================== checksums
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"


def verify_bundle(directory: str | Path) -> list[str]:
    """Check a bundle folder against its ``checksums.json``. Returns the problems found; empty means intact."""
    root = Path(directory)
    index = root / CHECKSUMS
    if not index.is_file():
        return [f"{CHECKSUMS} is missing: nothing to verify against"]
    try:
        listed = json.loads(index.read_text(encoding="utf-8"))
        files: dict[str, dict[str, Any]] = listed["files"]
    except (ValueError, KeyError, TypeError) as exc:
        return [f"{CHECKSUMS} is not a valid checksum index ({type(exc).__name__})"]
    problems: list[str] = []
    for name, info in sorted(files.items()):
        path = root / name
        if Path(name).name != name:
            problems.append(f"{name}: the index names a path outside the bundle")
        elif not path.is_file():
            problems.append(f"{name}: missing")
        else:
            data = path.read_bytes()
            if _sha256(data) != info.get("sha256"):
                problems.append(f"{name}: checksum does not match (the file was changed after it was written)")
            elif len(data) != info.get("bytes"):
                problems.append(f"{name}: size does not match")
    known = set(files) | {CHECKSUMS}
    for path in sorted(root.iterdir()):
        if path.is_file() and path.name not in known:
            problems.append(f"{path.name}: not in {CHECKSUMS} (added after the bundle was written)")
    return problems


# ===================================================================================================== generation
def _bundle_dir(sv: Services, run_id: str, version: int, output: Path | None) -> Path:
    if output is not None:
        return output
    return sv.base_dir / sv.config.storage.reports_dir / run_id / f"v{version}"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _regression(sv: Services, run_id: str, baseline: str | None, warnings: list[str]) -> dict[str, Any] | None:
    if not baseline:
        return None
    try:
        comparison: Comparison = compare_runs(sv, baseline, run_id)
    except UserError as exc:
        warnings.append(f"the baseline comparison was left out: {exc}")
        return None
    return comparison.to_json_dict()


def generate_report(
    sv: Services,
    run_id: str,
    *,
    formats: Sequence[str] | str | None = None,
    output: str | Path | None = None,
    baseline: str | None = None,
    include_sensitive: bool = False,
    write_files: bool = True,
) -> ReportBundle:
    """Build, render and store the report of a stored run.

    * ``formats`` - any of json, md, html, pdf (default: all).
    * ``output`` - folder for the bundle (default: ``<reports_dir>/<run>/v<n>``). It is created and must not already
      hold a bundle: a bundle is never overwritten.
    * ``baseline`` - run id to compare with (default: the baseline the run was started with, if any).
    * ``include_sensitive`` - embed restricted artifacts (screenshots taken while signed in). Off by default.
    """
    wanted = normalise_formats(formats)
    material = load_material(sv, run_id)
    run_id = material.run_id
    warnings: list[str] = list(material.notes)
    if baseline is None:
        baseline = (material.manifest.get("options") or {}).get("baseline_run_id")
    regression = _regression(sv, run_id, baseline, warnings)

    report, redactions = scrub(build_report(sv, material, regression=regression))
    if redactions:
        warnings.append(
            f"{sum(redactions.values())} secret-like value(s) were masked in the report ({', '.join(sorted(redactions))})"
        )
    version = report.report_version
    load = blob_loader(sv, run_id, include_sensitive=include_sensitive)

    rendered: dict[str, bytes] = {}
    for fmt in wanted:
        if fmt == "json":
            rendered[fmt] = _canonical(report.to_json_dict())
        elif fmt == "md":
            rendered[fmt] = render_markdown(report).encode("utf-8")
        elif fmt == "html":
            rendered[fmt] = render_html(report, load_blob=load, json_name=FILE_NAMES["json"]).encode("utf-8")
        else:
            rendered[fmt] = render_pdf(report, load_blob=load)

    manifest_bytes = _canonical(material.manifest)
    files: dict[str, bytes] = {FILE_NAMES[f]: rendered[f] for f in wanted}
    files[RUN_MANIFEST] = manifest_bytes
    checksums = {name: _sha256(data) for name, data in files.items()}
    index = {
        "schema": BUNDLE_SCHEMA,
        "schema_version": 1,
        "run_id": run_id,
        "report_version": version,
        "generated_at": report.generated_at.isoformat(),
        "agentlab_version": __version__,
        "files": {name: {"sha256": checksums[name], "bytes": len(data)} for name, data in sorted(files.items())},
    }
    index_bytes = _canonical(index)
    bundle_id = _sha256(index_bytes)[:24]

    directory: Path | None = None
    paths: dict[str, Path] = {}
    if write_files:
        directory = _bundle_dir(sv, run_id, version, Path(output) if output else None)
        if directory.exists() and (directory / CHECKSUMS).exists():
            raise UserError(
                f"{directory} already holds a report bundle; reports are never overwritten (choose another --output)"
            )
        for name, data in files.items():
            _write(directory / name, data)
        _write(directory / CHECKSUMS, index_bytes)
        paths = {f: directory / FILE_NAMES[f] for f in wanted}

    artifact_ids: dict[str, str] = {}
    for fmt in wanted:
        ref = sv.artifacts.put(
            rendered[fmt],
            kind="report",
            media_type=MEDIA_TYPES[fmt],
            name=f"report-v{version}.{fmt}",
            run_id=run_id,
            redact=False,  # already scrubbed as data; redacting an HTML or PDF again could only damage it
            meta={"format": fmt, "report_version": version, "bundle_id": bundle_id},
        )
        sv.store.register_artifact(ref)
        artifact_ids[fmt] = ref.id
    row = sv.store.save_report(
        run_id,
        artifact_ids,
        {
            "bundle_id": bundle_id,
            "checksums": checksums,
            "directory": str(directory) if directory else None,
            "generated_at": report.generated_at.isoformat(),
            "agentlab_version": __version__,
            "baseline_run_id": baseline if regression else None,
            "redactions": redactions,
            "warnings": warnings,
        },
    )
    return ReportBundle(
        run_id=run_id,
        report_id=str(row["id"]),
        report_version=int(row["report_version"]),
        formats=artifact_ids,
        paths=paths,
        directory=directory,
        bundle_id=bundle_id,
        checksums=checksums,
        redactions=redactions,
        warnings=warnings,
        data=report,
    )


async def default_reporter(outcome: Any, sv: Services) -> ReportBundle:
    """The report phase of the orchestrator: every format configured in ``reporting.formats``."""
    cfg = sv.config.reporting

    def build() -> ReportBundle:
        return generate_report(
            sv,
            str(outcome.run_id),
            formats=cfg.formats,
            include_sensitive=cfg.include_sensitive_artifacts,
            write_files=True,
        )

    url = sv.store.db.url
    if ":memory:" in url or url == "sqlite://":
        return build()  # one shared connection: not for use from two threads
    # a PDF takes seconds of CPU; a worker thread keeps live events and the API responsive meanwhile
    return await asyncio.to_thread(build)


__all__ = [
    "FORMATS",
    "ReportBundle",
    "default_reporter",
    "generate_report",
    "normalise_formats",
    "scrub",
    "verify_bundle",
]
