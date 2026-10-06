"""Synthetic attachments for tests: ``gen://`` references name a *recipe*, never a file on the host.

A multimodal or document agent is tested with inputs whose content is known exactly: a plain red image, a picture of a
code, a truncated PNG, a file of random bytes. They are made at run time from the reference (so a test file holds
``gen://png/text?text=CODE%204821``, not a binary) and are fully deterministic: the same reference always yields the
same bytes. Nothing is fetched and no library beyond the standard one is needed to make an image (PNG is written by
hand; text is drawn with a built-in 5x7 bitmap font, which is also what makes the pictures readable by a simple
reader and therefore checkable).

Recipes::

    gen://png/solid?color=red[&size=48]
    gen://png/text?text=CODE%204821[&scale=3]          upper-cased, wrapped at 64 columns, black on white
    gen://corrupt/png                                  a PNG cut off in the middle of its pixel data
    gen://corrupt/pdf                                  a PDF cut off before its cross-reference table
    gen://bin/random[?size=2048&seed=1]                opaque bytes
    gen://pdf/text?text=...                            a one-page PDF holding the text
    gen://docx/text?text=...                           a Word document holding the text
    gen://txt/text?text=...                            a plain-text file
"""

from __future__ import annotations

import io
import random
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_GENERATED_BYTES = 2_000_000
MAX_PICTURE_SIDE = 2048  # pixels; a plain picture of that size is 12 MB before it is compressed
MAX_PICTURE_TEXT = 1200  # characters in a picture of text (it is drawn pixel by pixel, so cost grows with the area)
COLUMNS = 64  # characters per line of a text picture
GLYPH_W, GLYPH_H = 5, 7

COLOURS: dict[str, tuple[int, int, int]] = {
    "red": (220, 30, 30),
    "green": (30, 160, 60),
    "blue": (30, 70, 220),
    "yellow": (240, 220, 40),
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "orange": (245, 140, 20),
    "purple": (140, 50, 170),
    "pink": (245, 150, 190),
    "grey": (128, 128, 128),
    "brown": (120, 70, 30),
}

_FONT_SOURCE = """
A:.###.|#...#|#...#|#####|#...#|#...#|#...#
B:####.|#...#|#...#|####.|#...#|#...#|####.
C:.###.|#...#|#....|#....|#....|#...#|.###.
D:####.|#...#|#...#|#...#|#...#|#...#|####.
E:#####|#....|#....|####.|#....|#....|#####
F:#####|#....|#....|####.|#....|#....|#....
G:.###.|#...#|#....|#.###|#...#|#...#|.###.
H:#...#|#...#|#...#|#####|#...#|#...#|#...#
I:.###.|..#..|..#..|..#..|..#..|..#..|.###.
J:..###|...#.|...#.|...#.|...#.|#..#.|.##..
K:#...#|#..#.|#.#..|##...|#.#..|#..#.|#...#
L:#....|#....|#....|#....|#....|#....|#####
M:#...#|##.##|#.#.#|#.#.#|#...#|#...#|#...#
N:#...#|##..#|#.#.#|#..##|#...#|#...#|#...#
O:.###.|#...#|#...#|#...#|#...#|#...#|.###.
P:####.|#...#|#...#|####.|#....|#....|#....
Q:.###.|#...#|#...#|#...#|#.#.#|#..#.|.##.#
R:####.|#...#|#...#|####.|#.#..|#..#.|#...#
S:.####|#....|#....|.###.|....#|....#|####.
T:#####|..#..|..#..|..#..|..#..|..#..|..#..
U:#...#|#...#|#...#|#...#|#...#|#...#|.###.
V:#...#|#...#|#...#|#...#|#...#|.#.#.|..#..
W:#...#|#...#|#...#|#.#.#|#.#.#|##.##|#...#
X:#...#|#...#|.#.#.|..#..|.#.#.|#...#|#...#
Y:#...#|#...#|.#.#.|..#..|..#..|..#..|..#..
Z:#####|....#|...#.|..#..|.#...|#....|#####
0:.###.|#...#|#..##|#.#.#|##..#|#...#|.###.
1:..#..|.##..|..#..|..#..|..#..|..#..|.###.
2:.###.|#...#|....#|...#.|..#..|.#...|#####
3:#####|...#.|..#..|...#.|....#|#...#|.###.
4:...#.|..##.|.#.#.|#..#.|#####|...#.|...#.
5:#####|#....|####.|....#|....#|#...#|.###.
6:..##.|.#...|#....|####.|#...#|#...#|.###.
7:#####|....#|...#.|..#..|.#...|.#...|.#...
8:.###.|#...#|#...#|.###.|#...#|#...#|.###.
9:.###.|#...#|#...#|.####|....#|...#.|.##..
_:.....|.....|.....|.....|.....|.....|#####
-:.....|.....|.....|#####|.....|.....|.....
=:.....|.....|#####|.....|#####|.....|.....
+:.....|..#..|..#..|#####|..#..|..#..|.....
::.....|..#..|.....|.....|.....|..#..|.....
.:.....|.....|.....|.....|.....|.##..|.##..
,:.....|.....|.....|.....|.##..|..#..|.#...
!:..#..|..#..|..#..|..#..|..#..|.....|..#..
?:.###.|#...#|....#|...#.|..#..|.....|..#..
/:....#|....#|...#.|..#..|.#...|#....|#....
(:...#.|..#..|.#...|.#...|.#...|..#..|...#.
):.#...|..#..|...#.|...#.|...#.|..#..|.#...
':..#..|..#..|.#...|.....|.....|.....|.....
 :.....|.....|.....|.....|.....|.....|.....
"""


def _parse_font() -> dict[str, tuple[str, ...]]:
    font: dict[str, tuple[str, ...]] = {}
    for line in _FONT_SOURCE.strip("\n").splitlines():
        char, _, rows = line.partition(":") if not line.startswith(("::", " :")) else (line[0], ":", line[2:])
        font[char] = tuple(rows.split("|"))
    return font


FONT: dict[str, tuple[str, ...]] = _parse_font()
UNKNOWN_GLYPH = "?"


@dataclass(frozen=True)
class Generated:
    name: str
    media_type: str
    data: bytes


# ------------------------------------------------------------------------------------------------------------- PNG
def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def encode_png(width: int, height: int, rows: list[bytes]) -> bytes:
    """An 8-bit RGB PNG; ``rows`` holds ``width * 3`` bytes per scanline."""
    raw = b"".join(b"\x00" + row for row in rows)  # filter type 0 (none) on every line
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return PNG_SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")


def solid_png(color: str, size: int = 48) -> bytes:
    rgb = COLOURS.get(color.lower())
    if rgb is None:
        raise ValueError(f"unknown colour '{color}'; known: {', '.join(sorted(COLOURS))}")
    if (
        not 1 <= size <= MAX_PICTURE_SIDE
    ):  # the picture is built in memory: its side is not the author's to choose freely
        raise ValueError(f"picture size {size} is outside the range 1..{MAX_PICTURE_SIDE}")
    row = bytes(rgb) * size
    return encode_png(size, size, [row] * size)


def wrap(text: str, columns: int = COLUMNS) -> list[str]:
    """Greedy word wrap; a word longer than a line is split."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for word in paragraph.split(" "):
            while len(word) > columns:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:columns])
                word = word[columns:]
            candidate = f"{current} {word}" if current else word
            if len(candidate) > columns:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def text_png(text: str, scale: int = 3) -> bytes:
    """``text`` (upper-cased) drawn black on white: every glyph is a 5x7 grid of ``scale``-pixel squares, laid out in
    cells of 6x8 squares, inside a margin of two squares on every side."""
    if len(text) > MAX_PICTURE_TEXT:
        raise ValueError(f"a picture of {len(text)} characters of text is too large (at most {MAX_PICTURE_TEXT})")
    scale = max(1, min(scale, 8))
    lines = wrap(text.upper())
    cols = max(1, max(len(line) for line in lines))
    margin = 2 * scale
    width = 2 * margin + cols * (GLYPH_W + 1) * scale
    height = 2 * margin + len(lines) * (GLYPH_H + 1) * scale
    white, black = b"\xff\xff\xff", b"\x00\x00\x00"
    canvas = [[white] * width for _ in range(height)]
    for li, line in enumerate(lines):
        for ci, char in enumerate(line):
            glyph = FONT.get(char) or FONT[UNKNOWN_GLYPH]
            x0 = margin + ci * (GLYPH_W + 1) * scale
            y0 = margin + li * (GLYPH_H + 1) * scale
            for gy, bits in enumerate(glyph):
                for gx, bit in enumerate(bits):
                    if bit == "#":
                        for dy in range(scale):
                            row = canvas[y0 + gy * scale + dy]
                            for dx in range(scale):
                                row[x0 + gx * scale + dx] = black
    return encode_png(width, height, [b"".join(row) for row in canvas])


def corrupt_png() -> bytes:
    """A valid signature and header, then pixel data that stops in the middle (and no end-of-file chunk)."""
    whole = solid_png("blue", 64)
    cut = whole.index(b"IDAT") + 20
    return whole[:cut]


# ------------------------------------------------------------------------------------------------------ documents
def corrupt_pdf() -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1"
    )


def text_pdf(text: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4, invariant=True)
    y = 800
    for line in wrap(text, 90):
        pdf.drawString(50, y, line)
        y -= 16
        if y < 60:
            pdf.showPage()
            y = 800
    pdf.save()
    return buf.getvalue()


#: A Word file is a zip archive, and zip stamps every member with the time it was written. Stamping them all the same makes
#: the same reference give the same bytes, whatever the time is, which is what "deterministic" has to mean here.
FIXED_ZIP_TIME = (2000, 1, 1, 0, 0, 0)


def _stable_zip(data: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            member = zipfile.ZipInfo(info.filename, date_time=FIXED_ZIP_TIME)
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = info.external_attr
            target.writestr(member, source.read(info.filename))
    return out.getvalue()


def text_docx(text: str) -> bytes:
    import docx

    document = docx.Document()
    for paragraph in text.splitlines() or [""]:
        document.add_paragraph(paragraph)
    buf = io.BytesIO()
    document.save(buf)
    return _stable_zip(buf.getvalue())


# ----------------------------------------------------------------------------------------------------- the recipes
def _one(query: dict[str, list[str]], key: str, default: str = "") -> str:
    return query.get(key, [default])[0]


def generate(ref: str) -> Generated:
    """Build the attachment a ``gen://`` reference names. Raises ``ValueError`` for an unknown or malformed recipe."""
    parts = urlsplit(ref)
    if parts.scheme != "gen":
        raise ValueError(f"'{ref}' is not a gen:// reference")
    kind, _, fmt = f"{parts.netloc}{parts.path}".partition("/")
    query = parse_qs(parts.query, keep_blank_values=True)
    text = unquote(_one(query, "text"))
    if len(text) > MAX_GENERATED_BYTES:
        raise ValueError(f"the text of '{ref[:60]}...' is larger than {MAX_GENERATED_BYTES} characters")
    data: bytes
    if (kind, fmt) == ("png", "solid"):
        colour = _one(query, "color", "red")
        data, name, media = solid_png(colour, int(_one(query, "size", "48"))), f"solid-{colour}.png", "image/png"
    elif (kind, fmt) == ("png", "text"):
        data, name, media = text_png(text, int(_one(query, "scale", "3"))), "text.png", "image/png"
    elif (kind, fmt) == ("corrupt", "png"):
        data, name, media = corrupt_png(), "scan-0417.png", "image/png"
    elif (kind, fmt) == ("corrupt", "pdf"):
        data, name, media = corrupt_pdf(), "statement-0417.pdf", "application/pdf"
    elif (kind, fmt) == ("bin", "random"):
        size = min(int(_one(query, "size", "2048")), MAX_GENERATED_BYTES)
        rng = random.Random(int(_one(query, "seed", "1")))  # noqa: S311 - reproducible test data, not security
        data, name, media = rng.randbytes(size), "attachment-0417.bin", "application/octet-stream"
    elif (kind, fmt) == ("pdf", "text"):
        data, name, media = text_pdf(text), "text.pdf", "application/pdf"
    elif (kind, fmt) == ("docx", "text"):
        data, name = text_docx(text), "text.docx"
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif (kind, fmt) == ("txt", "text"):
        data, name, media = text.encode("utf-8"), "text.txt", "text/plain"
    else:
        raise ValueError(f"unknown gen:// recipe '{kind}/{fmt}' in '{ref}'")
    if len(data) > MAX_GENERATED_BYTES:
        raise ValueError(f"'{ref}' would be larger than {MAX_GENERATED_BYTES} bytes")
    return Generated(name, media, data)


def is_generated(ref: str) -> bool:
    return bool(re.match(r"^gen://", ref))
