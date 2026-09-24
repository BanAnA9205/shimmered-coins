import random

import numpy as np
import pytest
from conftest import graph
from shimmered_coins.data import Dataset
from shimmered_coins.model import BUY, CRAFT, SELL, SHIMMER, NpcSetup, WorldConfig, build_graph
from shimmered_coins.search import SearchParams, SequenceLp, quantize, search, simulate


def seq_of(g, *specs):
    """Pick transforms by (kind, item name) where item is the bought/sold/produced item."""
    out = []
    for kind, name in specs:
        item = g.dataset.item_by_name(name).id
        for t in g.transforms:
            touched = t.output_items if kind in (BUY, CRAFT, SHIMMER) else t.input_items
            if t.kind == kind and item in touched:
                out.append(t)
                break
        else:
            raise AssertionError(f"no {kind} for {name}")
    return out


def test_lp_simple_chain(toy):
    g = graph(toy)
    seq = seq_of(g, (BUY, "Ore"), (BUY, "Gem"), (CRAFT, "Bar"), (CRAFT, "Ring"), (SELL, "Ring"))
    res = SequenceLp(seq).maximize({})
    assert res.gain == pytest.approx(4000 / 2300 - 1)
    plan = quantize(seq, res.x, {})
    assert plan.batches == [3, 2, 1, 1, 1]
    assert (plan.capital, plan.profit) == (2300, 1700)


def test_lp_partial_use_of_one_stack(toy):
    """Ore is split: 3 go into a Bar, 2 are shimmered into Gems."""
    g = graph(toy, ["downedBoss"])
    seq = seq_of(g, (BUY, "Ore"), (CRAFT, "Bar"), (SHIMMER, "Gem"), (CRAFT, "Ring"), (SELL, "Ring"))
    res = SequenceLp(seq).maximize({})
    assert res.gain == pytest.approx(7.0)
    plan = quantize(seq, res.x, {})
    assert plan.batches == [5, 1, 2, 1, 1]
    assert plan.gain == pytest.approx(7.0)


def test_lp_decraft_leftovers_and_policy(toy):
    """Bundle -> 2 Gem + 5 Junk; Junk is left over (unsellable), Gems used for a Ring."""
    g = graph(toy)
    seq = seq_of(
        g,
        (BUY, "Bundle"),
        (SHIMMER, "Gem"),
        (BUY, "Ore"),
        (CRAFT, "Bar"),
        (CRAFT, "Ring"),
        (SELL, "Ring"),
    )
    res = SequenceLp(seq).maximize({})
    assert res.gain == pytest.approx(4000 / 2300 - 1)
    plan = quantize(seq, res.x, {})
    assert plan.leftover == {toy.item_by_name("Junk").id: 5}


def test_capital_is_peak_spend_not_total(toy):
    """Selling first funds the next purchase, so capital < total spent."""
    g = graph(toy, ["downedBoss"])
    seq = seq_of(
        g,
        (BUY, "Ore"),
        (CRAFT, "Bar"),
        (SHIMMER, "Gem"),
        (CRAFT, "Ring"),
        (SELL, "Ring"),
        (BUY, "Gem"),
    )
    plan = simulate(seq, [5, 1, 2, 1, 1, 3], {})
    assert plan.capital == 500  # the 3000 of Gems is paid out of the 4000 revenue
    assert plan.profit == 4000 - 500 - 3000


def test_negative_gain(toy):
    g = graph(toy)
    seq = seq_of(g, (BUY, "Gem"), (SELL, "Gem"))
    lp = SequenceLp(seq)
    assert lp.maximize({}).gain == pytest.approx(0.0)
    assert lp.evaluate({}).gain == pytest.approx(200 / 1000 - 1)


def test_quantize_batch_sizes(toy):
    g = graph(toy)
    seq = seq_of(g, (BUY, "Oak Wood"), (CRAFT, "Stick"), (SELL, "Stick"))
    plan = quantize(seq, np.array([2.0, 1.0, 3.0]), {})
    assert plan.batches == [2, 1, 3]
    assert simulate(seq, [2, 1, 4], {}) is None  # 4 sticks don't exist


def test_search_finds_best_chain(toy):
    rep = search(graph(toy), SearchParams(max_steps=5, top_n=3))
    best = rep.results[0]
    assert best.gain == pytest.approx(4000 / 2300 - 1)
    assert [m.transform.kind for m in best.moves][-1] == SELL
    assert best.repeatable
    assert rep.stats.complete


def test_search_respects_max_steps(toy):
    rep = search(graph(toy), SearchParams(max_steps=4, top_n=5))
    # only the limited-stock Bar shortcut fits into 4 moves
    assert rep.results[0].gain == pytest.approx(4000 / 2900 - 1)
    assert not rep.results[0].repeatable
    assert all(len(r.moves) <= 4 for r in rep.results)


def test_search_with_shimmer_transmute(toy):
    rep = search(graph(toy, ["downedBoss"]), SearchParams(max_steps=5))
    assert rep.results[0].gain == pytest.approx(7.0)
    rep = search(graph(toy, ["downedBoss", "hardmode"]), SearchParams(max_steps=6))
    assert rep.results[0].gain == pytest.approx(20000 / 500 - 1)


def test_search_must_use_and_start(toy):
    ds = toy
    rep = search(
        graph(ds), SearchParams(max_steps=6, must_use=frozenset({ds.item_by_name("Bundle").id}))
    )
    assert rep.results and all(
        any(ds.item_by_name("Bundle").id in m.transform.output_items for m in r.moves)
        for r in rep.results
    )
    rep = search(
        graph(ds), SearchParams(max_steps=5, start_items=frozenset({ds.item_by_name("Gem").id}))
    )
    assert all(r.moves[0].transform.outputs[0][0] == ds.item_by_name("Gem").id for r in rep.results)


def test_allow_loss(toy):
    g = build_graph(toy, WorldConfig(), NpcSetup(happiness={2: 1.0}))  # no Smith: no Ore/Bar path
    assert not search(g, SearchParams(max_steps=3)).results
    rep = search(g, SearchParams(max_steps=3, allow_loss=True, top_n=3))
    assert rep.results and rep.results[0].gain < 0


# ---- branch-and-bound must not lose the optimum -------------------------------------------


def random_dataset(seed: int) -> Dataset:
    rng = random.Random(seed)
    n = 9
    items = [
        {"id": i, "name": f"I{i}", "value": rng.choice([10, 50, 100, 300, 1000, 5000])}
        for i in range(1, n + 1)
    ]
    recipes = []
    for rid in range(6):
        k = rng.randint(1, 2)
        ins = rng.sample(range(1, n + 1), k + 1)
        res, ins = ins[0], ins[1:]
        recipes.append(
            {
                "id": rid,
                "result": res,
                "result_stack": rng.choice([1, 1, 2, 3]),
                "ingredients": [{"item": i, "count": rng.randint(1, 3)} for i in ins],
            }
        )
    shimmer = []
    for _ in range(4):
        a, b, c = rng.sample(range(1, n + 1), 3)
        shimmer.append(
            {
                "kind": "decraft",
                "item": a,
                "count": rng.randint(1, 2),
                "outputs": [[b, rng.randint(1, 3)], [c, 1]],
            }
        )
    shops = [{"npc": 1, "item": i} for i in rng.sample(range(1, n + 1), 3)]
    return Dataset.from_dict(
        {
            "version": f"rand-{seed}",
            "items": items,
            "recipes": recipes,
            "shimmer": shimmer,
            "shops": shops,
            "npcs": [{"id": 1, "name": "N"}],
        }
    )


@pytest.mark.parametrize("seed", range(12))
def test_bnb_matches_exhaustive(seed):
    ds = random_dataset(seed)
    g = build_graph(ds, WorldConfig(), NpcSetup(happiness={1: 1.0}))
    for leftover in ("discard", "sell"):
        p = dict(max_steps=4, top_n=1, leftover=leftover, time_limit=None)
        full = search(g, SearchParams(exhaustive=True, **p))
        fast = search(g, SearchParams(**p))
        best_full = full.results[0].lp_gain if full.results else None
        best_fast = fast.results[0].lp_gain if fast.results else None
        if best_full is None:
            assert best_fast is None
        else:
            assert best_fast == pytest.approx(best_full, rel=1e-6)
        assert fast.stats.nodes <= full.stats.nodes
