from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from shimmered_coins.data import load_dataset  # noqa: E402
from shimmered_coins.model import NpcSetup, WorldConfig, build_graph  # noqa: E402

TOY = ROOT / "python" / "shimmered_coins" / "demo" / "toy_world.json"


@pytest.fixture
def toy():
    return load_dataset(TOY)


@pytest.fixture
def neutral_npcs():
    return NpcSetup(happiness={1: 1.0, 2: 1.0})


def graph(ds, flags=(), npcs=None, **kw):
    return build_graph(
        ds, WorldConfig(flags=frozenset(flags), **kw), npcs or NpcSetup(happiness={1: 1.0, 2: 1.0})
    )
