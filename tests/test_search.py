import numpy as np
import pytest
from conftest import graph
from shimmered_coins.model import BUY, CRAFT, SELL, SHIMMER
from shimmered_coins.search import quantize, simulate


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
