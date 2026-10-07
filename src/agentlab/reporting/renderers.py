"""Report renderers (spec section 43): each report format is a plug-in.

A renderer turns the finished, secret-scrubbed :class:`ReportData` into the bytes of one file of a report bundle. The
four formats AgentLab ships (``json``, ``md``, ``html``, ``pdf``) are registered here exactly the way a third-party
format is: in ``REPORT_RENDERERS``, by name, through an ``agentlab.report_renderers`` entry point or a module listed in
``plugins:``. Nothing in the report builder, the bundle writer, the REST API or the command line names a format, so a
package that adds one (JUnit XML, SARIF, CSV) adds no code to AgentLab.

The report a renderer receives has already been through the secret redactor, once, for every string in it: a renderer
cannot leak what is not there. It must only use that report (and the blobs it may embed through ``context``).
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from agentlab.core.errors import UserError
from agentlab.core.plugins import Registry
from agentlab.reporting import model as m
from agentlab.reporting.render_html import render_html
from agentlab.reporting.render_md import render_markdown
from agentlab.reporting.render_pdf import render_pdf

BlobLoader = Callable[[str], bytes | None]

#: the formats AgentLab ships, in the order a bundle lists them. A plug-in cannot take one of these names.
BUILTIN_FORMATS: tuple[str, ...] = ("json", "md", "html", "pdf")
#: words with a meaning of their own on the command line and in the API, so no format can be called one of them
RESERVED_NAMES: frozenset[str] = frozenset({"all", "none", "markdown", "htm"})
#: other files of a bundle; no renderer may write one of these
BUNDLE_FILES: frozenset[str] = frozenset({"checksums.json", "run-manifest.json"})

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MEDIA = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


@dataclass(frozen=True)
class RenderContext:
    """What a renderer may use besides the report itself."""

    #: bytes of an artifact of this run that may be embedded in the report (a screenshot), or ``None``
    load_blob: BlobLoader
    #: file name of the JSON rendering in the bundle (the HTML report links to it)
    json_file_name: str = "report.json"


class ReportRenderer(ABC):
    """One report format. Register the class: ``REPORT_RENDERERS.register("junit", JunitRenderer)``. The name it is
    registered under is the format's name everywhere (``--format junit``, ``reporting.formats``, the API)."""

    #: name of the file in the bundle, e.g. ``report.junit.xml``; a bare file name, never a path
    file_name: ClassVar[str]
    #: media type the file is stored and served as, e.g. ``application/xml``
    media_type: ClassVar[str]

    @abstractmethod
    def render(self, report: m.ReportData, context: RenderContext) -> bytes: ...


REPORT_RENDERERS: Registry[type[ReportRenderer]] = Registry("report_renderers")


class JsonRenderer(ReportRenderer):
    file_name = "report.json"
    media_type = "application/json"

    def render(self, report: m.ReportData, context: RenderContext) -> bytes:
        return canonical_json(report.to_json_dict())


class MarkdownRenderer(ReportRenderer):
    file_name = "report.md"
    media_type = "text/markdown"

    def render(self, report: m.ReportData, context: RenderContext) -> bytes:
        return render_markdown(report).encode("utf-8")


class HtmlRenderer(ReportRenderer):
    file_name = "report.html"
    media_type = "text/html"

    def render(self, report: m.ReportData, context: RenderContext) -> bytes:
        return render_html(report, load_blob=context.load_blob, json_name=context.json_file_name).encode("utf-8")


class PdfRenderer(ReportRenderer):
    file_name = "report.pdf"
    media_type = "application/pdf"

    def render(self, report: m.ReportData, context: RenderContext) -> bytes:
        return render_pdf(report, load_blob=context.load_blob)


def canonical_json(obj: object) -> bytes:
    """Sorted keys, two-space indent, a final newline: the same data is always the same bytes (so checksums agree)."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"


for _name, _cls in (("json", JsonRenderer), ("md", MarkdownRenderer), ("html", HtmlRenderer), ("pdf", PdfRenderer)):
    REPORT_RENDERERS.register(_name, _cls, replace=True)


def available_formats() -> list[str]:
    """Every format that can be written: the built-in ones in their usual order, then the plug-ins by name. A plug-in
    whose name is not usable (see :data:`RESERVED_NAMES`, or not lower-case letters, digits, ``-`` and ``_``) is left
    out, because it could not be asked for."""
    extra = [
        n for n in REPORT_RENDERERS.names() if n not in BUILTIN_FORMATS and n not in RESERVED_NAMES and _NAME.match(n)
    ]
    return [*BUILTIN_FORMATS, *extra]


def renderer_for(name: str) -> ReportRenderer:
    """The renderer of format ``name``, checked: a plug-in that names a file outside the bundle, or reuses another
    file's name, is refused here and not trusted to behave."""
    try:
        cls = REPORT_RENDERERS.get(name)
    except KeyError:
        raise UserError(f"unknown report format '{name}' (use {', '.join(available_formats())} or all)") from None
    problem = renderer_problem(name, cls)
    if problem:
        raise UserError(f"report format '{name}' cannot be written: {problem}")
    return cls()


def renderer_problem(name: str, cls: type[ReportRenderer]) -> str | None:
    """Why ``cls`` is not a usable renderer for ``name``; ``None`` when it is."""
    file_name = getattr(cls, "file_name", None)
    media_type = getattr(cls, "media_type", None)
    if not callable(getattr(cls, "render", None)):
        return "it has no render() method"
    if not isinstance(file_name, str) or not _FILE.match(file_name) or ".." in file_name:
        return f"file_name {file_name!r} is not a bare file name"
    if file_name in BUNDLE_FILES:
        return f"file_name {file_name!r} is a file of the bundle itself"
    if not isinstance(media_type, str) or not _MEDIA.match(media_type):
        return f"media_type {media_type!r} is not a media type"
    for other, other_cls in REPORT_RENDERERS.items():
        if other != name and getattr(other_cls, "file_name", None) == file_name:
            return f"file_name {file_name!r} is already the file of format '{other}'"
    return None


def file_name_of(name: str) -> str:
    return renderer_for(name).file_name


def media_type_of(name: str, default: str = "application/octet-stream") -> str:
    """Media type of format ``name`` (``default`` when no such format is installed any more, e.g. an old report whose
    plug-in was removed)."""
    try:
        return str(REPORT_RENDERERS.get(name).media_type)
    except KeyError:
        return default


__all__ = [
    "BUILTIN_FORMATS",
    "BUNDLE_FILES",
    "REPORT_RENDERERS",
    "RESERVED_NAMES",
    "RenderContext",
    "ReportRenderer",
    "available_formats",
    "canonical_json",
    "file_name_of",
    "media_type_of",
    "renderer_for",
    "renderer_problem",
]
