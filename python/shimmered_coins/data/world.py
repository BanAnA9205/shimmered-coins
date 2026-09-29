"""Terraria world file (.wld) parsing: chests, town NPCs and world metadata.

The format was reverse-engineered against 1.4.5.8 (file version 326) by reading the game's own
``Terraria.IO.WorldFile`` IL and validating byte-for-byte on real save files. Layout::

    int32 version; "relogic"; byte fileFormatVersion
    u64 revision; u16 fileType; u16 favorite
    int16 sectionCount; int32[sectionCount] positions      # see _SECTIONS
    u16 importanceCount; importanceCount bits packed LSB-first
    -- header (at positions[0]) --
    string name; string seed; u64 worldGenVersion; 16-byte guid; int32 worldId
    int32 x0, x1, y0, y1 (world bounds in pixels, 16 px per tile)
    ... more world state we skip ...
    -- chests (positions[2]) --
    int16 count; per chest: int32 x, int32 y, string name, int32 slotCount,
    per slot: int16 stack (0 = empty slot ends at 2 bytes), else int32 itemId, byte prefix
    -- npcs (positions[4]) --
    int32 shimmeredCount; int32[shimmeredCount] shimmered town npc types
    byte more; while more:
        int32 type; string givenName; float x, float y; bool homeless;
        int32 homeX, int32 homeY; byte flags (bit 0: has int32 townNpcVariationIndex);
        bool homelessDespawn; byte more

The section order after the header: tiles, chests, signs, npcs, dummies, tile entities,
pressure plates, town rooms, bestiary, creative powers (the trailing [bool][string][int32]
footer is the file-identity check). Tiles are LZ4-compressed and skipped via the positions
table, so parsing a large world takes milliseconds.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

# positions[] index -> section (validated against LoadWorld_Version2's call order)
SEC_HEADER, SEC_TILES, SEC_CHESTS, SEC_SIGNS, SEC_NPCS, SEC_DUMMIES = 0, 1, 2, 3, 4, 5


@dataclass
class Chest:
    x: int
    y: int
    name: str
    items: list[tuple[int, int, int]]  # (item id, prefix, stack)


@dataclass
class TownNpc:
    type: int
    name: str  # the given ("display") name, e.g. "Logan"
    homeless: bool
    home_x: int
    home_y: int


@dataclass
class World:
    path: Path
    version: int
    name: str
    seed: str
    width: int  # tiles
    height: int  # tiles
    chests: list[Chest] = field(default_factory=list)
    npcs: list[TownNpc] = field(default_factory=list)
    shimmered_types: list[int] = field(default_factory=list)


def _read_string(d: bytes, p: int) -> tuple[str, int]:
    v = s = 0
    while True:
        b = d[p]
        p += 1
        v |= (b & 0x7F) << s
        if not b & 0x80:
            break
        s += 7
    return d[p : p + v].decode("utf-8", "replace"), p + v


def parse_world(path: str | Path) -> World:
    """Parse a .wld file: metadata, chest contents and town NPC list (with homes)."""
    d = Path(path).read_bytes()
    if d[4:11] != b"relogic":
        raise ValueError(f"{path}: not a Terraria world file")
    (version,) = struct.unpack_from("<i", d, 0)
    if version < 135:
        raise ValueError(f"{path}: world version {version} is not the v2 format")

    # format header: revision, type, favorite, then the section positions table
    (n_sections,) = struct.unpack_from("<h", d, 24)
    positions = struct.unpack_from(f"<{n_sections}i", d, 26)
    # skip the importance bits to reach the header (positions[SEC_HEADER])
    p = 26 + 4 * n_sections
    (importance_count,) = struct.unpack_from("<H", d, p)
    if positions[SEC_HEADER] != p + 2 + (importance_count + 7) // 8:
        raise ValueError(f"{path}: header start mismatch; unsupported world layout")

    # header
    p = positions[SEC_HEADER]
    name, p = _read_string(d, p)
    seed, p = _read_string(d, p)
    p += 8  # worldGenVersion (u64)
    p += 16  # guid
    p += 4  # worldId
    x0, x1, y0, y1 = struct.unpack_from("<4i", d, p)
    width, height = (x1 - x0) // 16, (y1 - y0) // 16
    w = World(path=Path(path), version=version, name=name, seed=seed, width=width, height=height)

    # chests
    p = positions[SEC_CHESTS]
    (n,) = struct.unpack_from("<h", d, p)
    p += 2
    for _ in range(n):
        x, y = struct.unpack_from("<2i", d, p)
        p += 8
        cname, p = _read_string(d, p)
        (nslots,) = struct.unpack_from("<i", d, p)
        p += 4
        items: list[tuple[int, int, int]] = []
        for _ in range(nslots):
            (stack,) = struct.unpack_from("<h", d, p)
            if stack == 0:
                p += 2
                continue
            (iid,) = struct.unpack_from("<i", d, p + 2)
            prefix = d[p + 6]
            p += 7
            items.append((iid, prefix, stack))
        w.chests.append(Chest(x, y, cname, items))

    # town npcs
    npcs_end = (
        positions[SEC_DUMMIES] if len(positions) > SEC_DUMMIES else len(d)
    )  # corrupt files must not walk us off the section
    p = positions[SEC_NPCS]
    (shimmered,) = struct.unpack_from("<i", d, p)
    p += 4
    for _ in range(shimmered):
        w.shimmered_types.append(struct.unpack_from("<i", d, p)[0])
        p += 4
    more = d[p]
    p += 1
    while more:
        (npc_type,) = struct.unpack_from("<i", d, p)
        p += 4
        given, p = _read_string(d, p)
        p += 8  # position floats
        homeless = d[p]
        p += 1
        home_x, home_y = struct.unpack_from("<2i", d, p)
        p += 8
        flags = d[p]
        p += 1
        if flags & 1:
            p += 4  # townNpcVariationIndex
        p += 1  # homelessDespawn
        w.npcs.append(TownNpc(npc_type, given, bool(homeless), home_x, home_y))
        if p > npcs_end:
            raise ValueError(f"{path}: corrupt npc section (walked past {npcs_end})")
        more = d[p]
        p += 1
    return w


__all__ = ["Chest", "TownNpc", "World", "parse_world"]
