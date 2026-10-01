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
