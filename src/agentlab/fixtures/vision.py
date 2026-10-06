"""A tiny image reader for the multimodal fixture: PNG decoding, dominant colour and text recognition.

The fixture is the *target*, so its reader is its own: it decodes ordinary 8-bit PNGs (all five scanline filters, no
interlacing), names the dominant colour, and recognises text only in pictures drawn with the 5x7 bitmap font AgentLab
generates (``documents.generated``). That limit is what a simulation of a vision model can honestly offer. Anything
else it cannot read it says so, as a real agent should.
"""

from __future__ import annotations

import struct
import zlib
from collections import Counter
from math import gcd

from agentlab.documents.generated import COLOURS, FONT, PNG_SIGNATURE

GLYPH_W, GLYPH_H = 5, 7
_REVERSE: dict[tuple[str, ...], str] = {rows: char for char, rows in FONT.items() if char != " "}
_BLANK = FONT[" "]


class ImageError(ValueError):
    """The bytes are not a PNG this reader can decode."""


class Image:
    def __init__(self, width: int, height: int, channels: int, rows: list[bytearray]) -> None:
        self.width, self.height, self.channels, self.rows = width, height, channels, rows

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        i = x * self.channels
        row = self.rows[y]
        if self.channels in (1, 2):
            return row[i], row[i], row[i]
        return row[i], row[i + 1], row[i + 2]

    def dark(self, x: int, y: int) -> bool:
        r, g, b = self.pixel(x, y)
        return (r + g + b) / 3 < 128


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> list[bytearray]:
    stride = width * bpp
    rows: list[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        if pos >= len(raw):
            raise ImageError("the pixel data ends early")
        kind = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        if len(line) != stride:
            raise ImageError("the pixel data ends early")
        for i in range(stride):
            left = line[i - bpp] if i >= bpp else 0
            up = prev[i]
            upleft = prev[i - bpp] if i >= bpp else 0
            if kind == 0:
                add = 0
            elif kind == 1:
                add = left
            elif kind == 2:
                add = up
            elif kind == 3:
                add = (left + up) >> 1
            elif kind == 4:
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                add = left if pa <= pb and pa <= pc else (up if pb <= pc else upleft)
            else:
                raise ImageError(f"unknown scanline filter {kind}")
            line[i] = (line[i] + add) & 0xFF
        rows.append(line)
        prev = line
    return rows


def decode_png(data: bytes) -> Image:
    if not data.startswith(PNG_SIGNATURE):
        raise ImageError("not a PNG file")
    pos = len(PNG_SIGNATURE)
    header: tuple[int, ...] | None = None
    idat = bytearray()
    ended = False
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        crc = data[pos + 8 + length : pos + 12 + length]
        if len(body) != length or len(crc) != 4:
            raise ImageError("the file ends in the middle of a chunk")
        if struct.unpack(">I", crc)[0] != zlib.crc32(kind + body) & 0xFFFFFFFF:
            raise ImageError(f"chunk {kind.decode('latin-1')} is damaged")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            ended = True
            break
        pos += 12 + length
    if header is None:
        raise ImageError("the image header is missing")
    if not ended:
        raise ImageError("the file ends before the image does")
    width, height, depth, colour_type, _comp, _filt, interlace = header
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colour_type)
    if depth != 8 or channels is None or interlace != 0:
        raise ImageError("only 8-bit, non-interlaced images are supported")
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ImageError("the pixel data is damaged") from exc
    return Image(width, height, channels, _unfilter(raw, width, height, channels))


def dominant_colour(img: Image) -> str:
    """The named colour closest to the most common pixel value (the background, for a picture of text)."""
    counts = Counter(img.pixel(x, y) for y in range(img.height) for x in range(img.width))
    common = counts.most_common(1)[0][0]
    return min(COLOURS, key=lambda name: sum((m - v) ** 2 for m, v in zip(common, COLOURS[name], strict=True)))


def read_text(img: Image) -> str | None:
    """Text drawn by AgentLab's generator, or ``None`` when the picture holds none this reader can recognise."""
    dark_runs: list[int] = []
    dark_pixels = 0
    for y in range(img.height):
        run = 0
        for x in range(img.width):
            if img.dark(x, y):
                run += 1
                dark_pixels += 1
            elif run:
                dark_runs.append(run)
                run = 0
        if run:
            dark_runs.append(run)
    if not dark_runs or not 0.001 <= dark_pixels / (img.width * img.height) <= 0.6:
        return None
    scale = 0
    for run in dark_runs:
        scale = gcd(scale, run)
    margin = 2 * scale
    cols = (img.width - 2 * margin) // ((GLYPH_W + 1) * scale)
    lines = (img.height - 2 * margin) // ((GLYPH_H + 1) * scale)
    if scale < 1 or cols < 1 or lines < 1:
        return None
    out: list[str] = []
    for li in range(lines):
        chars: list[str] = []
        for ci in range(cols):
            x0 = margin + ci * (GLYPH_W + 1) * scale
            y0 = margin + li * (GLYPH_H + 1) * scale
            glyph = tuple(
                "".join(
                    "#" if img.dark(x0 + gx * scale + scale // 2, y0 + gy * scale + scale // 2) else "."
                    for gx in range(GLYPH_W)
                )
                for gy in range(GLYPH_H)
            )
            chars.append(" " if glyph == _BLANK else _REVERSE.get(glyph, "?"))
        out.append("".join(chars).rstrip())
    text = " ".join(line for line in out if line)
    return text or None
