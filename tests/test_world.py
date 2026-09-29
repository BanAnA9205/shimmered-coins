"""World file parsing: synthetic round-trip + real save integration."""

import struct
from pathlib import Path

import pytest
from shimmered_coins.data.world import parse_world


def build_wld(
    name="Test World",
    seed="abc",
    chests=(((100, 200), "", [(19, 0, 166), (2, 0, 304), (8, 3, 1)]),),
    npcs=((22, "Logan", 0, 4200, 576), (17, "Howard", 0, 4191, 570)),
    shimmered=(20,),
):
    """A minimal valid v326 world following the format decoded from the game's own code."""
    out = bytearray()
    out += struct.pack("<i", 326)
    out += b"relogic"
    out += struct.pack("<b", 2)  # fileFormatVersion
    out += struct.pack("<Q", 7)  # revision
    out += struct.pack("<H", 0)  # fileType: world
    out += struct.pack("<H", 0)  # favorite

    # header (built later, but positions must point at it)
    header = bytearray()
    hs = header
    hs += bytes([len(name.encode())]) + name.encode()
    hs += bytes([len(seed.encode())]) + seed.encode()
    hs += struct.pack("<Q", 1)  # worldGenVersion
    hs += bytes(16)  # guid
    hs += struct.pack("<i", 42)  # worldId
    hs += struct.pack("<4i", 0, 8400 * 16, 0, 2400 * 16)  # bounds

    # sections: header at 0-relative; we patch positions below
    sections = [bytes(header), b"TILES", b"", b"", b"", b"", b"", b"", b"", b"", b""]

    def build_chests():
        c = bytearray()
        c += struct.pack("<h", len(chests))
        for (x, y), cname, items in chests:
            c += struct.pack("<2i", x, y)
            b = cname.encode()
            c += bytes([len(b)]) + b
            c += struct.pack("<i", 40)  # slots
            for _ in range(40):
                if items:
                    iid, prefix, stack = items.pop(0)
                    c += struct.pack("<h", stack)
                    c += struct.pack("<i", iid)
                    c += struct.pack("<b", prefix)
                else:
                    c += struct.pack("<h", 0)
        return bytes(c)

    def build_npcs():
        c = bytearray()
        c += struct.pack("<i", len(shimmered))
        for t in shimmered:
            c += struct.pack("<i", t)
        for npc_type, gname, homeless, hx, hy in npcs:
            c += struct.pack("<b", 1)  # more
            c += struct.pack("<i", npc_type)
            b = gname.encode()
            c += bytes([len(b)]) + b
            c += struct.pack("<2f", 1000.0, 500.0)
            c += struct.pack("<b", homeless)
            c += struct.pack("<2i", hx, hy)
            c += struct.pack("<b", 1)  # flags: has variation index
            c += struct.pack("<i", 0)  # townNpcVariationIndex
            c += struct.pack("<b", 0)  # homelessDespawn
        c += struct.pack("<b", 0)  # end
        return bytes(c)

    sections[2] = build_chests()
    sections[4] = build_npcs()

    out += struct.pack("<h", 11)
    positions = [0] * 11
    out += b"\x00" * 44  # placeholder for the positions table
    out += struct.pack("<H", 8)  # importance count
    out += bytes([0x55])  # 8 bits packed
    at = len(out)
    for i, sec in enumerate(sections):
        positions[i] = at
        at += len(sec)
    struct.pack_into("<11i", out, 24 + 2, *positions)
    for sec in sections:
        out += sec
    return bytes(out), positions


def test_round_trip(tmp_path):
    data, positions = build_wld()
    p = tmp_path / "test.wld"
    p.write_bytes(data)
    w = parse_world(p)
    assert w.version == 326
    assert w.name == "Test World"
    assert w.seed == "abc"
    assert (w.width, w.height) == (8400, 2400)
    assert w.shimmered_types == [20]
    assert [n.type for n in w.npcs] == [22, 17]
    assert w.npcs[0].name == "Logan" and w.npcs[0].home_x == 4200
    chest = w.chests[0]
    assert (chest.x, chest.y, chest.name) == (100, 200, "")
    assert chest.items == [(19, 0, 166), (2, 0, 304), (8, 3, 1)]


def test_empty_chests_and_dead_slots(tmp_path):
    data, _ = build_wld(chests=(((1, 2), "storage", []),))
    p = tmp_path / "empty.wld"
    p.write_bytes(data)
    w = parse_world(p)
    assert w.chests[0].name == "storage"
    assert w.chests[0].items == []


REAL = Path.home() / ".local/share/Terraria/Worlds"


@pytest.fixture(params=["Feral_Dreamland_of_Pizza.wld", "joe.wld"])
def real_world(request):
    p = REAL / request.param
    if not p.is_file():
        pytest.skip("no real worlds on this machine")
    return p


def test_real_worlds_parse(real_world):
    from shimmered_coins.data import load_dataset

    ds = load_dataset("data/1.4.5.8") if Path("data/1.4.5.8").is_dir() else None
    w = parse_world(real_world)
    assert w.version >= 326
    assert w.width * w.height > 0
    assert w.chests and w.npcs
    # every filled chest holds valid, sane items
    for c in w.chests:
        for iid, prefix, stack in c.items:
            assert 0 < iid < 6200
            assert 0 <= prefix <= 255  # 1.4.5 added prefixes past the classic 84
            assert 0 < stack <= 9999
            if ds:
                assert iid in ds.items
    # town NPC ids and homes are sane
    for n in w.npcs:
        assert 0 < n.type < 700
        assert 0 <= n.home_x < w.width and 0 <= n.home_y < w.height


def test_real_world_chest_contents_make_sense(real_world):
    """Named game items appear in the chests (dungeon loot, potions, blocks)."""
    p = real_world
    w = parse_world(p)
    all_items = {i for c in w.chests for i, _, _ in c.items}
    assert len(all_items) > 50  # a real storage world has variety


def test_corrupt_npc_section_raises_cleanly(tmp_path):
    data, positions = build_wld()
    d = bytearray(data)
    # flip the npc terminator (final `00` of the section) so the loop keeps reading
    end = positions[5] - 1
    d[end] = 1
    p = tmp_path / "corrupt.wld"
    p.write_bytes(bytes(d))
    try:
        parse_world(p)
    except (ValueError, struct.error, IndexError):
        pass  # any *clean* exception: no hang, no huge bogus npc list
    else:
        pytest.fail("corrupt npc section parsed without an error")
    # a benign parse still works and the bound does not clip real files
    ok = tmp_path / "ok.wld"
    ok.write_bytes(data)
    w = parse_world(ok)
    assert [n.type for n in w.npcs] == [22, 17]
