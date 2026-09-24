"""The C++ core must agree with the Python reference engine."""

import numpy as np
import pytest
from conftest import graph
from shimmered_coins.model import NpcSetup, WorldConfig, build_graph
from shimmered_coins.search import SearchParams, SequenceLp, native, search
from test_search import random_dataset

pytestmark = pytest.mark.skipif(not native.available(), reason="C++ core not built")


def both(g, **kw):
    py = search(g, SearchParams(backend="python", **kw))
    cc = search(g, SearchParams(backend="native", **kw))
    return py, cc


def test_lp_matches_scipy(toy):
    g = graph(toy, ["downedBoss"])
    idx, arrays = native._dense(g)
    for n in range(1, 6):
        seq = g.transforms[:n]
        for leftover in ({}, {i: float(t.revenue) for i, t in g.sell_of.items()}):
            ref = SequenceLp(seq).maximize(leftover)
            w = [0.0] * len(idx)
            for i, v in leftover.items():
                w[idx[i]] = v
            ok, gain, x = native._core.maximize(len(idx), *arrays, [t.id for t in seq], w, 1.0)
            assert ok == (ref is not None)
            if ok:
                assert gain == pytest.approx(ref.gain, abs=1e-9)


@pytest.mark.parametrize("flags", [(), ("downedBoss",), ("downedBoss", "hardmode")])
def test_toy_parity(toy, flags):
    g = graph(toy, flags)
    for leftover in ("discard", "sell"):
        py, cc = both(g, max_steps=6, top_n=5, leftover=leftover, time_limit=None)
        assert [r.lp_gain for r in cc.results] == pytest.approx([r.lp_gain for r in py.results])
        assert [r.gain for r in cc.results] == pytest.approx([r.gain for r in py.results])


@pytest.mark.parametrize("seed", range(12))
def test_random_parity(seed):
    ds = random_dataset(seed)
    g = build_graph(ds, WorldConfig(), NpcSetup(happiness={1: 1.0}))
    for exhaustive in (True, False):
        py, cc = both(g, max_steps=4, top_n=1, exhaustive=exhaustive, time_limit=None)
        best_py = py.results[0].lp_gain if py.results else None
        best_cc = cc.results[0].lp_gain if cc.results else None
        assert (best_py is None) == (best_cc is None)
        if best_py is not None:
            assert best_cc == pytest.approx(best_py, rel=1e-6)


def test_constraints_parity(toy):
    g = graph(toy)
    bundle = toy.item_by_name("Bundle").id
    gem = toy.item_by_name("Gem").id
    py, cc = both(g, max_steps=6, must_use=frozenset({bundle}), time_limit=None)
    assert [r.gain for r in cc.results] == pytest.approx([r.gain for r in py.results])
    py, cc = both(g, max_steps=5, start_items=frozenset({gem}), time_limit=None)
    assert [r.gain for r in cc.results] == pytest.approx([r.gain for r in py.results])


def test_auto_backend_and_loss_fallback(toy):
    g = build_graph(toy, WorldConfig(), NpcSetup(happiness={2: 1.0}))
    rep = search(g, SearchParams(max_steps=3, allow_loss=True))  # auto -> python
    assert rep.results and rep.results[0].gain < 0
    with pytest.raises(ValueError):
        search(g, SearchParams(max_steps=3, allow_loss=True, backend="native"))
    assert np.isfinite(search(graph(toy), SearchParams(max_steps=5)).results[0].gain)
