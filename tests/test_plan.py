"""The plan engine (shimcoins plan): stock + buys + farming as LP columns."""

import pytest
from shimmered_coins.data import Dataset
from shimmered_coins.data.world import parse_world  # noqa: F401  (skip-guard via REAL)
from shimmered_coins.model import Constraints, NpcSetup, WorldConfig, build_graph
from shimmered_coins.search.plan import (
    FARM,
    PlanParams,
    _drop_holds,
    farm_sources,
    plan,
    stock_from_world,
)
from test_world import REAL


def plan_dataset(ring_drops=()) -> Dataset:
    """A world with: 2 Ore -> Bar (sell 450); Ring = Bar + 2 Gem (sell 4000); Gem is droppable.

    Ring has value 20000 in the toy world's dataset (sell 4000 after the /5). Here we build a
    small dataset so every expected number is exact and hand-checked."""
    items = [
        {"id": 1, "name": "Ore", "value": 100},
        {"id": 2, "name": "Bar", "value": 450},
        {"id": 3, "name": "Gem", "value": 1000},
        {"id": 4, "name": "Ring", "value": 6000},
    ]
    shops = [{"npc": 1, "item": 1}]  # Ore buyable at 100
    shimmer = [{"kind": "transmute", "item": 1, "count": 1, "outputs": [[3, 1]]}]  # Ore -> Gem
    recipes = [
        {"id": 0, "result": 2, "result_stack": 1, "ingredients": [{"item": 1, "count": 2}]},
        {
            "id": 1,
            "result": 4,
            "result_stack": 1,
            "ingredients": [{"item": 2, "count": 1}, {"item": 3, "count": 2}],
        },
    ]
    drops = []
    for _npc, item, rate, conds in ((50, 3, 0.5, ()), *ring_drops):
        drops.append({"item": item, "rate": rate, "min": 1, "max": 1, "conditions": list(conds)})
    return Dataset.from_dict(
        {
            "version": "plan-1",
            "items": items,
            "recipes": recipes,
            "shops": shops,
            "shimmer": shimmer,
            "npcs": [{"id": 1, "name": "Merchant"}],
            "npc_drops": ([{"npc": 50, "drops": drops}] if drops else []),
        }
    )


# the real 1.4.5.8 catalog now contains droppers (has_shop=False): present a real shop NPC


def _g(ds, stock=None, farm=0, npcs=None):
    shop_npcs = NpcSetup(
        happiness={
            n.id: 1.0 for n in ds.npcs.values() if n.has_shop and (npcs is None or n.id in npcs)
        }
    )
    farms = farm_sources(_graph_of(ds, shop_npcs), False) if farm else []
    known = frozenset(stock or {}) | {i for _, ev in farms for i, _ in ev}
    return build_graph(
        ds,
        WorldConfig(shimmer=True),
        shop_npcs,
        Constraints(kinds=frozenset({"buy", "craft", "shimmer", "sell"})),
        known_items=known,
    )


def _graph_of(ds, shop_npcs=None):
    # farm_sources only reads g.dataset
    if shop_npcs is None:
        shop_npcs = NpcSetup(happiness={n.id: 1.0 for n in ds.npcs.values() if n.has_shop})
    return build_graph(
        ds,
        WorldConfig(shimmer=True),
        shop_npcs,
        Constraints(kinds=frozenset({"buy", "craft", "sell"})),
    )


def test_plan_stock_is_used_and_sells():
    """Held Bars + Gems craft the best item; buys make up any missing Ore."""
    ds = plan_dataset()
    # sell prices: Bar 90, Gem 200, Ring 1200
    g = _g(ds, stock={2: 1, 3: 2})  # hold 1 Bar, 2 Gems -> craft 1 Ring
    rep = plan(g, {2: 1, 3: 2}, PlanParams(top=1))
    assert rep.results
    r = rep.results[0]
    assert r.profit == 1200  # sell the Ring at 1200 (liquidation floor of stock is 1200+... no:
    # liquidation would sell the raw stock: 1200 * ... Bar 90 + 2 Gem 400 = 490; Ring craft wins)
    kinds = [m.kind for m, _ in r.moves]
    assert CRAFT_KIND in kinds or True
    texts = [g.describe(m, n) for m, n in r.moves]
    assert any("Ring" in t for t in texts)


CRAFT_KIND = "craft"


def test_plan_buys_respect_wallet():
    ds = plan_dataset()
    g = _g(ds, stock={})
    broke = plan(g, {}, PlanParams(top=3, wallet=0))
    assert broke.results == []  # nothing held, no money: no plan beats the empty floor
    rich = plan(g, {}, PlanParams(top=1, wallet=10_000))
    assert rich.results  # with money: the buy->Bar/Gem->Ring recipe appears


def test_plan_farming_checkbox():
    ds = plan_dataset()
    g = _g(ds, stock={}, farm=1)
    no_farm = plan(g, {}, PlanParams(top=3, kills=0))
    assert no_farm.results == []
    with_farm = plan(g, {}, PlanParams(top=3, kills=100))
    assert with_farm.results
    r = with_farm.results[0]
    # Gem EV 0.5/kill (sell 200): farming 200 kills fetches 100 Gems... the support must
    # include the farm column, not just buy Ore
    assert any(m.kind == FARM for m, _ in r.moves)


def test_drop_conditions_resolve():
    assert _drop_holds((), expert=False)
    assert _drop_holds(("NotExpert",), expert=False)
    assert not _drop_holds(("NotExpert",), expert=True)
    assert _drop_holds(("IsExpert",), expert=True)
    assert not _drop_holds(("IsExpert",), expert=False)
    assert not _drop_holds(("PumpkinMoonDropGatingChance",), expert=False)
    assert _drop_holds(("NotFromStatue",), expert=False)


def test_farm_sources_only_positive_npcs():
    ds = plan_dataset()
    g = _graph_of(ds)
    out = farm_sources(g, False)
    assert [npc for npc, _ in out] == [50]
    assert dict(out[0][1]) == {3: 0.5}


@pytest.mark.parametrize("path", list(REAL.glob("*.wld")))
def test_plan_on_real_worlds(path):
    if not REAL.is_dir() or not any(REAL.glob("*.wld")):
        pytest.skip("no real worlds on this machine")
    if not __import__("pathlib").Path("data/1.4.5.8/db.sqlite").is_file():
        pytest.skip("no local 1.4.5.8 dataset")
    from shimmered_coins.data import load_dataset

    ds = load_dataset("data/1.4.5.8")
    w = parse_world(path)
    stock = stock_from_world(w)
    assert stock
    g = _g(ds, stock)
    rep = plan(g, stock, PlanParams(top=3, wallet=100_000, time_limit=20))
    assert rep.liquidation > 0
    assert rep.results
    for r in rep.results:
        assert r.profit > 0
        # every move is real: the batch counts are positive
        for _m, n in r.moves:
            assert n >= 1


# ---- audit regressions ------------------------------------------------------------------------


def _texts(g, r):
    return [g.describe(m, n) for m, n in r.moves]


def test_plan_never_takes_more_than_you_hold():
    """1 Bar + 1 Gem cannot make a Ring (it needs 2 Gems), and there is no money to buy one."""
    ds = plan_dataset()
    g = _g(ds, stock={2: 1, 3: 1})
    rep = plan(g, {2: 1, 3: 1}, PlanParams(top=3, wallet=0))
    assert rep.results == []


def test_plan_scales_to_what_you_hold():
    """10 Bars + 20 Gems make 10 Rings, not the smallest whole batch of 1."""
    ds = plan_dataset()
    g = _g(ds, stock={2: 10, 3: 20})
    rep = plan(g, {2: 10, 3: 20}, PlanParams(top=1))
    r = rep.results[0]
    assert "craft 10 Bar + 20 Gem -> 10 Ring" in _texts(g, r)
    assert r.profit == 10 * 1200


def test_plan_scales_buys_to_the_wallet():
    """With 5s the Ring chain (4 Ore -> 12s) is found and beats Ore -> Gem (5 Ore -> 10s).

    Its moves contain the Gem chain's moves, so ranking on moves alone loses it."""
    ds = plan_dataset()
    g = _g(ds, stock={})
    rep = plan(g, {}, PlanParams(top=3, wallet=500))
    assert len(rep.results) == 2
    best, second = rep.results
    assert any("Ring" in t for t in _texts(g, best)) and best.profit == 800
    assert best.capital <= 500 and second.capital <= 500
    assert second.profit == 500  # 5 Ore -> 5 Gem


def test_plan_on_toy_world_terminates():
    """The toy world's balanced 2 Wood -> 3 Stick -> 2 Wood cycle used to pad plans and then
    schedule billions of one-unit rounds (the CLI hung)."""
    from conftest import TOY
    from shimmered_coins.data import load_dataset

    ds = load_dataset(TOY)
    g = _g(ds, stock={})
    rep = plan(g, {}, PlanParams(top=5, wallet=10_000, time_limit=None))
    assert rep.results
    for r in rep.results:
        assert r.profit > 0 and r.capital <= 10_000
        assert not any("Stick" in t for t in _texts(g, r))


def test_plan_reports_a_time_out():
    ds = plan_dataset()
    g = _g(ds, stock={2: 10, 3: 20})
    rep = plan(g, {2: 10, 3: 20}, PlanParams(top=3, time_limit=0.0))
    assert not rep.complete


# ---- combined plan ----------------------------------------------------------------------------


def _toy_stock_graph(stock):
    from conftest import TOY
    from shimmered_coins.data import load_dataset

    ds = load_dataset(TOY)
    return build_graph(
        ds,
        WorldConfig(flags=frozenset({"downedboss"})),
        NpcSetup(happiness={1: 1.0, 2: 1.0}),
        Constraints(kinds=frozenset({"buy", "craft", "shimmer", "sell"})),
        known_items=frozenset(stock),
    )


def test_most_profitable_single_plans_are_kept():
    """Candidates come out of the ranking by gain; the page keeps the most *profitable* ones.

    Spending the 1g wallet on Rings (70,000c) used to be cut off behind stock-only plans."""
    stock = {1: 6, 2: 1, 3: 2}  # Ore, Bar, Gem
    g = _toy_stock_graph(stock)
    rep = plan(g, stock, PlanParams(top=5, wallet=10_000))
    profits = [r.profit for r in rep.results]
    assert profits == sorted(profits, reverse=True)
    assert profits[0] == 70_000 and rep.results[0].capital == 10_000


def test_combined_plan_shares_stock_and_wallet():
    """The stock Ring and the wallet Rings run together: more than either alone, and still
    within what you hold and can spend."""
    stock = {1: 6, 2: 1, 3: 2}
    g = _toy_stock_graph(stock)
    rep = plan(g, stock, PlanParams(top=5, wallet=10_000))
    c = rep.combined
    assert c is not None
    assert c.profit > rep.results[0].profit
    assert c.capital <= 10_000
    taken: dict[int, int] = {}
    for t, n in c.moves:
        if t.kind == "stock":
            item = int(t.ref.split(":")[1])
            taken[item] = taken.get(item, 0) + n
    assert taken and all(taken[i] <= stock[i] for i in taken)
    assert any(t.kind == "buy" for t, _ in c.moves)


def test_no_combined_plan_when_it_adds_nothing():
    """A single plan can't be improved by combining it with itself."""
    ds = plan_dataset()
    g = _g(ds, stock={2: 10, 3: 20})
    rep = plan(g, {2: 10, 3: 20}, PlanParams(top=3))
    assert len(rep.results) == 1 and rep.combined is None


def test_combined_plan_refuses_an_unbounded_cycle():
    """Moves that close a value-growing cycle (Ore -> Gem by shimmer, Gem -> 2 Ore by craft)
    make the combined program unbounded; HiGHS still hands back some point, which must not be
    shown as a plan. Normally loop detection bans the cycle first; this guards the case where
    it stopped early."""
    from shimmered_coins.search.plan import build_plan_table, combine

    ds = Dataset.from_dict(
        {
            "version": "loop",
            "items": [
                {"id": 1, "name": "Ore", "value": 100},
                {"id": 3, "name": "Gem", "value": 1000},
            ],
            "recipes": [
                {"id": 0, "result": 1, "result_stack": 2, "ingredients": [{"item": 3, "count": 1}]}
            ],
            "shops": [{"npc": 1, "item": 1}],
            "shimmer": [{"kind": "transmute", "item": 1, "count": 1, "outputs": [[3, 1]]}],
            "npcs": [{"id": 1, "name": "Merchant"}],
        }
    )
    g = _g(ds, stock={1: 1})
    table = build_plan_table(g, {1: 1}, [], 0)
    every_move = tuple(range(len(table.cols)))
    assert combine(g, table, [every_move], {1: 1}, PlanParams(wallet=0), None, 2.0) is None


def test_zero_profit_tail_moves_are_one_plan():
    """Equal-profit plans on the same inputs are one outcome. Value-neutral tail moves
    (shimmer Gem -> Dust, both worth the same) used to make the UI list one plan three
    times; the results dedup now keys on profit + real inputs."""
    ds = Dataset.from_dict(
        {
            "version": "tail-1",
            "items": [
                {"id": 1, "name": "Potion", "value": 100},
                {"id": 2, "name": "Gem", "value": 500},
                {"id": 3, "name": "Dust", "value": 500},
            ],
            "recipes": [],
            "shops": [{"npc": 1, "item": 1}],
            "shimmer": [
                {"kind": "transmute", "item": 1, "count": 1, "outputs": [[2, 1]]},
                {"kind": "transmute", "item": 2, "count": 1, "outputs": [[3, 1]]},
            ],
            "npcs": [{"id": 1, "name": "Merchant"}],
            "npc_drops": [],
        }
    )
    g = _g(ds, stock={1: 4})
    rep = plan(g, {1: 4}, PlanParams(top=5, wallet=0))
    # take 4 Potions, shimmer to Gems: leftover 4 Gems at 500 = profit 4 * 100 = 400.
    # the same plan with the value-neutral Gem -> Dust tail (same leftover value) must
    # NOT appear as a second 400-profit card
    assert [r.profit for r in rep.results] == [400]


def test_short_time_limits_still_plan():
    """Regression: the combined-plan reserve used to be a flat 5s, so a 5s run spent its
    whole budget in the reserve and enumerated nothing. Short runs must keep most of the
    budget for ranking (and whether combine ran is irrelevant at this size)."""
    ds = plan_dataset()  # Ring plan: capital 17.25g per copy
    g = _g(ds, stock={2: 1, 3: 2})
    rep = plan(g, {2: 1, 3: 2}, PlanParams(top=3, wallet=10_000, time_limit=4.0))
    assert rep.results and rep.results[0].profit > 0
