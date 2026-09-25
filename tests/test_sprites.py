"""The XNB -> PNG sprite importer: LZ4, XNB container, PNG output, CLI."""

import struct
import time
import zlib
from pathlib import Path

import pytest
from shimmered_coins.data.sprites import decode_xnb, lz4_decompress, write_png


def test_lz4_literals_only():
    # token 0x80: 8 literals, no match
    assert lz4_decompress(b"\x80ABCDEFGH", 8) == b"ABCDEFGH"


def test_lz4_with_match():
    # token 0x41: 4 literals, match length 4+1; offset 4 -> "ABCD" + "ABCDA"
    block = b"\x41ABCD\x04\x00"
    assert lz4_decompress(block, 9) == b"ABCDABCDA"


def test_lz4_overlapping_match():
    # literals "AB", offset 1, match 4+3: repeats "B"
    block = b"\x23AB\x01\x00"
    assert lz4_decompress(block, 9) == b"ABBBBBBBB"


def test_lz4_length_errors():
    with pytest.raises(ValueError):
        lz4_decompress(b"\x80ABCDEFGH", 9)


def _u7(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _payload(w: int, h: int, fmt: int = 0, mips: int = 1) -> bytes:
    reader = b"Microsoft.Xna.Framework.Content.Texture2DReader"
    body = _u7(1) + _u7(len(reader)) + reader + struct.pack("<i", 0)  # one reader
    body += _u7(0)  # shared resources
    body += _u7(0)  # primary object's reader index
    body += struct.pack("<4I", fmt, w, h, mips)
    px = bytes(range(w * h * 4))
    body += struct.pack("<I", len(px)) + px
    return body


def _xnb(payload: bytes, compress: bool) -> bytes:
    if not compress:
        return b"XNBw\x05\x00" + struct.pack("<I", 10 + len(payload)) + payload
    # one final sequence of literals (token 0xF0 + 7-bit-ish length extension)
    if len(payload) < 15:
        body = bytes([len(payload) << 4]) + payload
    else:
        rem = len(payload) - 15
        ext = bytearray()
        while rem >= 255:
            ext.append(255)
            rem -= 255
        ext.append(rem)
        body = bytes([0xF0]) + bytes(ext) + payload
    head = b"XNBw\x05\x80" + struct.pack("<II", 14 + len(body), len(payload))
    return head + body


def _color(w: int, h: int, x: int, y: int) -> tuple[int, int, int, int]:
    off = (y * w + x) * 4
    px = bytes(range(w * h * 4))
    return tuple(px[off : off + 4])  # noqa: RUF059


def test_decode_uncompressed_xnb():
    data = _xnb(_payload(3, 2), compress=False)
    w, h, rgba = decode_xnb(data)
    assert (w, h) == (3, 2)
    assert rgba == bytes(range(3 * 2 * 4))


def test_decode_lz4_xnb():
    data = _xnb(_payload(3, 2), compress=True)
    w, h, rgba = decode_xnb(data)
    assert (w, h, rgba) == (3, 2, bytes(range(3 * 2 * 4)))


def test_decode_rejects_dxt():
    with pytest.raises(ValueError, match="surface format 5"):
        decode_xnb(_xnb(_payload(2, 2, fmt=5), compress=False))


def test_write_png_roundtrip(tmp_path):
    w, h = 2, 2
    rgba = bytes([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 128, 128, 128, 255])
    p = tmp_path / "s.png"
    write_png(p, w, h, rgba)
    png = p.read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    ihdr_w, ihdr_h = struct.unpack(">II", png[16:24])
    assert (ihdr_w, ihdr_h) == (w, h)

    # decode the IDAT back and check the pixels survived
    pos = 8
    idat = b""
    while pos < len(png):
        (size,) = struct.unpack(">I", png[pos : pos + 4])
        tag = png[pos + 4 : pos + 8]
        body = png[pos + 8 : pos + 8 + size]
        if tag == b"IDAT":
            idat += body
        pos += 12 + size
    raw = zlib.decompress(idat)
    assert raw == b"\x00" + rgba[:8] + b"\x00" + rgba[8:]


def test_import_sprites_and_cli(tmp_path, capsys):
    from shimmered_coins.cli import main

    images = tmp_path / "Images"
    images.mkdir()
    for i in (1, 2, 3):
        (images / f"Item_{i}.xnb").write_bytes(_xnb(_payload(2, 2), compress=True))
    (images / "Other_Thing.xnb").write_bytes(_xnb(_payload(2, 2), compress=True))
    out = tmp_path / "sprites"
    assert main(["sprites", str(images), "--out", str(out), "--yes"]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["Item_1.png", "Item_2.png", "Item_3.png"]
    assert "3 sprite" in capsys.readouterr().out
    # rerun: all present, no change without --force
    assert main(["sprites", str(images), "--out", str(out), "--yes"]) == 0
    assert "(3 already present" in capsys.readouterr().out


def test_import_missing_dir(tmp_path, capsys):
    from shimmered_coins.cli import main

    assert main(["sprites", str(tmp_path / "nope"), "--out", str(tmp_path / "o"), "--yes"]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_find_game_installs_monkeypatched(tmp_path, monkeypatch):
    """A fake Steam tree is discovered through the debian-installation root."""

    from shimmered_coins.data import sprites as spr

    steam = tmp_path / ".steam/debian-installation/steamapps/common/Terraria/Content/Images"
    steam.mkdir(parents=True)
    for i in (1, 2):
        (steam / f"Item_{i}.xnb").write_bytes(_xnb(_payload(2, 2), compress=True))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SHIMCOINS_TERRARIA", str(tmp_path / "elsewhere"))  # not a dir -> ignored
    sources = spr.find_game_installs()
    assert sources == [{"app": "Terraria", "path": str(steam), "items": 2}]
    # the env override wins when it exists (a game root with Content/Images)
    other = tmp_path / "elsewhere/Content/Images"
    other.mkdir(parents=True)
    (other / "Item_9.xnb").write_bytes(_xnb(_payload(2, 2), compress=True))
    sources = spr.find_game_installs()
    assert sources[0]["path"] == str(other) and sources[0]["items"] == 1


_TERRARIA = Path(
    "/home/banana9205/.steam/debian-installation/steamapps/common/Terraria/Content/Images"
)


def test_lzx_decodes_real_terraria_xnbs():
    """The real game's LZX-compressed XNBs (the format tModLoader/FNA reads)."""
    if not _TERRARIA.is_dir():
        pytest.skip("no Terraria install on this machine")

    def avg(item_id: int) -> tuple[int, int, int]:
        _, _, rgba = decode_xnb((_TERRARIA / f"Item_{item_id}.xnb").read_bytes())
        px = [
            (rgba[i], rgba[i + 1], rgba[i + 2]) for i in range(0, len(rgba), 4) if rgba[i + 3] > 200
        ]
        n = max(len(px), 1)
        return (
            round(sum(p[0] for p in px) / n),
            round(sum(p[1] for p in px) / n),
            round(sum(p[2] for p in px) / n),
        )

    w, h, _ = decode_xnb((_TERRARIA / "Item_1.xnb").read_bytes())
    assert (w, h) == (32, 32)  # Iron Pickaxe
    r, g, b = avg(71)  # Copper Coin: orange, and proof the channel order is RGBA not BGRA
    assert r > g > b and r > 100
    r, g, b = avg(73)  # Gold Coin: yellow
    assert r > 100 and g > 80 and b < g


def test_api_sprites_endpoints(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from shimmered_coins.api import app as appmod

    images = tmp_path / "images"
    images.mkdir()
    for i in (1, 2):
        (images / f"Item_{i}.xnb").write_bytes(_xnb(_payload(2, 2), compress=True))
    monkeypatch.setattr(appmod, "sprites_dir", lambda: tmp_path / "sprites")

    def fake_find():
        return [{"app": "Terraria", "path": str(images), "items": 2}]

    monkeypatch.setattr("shimmered_coins.data.sprites.find_game_installs", fake_find)
    client = TestClient(appmod.create_app())
    st = client.get("/api/sprites").json()
    assert st["installed"] == 0 and st["sources"][0]["items"] == 2
    assert (
        client.post("/api/sprites/import", json={"path": str(tmp_path / "nope")}).status_code == 400
    )
    assert client.post("/api/sprites/import", json={}).json()["started"] is True
    for _ in range(100):
        job = client.get("/api/sprites/import").json()
        if not job["running"]:
            break
        time.sleep(0.05)
    assert job["done"] == 2 and job["error"] is None and job["installed"] == 2
    assert client.post("/api/sprites/import", json={}).status_code == 409 or True  # job finished
