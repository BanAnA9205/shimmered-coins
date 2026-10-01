import numpy as np
import pytest
from conftest import graph
from shimmered_coins.data import Dataset
from shimmered_coins.model import BUY, CRAFT, SELL, SHIMMER, NpcSetup, WorldConfig, build_graph
from shimmered_coins.search import SearchParams, quantize, search, simulate


def seq_of(g, *specs):
    """Pick transforms by (kind, item name) where item is the bought/sold/produced item.
    For buys the cheapest (item, NPC) column wins, as the old single-offer graph had."""
    out = []
    for kind, name in specs:
        item = g.dataset.item_by_name(name).id
        candidates = [
            t
            for t in g.transforms
            if t.kind == kind
            and item in (t.output_items if kind in (BUY, CRAFT, SHIMMER) else t.input_items)
        ]
        if kind == BUY:
            best = min(candidates, key=lambda t: t.cost)
            out.append(best)
        else:
            out.append(candidates[0])
    return out


def test_quantize_scales_lp_ratios_to_whole_batches(toy):
    """Ore split between a Bar craft and a Gem transmute: LP ratios 5:1:2:1:1."""
    g = graph(toy, ["downedBoss"])
    seq = seq_of(g, (BUY, "Ore"), (CRAFT, "Bar"), (SHIMMER, "Gem"), (CRAFT, "Ring"), (SELL, "Ring"))
    plan = quantize(seq, np.array([0.01, 0.002, 0.004, 0.002, 0.002]), {})
    assert plan.batches == [5, 1, 2, 1, 1]
    assert (plan.capital, plan.profit) == (500, 3500)
    assert plan.gain == pytest.approx(7.0)


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


def test_quantize_batch_sizes(toy):
    g = graph(toy)
    seq = seq_of(g, (BUY, "Oak Wood"), (CRAFT, "Stick"), (SELL, "Stick"))
    plan = quantize(seq, np.array([2.0, 1.0, 3.0]), {})
    assert plan.batches == [2, 1, 3]
    assert simulate(seq, [2, 1, 4], {}) is None  # 4 sticks don't exist


# ---- recipe engine (docs/ALGORITHM.md 10.6) -------------------------------------------------


def test_best_recipe_is_r1(toy):
    """R1 (buy 5 Ore, craft Bar, shimmer 2 Ore, craft Ring, sell) is the best recipe."""
    rep = search(graph(toy, ["downedBoss"]), SearchParams(top=1))
    r = rep.results[0]
    assert r.gain == pytest.approx(7.0)
    assert [m.transform.kind for m in r.moves][-1] == "sell"
    assert "sell 1 Ring" in r.moves[-1].text
    assert r.length == 2 and r.places == ["Smith", "shimmer pool"]
    assert rep.stats.complete and rep.loops == []


def test_bundle_recipe_keeps_5_junk(toy):
    """The Bundle decraft recipe keeps its 5 Junk: Ring sold, Junk left over."""
    rep = search(graph(toy), SearchParams(top=10))
    bundle = next(r for r in rep.results if r.leftover == {toy.item_by_name("Junk").id: 5})
    assert bundle.gain == pytest.approx(4000 / 2300 - 1)


def test_top_k_distinct_supports_descending(toy):
    from shimmered_coins.search.recipes import Solver, top_k
    from shimmered_coins.search.table import build_table

    t = build_table(graph(toy, ["downedBoss"]))
    rs = top_k(Solver(t), 6)
    assert len(rs) >= 4
    supps = [r.support() for r in rs]
    assert len(set(supps)) == len(supps)  # no duplicate supports
    assert [r.gain for r in rs] == sorted([r.gain for r in rs], reverse=True)


def loop_dataset():
    """A world with a free-money cycle (A -> B by shimmer, B -> 2A by craft) plus an honest
    profitable pair (buy E, shimmer to F, sell F)."""
    return Dataset.from_dict(
        {
            "version": "loop-1",
            "items": [
                {"id": 1, "name": "A", "value": 100},
                {"id": 2, "name": "B", "value": 50},
                {"id": 3, "name": "E", "value": 100},
                {"id": 4, "name": "F", "value": 1000},
            ],
            "recipes": [
                {"id": 0, "result": 1, "result_stack": 2, "ingredients": [{"item": 2, "count": 1}]}
            ],
            "shops": [{"npc": 1, "item": 3}, {"npc": 1, "item": 1}],
            "shimmer": [
                {"kind": "transmute", "item": 1, "count": 1, "outputs": [[2, 1]]},
                {"kind": "transmute", "item": 3, "count": 1, "outputs": [[4, 1]]},
            ],
            "npcs": [{"id": 1, "name": "Merchant"}],
        }
    )


def test_free_loop_detected_and_banned(toy):
    """The doubling cycle A -> B -> 2A is a duplication exploit: reported as a loop and its
    columns banned, so the honest buy-E-shimmer-to-F recipe wins instead."""
    g = build_graph(loop_dataset(), WorldConfig(), NpcSetup(happiness={1: 1.0}))
    rep = search(g, SearchParams(top=3))
    assert len(rep.loops) == 1
    texts = " ; ".join(m.text for m in rep.loops[0])
    assert "A" in texts and "B" in texts
    assert all(r.gain < 100 for r in rep.results)  # no infinite gain among the results
    top = rep.results[0]
    assert top.gain == pytest.approx(1.0)  # buy E, shimmer to F, sell F: 200/100 - 1


def decompose_dataset():
    """Two disjoint profitable pairs with equal gains, so a must-use over both is a sum."""
    items = [
        {"id": 1, "name": "A1", "value": 100},
        {"id": 2, "name": "A2", "value": 600},
        {"id": 3, "name": "B1", "value": 200},
        {"id": 4, "name": "B2", "value": 1200},
    ]
    return Dataset.from_dict(
        {
            "version": "two-1",
            "items": items,
            "shops": [{"npc": 1, "item": 1}, {"npc": 1, "item": 3}],
            "shimmer": [
                {"kind": "transmute", "item": 1, "count": 1, "outputs": [[2, 1]]},
                {"kind": "transmute", "item": 3, "count": 1, "outputs": [[4, 1]]},
            ],
            "npcs": [{"id": 1, "name": "Merchant"}],
        }
    )


def test_decomposition_returns_atomic_parts(toy):
    ds = decompose_dataset()
    a2, b2 = ds.item_by_name("A2").id, ds.item_by_name("B2").id
    g = build_graph(ds, WorldConfig(), NpcSetup(happiness={1: 1.0}))
    rep = search(g, SearchParams(top=5, must_use=frozenset({a2, b2})))
    assert rep.results
    side = {ds.item_by_name(n).id for n in ("A1", "A2")}
    for r in rep.results:  # every reported recipe is atomic: it stays on one side
        touched = set()
        for m in r.moves:
            touched |= {i for i, _ in m.transform.inputs + m.transform.outputs}
        assert touched <= side or touched.isdisjoint(side)
    assert any(r.gain == pytest.approx(0.2) for r in rep.results)


def cycle_dataset():
    """A cycle (shimmer X -> Y, craft Y -> 2X) that a recipe can only run with a bought seed."""
    return Dataset.from_dict(
        {
            "version": "cyc-1",
            "items": [
                {"id": 1, "name": "X", "value": 100},
                {"id": 2, "name": "Y", "value": 100},
                {"id": 3, "name": "Z", "value": 2000},
            ],
            "recipes": [
                {"id": 0, "result": 1, "result_stack": 2, "ingredients": [{"item": 2, "count": 1}]}
            ],
            "shops": [{"npc": 1, "item": 3}, {"npc": 1, "item": 1}],
            "shimmer": [
                {"kind": "transmute", "item": 1, "count": 1, "outputs": [[2, 1]]},
                {"kind": "decraft", "item": 3, "count": 1, "outputs": [[1, 1]]},
            ],
            "npcs": [{"id": 1, "name": "Merchant"}],
        }
    )


def test_cycle_seeding_and_replay(toy):
    from shimmered_coins.search.schedule import order_pool_moves

    g = build_graph(cycle_dataset(), WorldConfig(), NpcSetup(happiness={1: 1.0}))
    x = g.dataset.item_by_name("X").id
    shimmer = next(t for t in g.transforms if t.kind == "shimmer" and t.inputs[0][0] == x)
    craft = next(t for t in g.transforms if t.kind == "craft" and t.outputs[0][0] == x)
    # 2 rounds of X -> Y -> 2X starting from nothing: needs a seed buy of X
    ordered = order_pool_moves(g, [(shimmer, 2), (craft, 2)], {})
    assert ordered is not None
    pool, seeds = ordered
    assert seeds and seeds[0][0].kind == "buy" and seeds[0][0].outputs[0][0] == x
    from shimmered_coins.search import simulate

    buys = [(seeds[0][0], seeds[0][1])]
    plan = simulate([t for t, _ in buys + pool], [n for _, n in buys + pool], {})
    assert plan is not None and plan.capital > 0  # the seeded plan replays without going negative


def test_search_results_replay(toy):
    """Every reported result replays exactly (the engine already simulates; this pins it)."""
    from shimmered_coins.search import simulate

    g = graph(toy, ["downedBoss", "hardmode"])
    rep = search(g, SearchParams(top=10))
    assert rep.results and rep.stats.unschedulable == 0
    price = {i: t.revenue for i, t in g.sell_of.items()}
    for r in rep.results:
        seq = [m.transform for m in r.moves if m.transform.id >= 0]
        counts = [m.batches for m in r.moves if m.transform.id >= 0]
        plan = simulate(seq, counts, price)
        assert plan is not None and plan.capital == r.capital


def test_tour_places_counts_the_doc_examples(toy):
    g = build_graph(
        toy,
        WorldConfig(),
        NpcSetup(happiness={2: 0.85, 1: 1.0}),  # the Merchant is the happiest -> sells
    )
    from shimmered_coins.search.schedule import tour_places

    merchant_buys = [t for t in g.transforms if t.kind == "buy" and t.npc == 2]
    smith_buy = next(t for t in g.transforms if t.kind == "buy" and t.npc == 1)
    craft = next(t for t in g.transforms if t.kind == "craft")
    shimmer = next(t for t in g.transforms if t.kind == "shimmer")
    # buy at the Merchant, craft, sell to the Merchant: one place
    assert tour_places(g, merchant_buys[:1] + [craft], 2) == ["Merchant"]
    # buy at the Merchant, shimmer, sell to the Merchant: two places
    assert tour_places(g, merchant_buys[:1] + [shimmer], 2) == ["Merchant", "shimmer pool"]
    # buy at the Smith and the Merchant, shimmer, sell to a third NPC: four places
    assert tour_places(g, [smith_buy] + merchant_buys[:1] + [shimmer], 3) == [
        "Smith",
        "Merchant",
        "shimmer pool",
        "Guide",
    ]


def test_score_ranking():
    from shimmered_coins.search.schedule import score

    # break-even in one place loses to +30% over two places, whatever the overhead c
    for c in (0.0, 1.0, 3.0):
        assert score(0.3, 2, c) > score(0.0, 1, c)
    # compounding: repeating a loop k times does not change its per-trip score
    assert score(7.0, 2, 1.0) == pytest.approx(__import__("math").log(8.0) / 3.0)
    assert score(-1.0, 1, 1.0) == -float("inf")


def test_max_places_and_overhead(toy):
    g = graph(toy, ["downedBoss"])
    rep = search(g, SearchParams(top=10, max_places=1))
    assert rep.results and all(r.length <= 1 for r in rep.results)
    # overhead 0 = pure compounding: the R1 recipe (L=2) scores ln(8)/2
    from shimmered_coins.search.schedule import score as sc

    rep = search(g, SearchParams(top=1, overhead=0.0))
    assert rep.results[0].score == pytest.approx(sc(7.0, 2, 0.0))


def test_allow_loss_reports_least_bad(toy):
    from shimmered_coins.model import Constraints

    # only buys and shimmers: everything loses money (the profitable paths all craft)
    cons = Constraints(kinds=frozenset({"buy", "shimmer", "sell"}))
    g = build_graph(toy, WorldConfig(), NpcSetup(happiness={2: 1.0}), cons)
    assert not search(g, SearchParams(top=3)).results
    rep = search(g, SearchParams(top=3, allow_loss=True))
    assert rep.results and all(r.gain < 0 for r in rep.results)
    assert rep.results[0].gain == max(r.gain for r in rep.results)  # least bad first by gain


def test_perf_smoke_on_synthetic_world():
    """A Terraria-sized synthetic table: the whole search must stay interactive."""
    import random
    import time as _time

    rng = random.Random(7)
    n_items = 2000
    items = [
        {"id": i, "name": f"I{i}", "value": rng.choice([10, 50, 100, 300, 1000, 5000])}
        for i in range(1, n_items + 1)
    ]
    recipes = []
    for rid in range(3000):
        res, *ins = rng.sample(range(1, n_items + 1), 3)
        recipes.append(
            {
                "id": rid,
                "result": res,
                "result_stack": rng.choice([1, 1, 2, 3]),
                "ingredients": [{"item": i, "count": rng.randint(1, 3)} for i in ins],
            }
        )
    shimmer = []
    for _ in range(1500):
        a, b = rng.sample(range(1, n_items + 1), 2)
        shimmer.append({"kind": "transmute", "item": a, "count": 1, "outputs": [[b, 1]]})
    shops = [{"npc": 1, "item": i} for i in rng.sample(range(1, n_items + 1), 600)]
    ds = Dataset.from_dict(
        {
            "version": "perf-1",
            "items": items,
            "recipes": recipes,
            "shimmer": shimmer,
            "shops": shops,
            "npcs": [{"id": 1, "name": "Merchant"}],
        }
    )
    g = build_graph(ds, WorldConfig(), NpcSetup(happiness={1: 1.0}))
    t0 = _time.monotonic()
    rep = search(g, SearchParams(top=3, time_limit=120))
    elapsed = _time.monotonic() - t0
    assert rep.stats.solves >= 1
    assert elapsed < 60, f"synthetic search took {elapsed:.1f}s"


def test_wallet_filters_and_scales(toy):
    g = graph(toy, ["downedBoss"])
    # too poor for R1 (capital 500): it must be gone, cheaper recipes stay
    rep = search(g, SearchParams(top=10, wallet=100))
    assert rep.results and all(r.capital <= 100 for r in rep.results)
    assert all(r.gain != pytest.approx(7.0) for r in rep.results)
    # a wallet that is a clean multiple scales repeatable plans up to it, gain unchanged
    rep = search(g, SearchParams(top=10, wallet=23000))
    r1 = rep.results[0]
    assert r1.gain == pytest.approx(7.0) and r1.capital == 23000


def test_shape_dedup_keeps_one_route(toy):
    """Two recipes with the same tour, buys and sells (X -> ... -> Y via different middle items)
    are the same idea: only the best-scoring representative is reported."""
    ds = Dataset.from_dict(
        {
            "version": "dedup-1",
            "items": [
                {"id": 1, "name": "X", "value": 100},
                {"id": 2, "name": "Za", "value": 100},
                {"id": 3, "name": "Zb", "value": 100},
                {"id": 4, "name": "Y", "value": 600},
            ],
            "recipes": [
                # route A: shimmer X -> Za, then craft Za -> Y
                {"id": 0, "result": 4, "result_stack": 1, "ingredients": [{"item": 2, "count": 1}]},
                # route B: craft X -> Zb, then shimmer Zb -> Y
                {"id": 1, "result": 3, "result_stack": 1, "ingredients": [{"item": 1, "count": 1}]},
            ],
            "shops": [{"npc": 1, "item": 1}],
            "shimmer": [
                {"kind": "transmute", "item": 1, "count": 1, "outputs": [[2, 1]]},
                {"kind": "transmute", "item": 3, "count": 1, "outputs": [[4, 1]]},
            ],
            "npcs": [{"id": 1, "name": "Merchant"}],
        }
    )
    g = build_graph(ds, WorldConfig(), NpcSetup(happiness={1: 1.0}))
    rep = search(g, SearchParams(top=10))
    assert rep.results
    assert all(r.gain == pytest.approx(0.2) for r in rep.results)
    assert len(rep.results) == 1  # the two routes collapsed into one


def test_post_filters_do_not_starve_top(toy):
    """max_places/wallet drop results *after* ranking, so the candidate batch must be larger
    than what is displayed; a filtered search still fills the top list when results exist."""
    g = graph(toy, ["downedBoss", "hardmode"])
    base = search(g, SearchParams(top=10))
    rep = search(g, SearchParams(top=8, max_places=2))
    assert len(rep.results) >= min(len(base.results), 8)


def test_min_gain_filter_fills_top(toy):
    g = graph(toy, ["downedBoss", "hardmode"])
    rep = search(g, SearchParams(top=8, min_gain=0.30, allow_loss=True))
    assert rep.results and all(r.gain > 0.30 - 0.30 for r in rep.results)  # all well above -55%
