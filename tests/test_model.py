from conftest import graph
from shimmered_coins.model import (
    ANY_ZONE,
    BUY,
    CRAFT,
    SELL,
    SHIMMER,
    Constraints,
    NpcSetup,
    WorldConfig,
    build_graph,
    buy_price,
    condition_holds,
    sell_price,
)
from shimmered_coins.model.graph import format_coins


def test_prices():
    assert buy_price(1000, 1.0, False) == 1000
    assert buy_price(1000, 0.75, False) == 750
    assert buy_price(1000, 1.0, True) == 800
    assert buy_price(1, 0.75, False) == 1  # never free
    assert sell_price(1000, 1.0) == 200
    assert sell_price(1000, 0.8) == 250
    assert sell_price(4, 1.0) == 0


def test_sell_uses_happiest_npc():
    s = NpcSetup(happiness={1: 1.2, 2: 0.9})
    assert s.best_sell_adjustment() == 0.9
    assert NpcSetup(happiness={1: 0.8}, happiness_affects_selling=False).best_sell_adjustment() == 1


def test_conditions():
    w = WorldConfig(flags=frozenset({"hardmode"}))
    assert w.holds(("hardmode",))
    assert not w.holds(("hardmode", "downedBoss"))
    assert w.holds(("!downedBoss",))
    assert not w.holds(("!hardmode",))


def test_buy_sell_graph(toy):
    g = graph(toy)
    names = {g.name(i): t.cost for i, t in g.buy_of.items()}
    # Kit needs downedBoss; the unsellable Junk never appears for sale.
    assert names == {
        "Ore": 100,
        "Gem": 1000,
        "Oak Wood": 10,
        "Bundle": 2000,
        "Bar": 900,
        "Bottle": 20,
    }
    assert g.sell_unit(toy.item_by_name("Ring").id) == 4000
    assert toy.item_by_name("Junk").id not in g.sell_of
    assert g.buy_of[toy.item_by_name("Bar").id].limited


def test_happiness_changes_buy_prices(toy):
    g = graph(toy, npcs=NpcSetup(happiness={1: 0.8, 2: 1.0}))
    assert g.buy_of[toy.item_by_name("Ore").id].cost == 80
    # only Smith present: Merchant's stock disappears, selling uses Smith's adjustment
    g = graph(toy, npcs=NpcSetup(happiness={1: 0.8}))
    assert set(g.buy_of) == {toy.item_by_name("Ore").id}


def test_npc_without_shop_cannot_buy_from_player(toy):
    g = build_graph(toy, WorldConfig(), NpcSetup(happiness={3: 1.0}))
    assert not g.transforms


def test_recipe_groups_expand(toy):
    g = graph(toy)
    stick = toy.item_by_name("Stick").id
    crafts = [t for t in g.transforms if t.kind == CRAFT and t.output_items == {stick}]
    # Pine Wood isn't obtainable, so only the Oak variant survives pruning
    assert [t.inputs for t in crafts] == [((toy.item_by_name("Oak Wood").id, 2),)]


def test_world_gating(toy):
    ring = toy.item_by_name("Ring").id
    crown = toy.item_by_name("Crown").id
    assert not any(t.output_items == {crown} for t in graph(toy).transforms)
    assert any(t.output_items == {crown} for t in graph(toy, ["hardmode"]).transforms)
    kit_shimmer = [
        t
        for t in graph(toy, ["downedBoss"]).transforms
        if t.kind == SHIMMER and t.output_items == {ring}
    ]
    assert kit_shimmer
    assert not [
        t for t in graph(toy, ["downedBoss"], shimmer=False).transforms if t.kind == SHIMMER
    ]


def test_constraints_filter(toy):
    gem = toy.item_by_name("Gem").id
    g = build_graph(
        toy,
        WorldConfig(),
        NpcSetup(happiness={1: 1.0, 2: 1.0}),
        Constraints(exclude_items=frozenset({gem})),
    )
    assert all(gem not in t.input_items | t.output_items for t in g.transforms)
    g = build_graph(
        toy,
        WorldConfig(),
        NpcSetup(happiness={1: 1.0, 2: 1.0}),
        Constraints(kinds=frozenset({BUY, SELL})),
    )
    assert {t.kind for t in g.transforms} == {BUY, SELL}


def test_format_coins():
    assert format_coins(1_234_567) == "1p 23g 45s 67c"
    assert format_coins(0) == "0c"
    assert format_coins(-150) == "-1s 50c"


# ---- per-NPC conditions ----------------------------------------------------------------------


def buyable(toy, npcs, flags=()):
    g = build_graph(toy, WorldConfig(flags=frozenset(flags)), npcs)
    return {g.name(i): t for i, t in g.buy_of.items()}


def test_zone_conditions_are_per_npc(toy):
    both = {1: 1.0, 2: 1.0}
    assert "Frost Charm" not in buyable(toy, NpcSetup(happiness=both))
    # the Smith living in snow doesn't help: the Merchant sells the charm
    assert "Frost Charm" not in buyable(toy, NpcSetup(happiness=both, zones={1: {"snow"}}))
    got = buyable(toy, NpcSetup(happiness=both, zones={2: frozenset({"Snow", "forest"})}))
    assert got["Frost Charm"].cost == 1500
    # global flags don't unlock zone items either
    assert "Frost Charm" not in buyable(toy, NpcSetup(happiness=both), flags=["zone:snow"])


def test_any_zone_is_a_what_if(toy):
    g = build_graph(toy, WorldConfig(), NpcSetup(happiness={1: 1.0, 2: 1.0}, zones={2: ANY_ZONE}))
    charm = g.buy_of[toy.item_by_name("Frost Charm").id]
    assert "living in snow" in g.describe(charm)


def test_pylon_needs_happiness_and_zone(toy):
    def has_pylon(adj):
        npcs = NpcSetup(happiness={1: 1.0, 2: adj}, zones={2: frozenset({"snow"})})
        return "Snow Pylon" in buyable(toy, npcs)

    assert has_pylon(0.85) and has_pylon(0.9)
    assert not has_pylon(0.95)


def test_npc_presence_condition(toy):
    gem = toy.item_by_name("Gem").id
    # the Smith only stocks Gems (at 11s) while the Merchant lives in town
    only_smith = buyable(toy, NpcSetup(happiness={1: 1.0}))
    assert "Gem" not in only_smith
    g = build_graph(toy, WorldConfig(), NpcSetup(happiness={1: 1.0, 2: 1.0}))
    assert g.buy_of[gem].cost == 1000  # cheaper Merchant offer wins
    assert condition_holds("npc:2", WorldConfig(), 1, NpcSetup(happiness={1: 1.0, 2: 1.0}))
    assert not condition_holds("npc:2", WorldConfig(), 1, NpcSetup(happiness={1: 1.0}))


def test_crafting_location_recipes_are_available(toy):
    g = graph(toy)
    water = toy.item_by_name("Bottled Water").id
    assert any(t.kind == CRAFT and t.output_items == {water} for t in g.transforms)


def test_travel_stock(toy):
    crown = toy.item_by_name("Crown").id
    g = build_graph(
        toy, WorldConfig(), NpcSetup(happiness={1: 1.0}, travel_stock=frozenset({crown}))
    )
    t = g.buy_of[crown]
    assert t.limited and t.cost == 100000
    assert "Travelling Merchant" in g.describe(t)
