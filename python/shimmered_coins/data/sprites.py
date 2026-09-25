"""Import item sprites from a Terraria install into the web UI's sprite directory.

Terraria's ``Content/Images/Item_<id>.xnb`` files (XNA/FNA content format) are decoded to PNG.
The UI then shows real item art; sprites are Re-Logic assets, so they stay gitignored and local
(``web/public/sprites/``).

Supports uncompressed and LZ4-compressed (FNA) XNBs with uncompressed (``Color``) texture data;
DXT-compressed textures are skipped with a warning.
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

_COLOR = 0  # SurfaceFormat.Color: 32 bits per pixel


def lz4_decompress(data: bytes, expected: int) -> bytes:
    """Decompress an LZ4 block (the format FNA uses for compressed .xnb files)."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        token = data[i]
        i += 1
        lit_len = token >> 4
        if lit_len == 15:
            while True:
                b = data[i]
                i += 1
                lit_len += b
                if b != 255:
                    break
        out += data[i : i + lit_len]
        i += lit_len
        if i >= n:
            break  # the last sequence has no match
        offset = data[i] | (data[i + 1] << 8)
        i += 2
        if offset == 0:
            raise ValueError("lz4: zero match offset")
        match_len = token & 0xF
        if match_len == 15:
            while True:
                b = data[i]
                i += 1
                match_len += b
                if b != 255:
                    break
        match_len += 4
        start = len(out) - offset
        for k in range(match_len):  # overlapping matches are legal and must copy byte-wise
            out.append(out[start + k])
    if len(out) != expected:
        raise ValueError(f"lz4: expected {expected} bytes, got {len(out)}")
    return bytes(out)


def _read_u7(buf: memoryview, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, pos
        shift += 7


def _read_string(buf: memoryview, pos: int) -> tuple[str, int]:
    length, pos = _read_u7(buf, pos)
    s = bytes(buf[pos : pos + length]).decode("utf-8")
    return s, pos + length


def decode_xnb(data: bytes) -> tuple[int, int, bytes]:
    """An XNB Texture2D -> (width, height, rgba bytes). Raises on anything else."""
    if data[:3] != b"XNB":
        raise ValueError("not an XNB file")
    flags = data[5]
    size = struct.unpack_from("<I", data, 6)[0]
    if flags & 0x80:  # compressed (FNA: LZ4)
        uncompressed = struct.unpack_from("<I", data, 10)[0]
        payload = memoryview(lz4_decompress(bytes(data[14:size]), uncompressed))
    else:
        payload = memoryview(data[10:size])
    pos = 0
    readers, pos = _read_u7(payload, pos)
    for _ in range(readers):
        _, pos = _read_string(payload, pos)  # reader type name
        pos += 4  # reader version
    pos = _read_u7(payload, pos)[1]  # shared resource count
    pos = _read_u7(payload, pos)[1]  # the primary object's type reader index
    fmt, width, height, mips = struct.unpack_from("<4I", payload, pos)
    pos += 16
    if fmt != _COLOR:
        raise ValueError(f"surface format {fmt} is not plain RGBA (DXT textures are unsupported)")
    if mips == 0:
        raise ValueError("runtime-generated texture (no pixel data)")
    data_len = struct.unpack_from("<I", payload, pos)[0]
    pos += 4
    rgba = bytes(payload[pos : pos + data_len])
    if len(rgba) != width * height * 4:
        raise ValueError("texture data size mismatch")
    return width, height, rgba


def write_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    """Minimal PNG writer (stdlib zlib only)."""
    if width * height * 4 != len(rgba):
        raise ValueError("png: data size mismatch")

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + rgba[y * width * 4 : (y + 1) * width * 4] for y in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


_ITEM_XNB = re.compile(r"^Item_\d+\.xnb$", re.IGNORECASE)


def import_sprites(images_dir: Path, out_dir: Path, *, bgra: bool = False, force: bool = False):
    """Convert every Item_<id>.xnb under images_dir to out_dir/Item_<id>.png."""
    files = sorted(p for p in images_dir.rglob("*.xnb") if _ITEM_XNB.match(p.name))
    if not files:
        raise FileNotFoundError(f"no Item_*.xnb under {images_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    done = skipped = 0
    for src in files:
        dst = out_dir / (src.stem + ".png")
        if dst.exists() and not force:
            skipped += 1
            continue
        try:
            w, h, rgba = decode_xnb(src.read_bytes())
        except ValueError as e:
            print(f"  skip {src.name}: {e}")
            continue
        if bgra:  # some content pipelines store BGRA; --bgra swaps channels
            b = bytearray(rgba)
            b[0::4], b[2::4] = rgba[2::4], rgba[0::4]
            rgba = bytes(b)
        write_png(dst, w, h, rgba)
        done += 1
    return done, skipped


__all__ = ["decode_xnb", "import_sprites", "lz4_decompress", "write_png"]
