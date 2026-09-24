"""Bridge to the optional C++ core (``pip install ./cpp``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ..model.graph import BUY, CRAFT, SELL, SHIMMER, Graph, Transform

if TYPE_CHECKING:
    from .engine import SearchParams, SearchStats

try:
    from shimmered_coins_core import _core
except ImportError:  # the accelerator is optional
    _core = None

KIND_CODE = {BUY: 0, SELL: 1, CRAFT: 2, SHIMMER: 3}


def available() -> bool:
    return _core is not None


def _dense(g: Graph) -> tuple[dict[int, int], list]:
    index: dict[int, int] = {}
    for t in g.transforms:
        for i, _ in t.inputs + t.outputs:
            index.setdefault(i, len(index))

    def remap(pairs: tuple[tuple[int, int], ...]) -> list[tuple[int, int]]:
        return [(index[i], c) for i, c in pairs]

    arrays = [
        [KIND_CODE[t.kind] for t in g.transforms],
        [remap(t.inputs) for t in g.transforms],
        [remap(t.outputs) for t in g.transforms],
        [float(t.cost) for t in g.transforms],
        [float(t.revenue) for t in g.transforms],
    ]
    return index, arrays


def native_search(
    g: Graph, params: SearchParams, w: dict[int, float]
) -> tuple[list[tuple[float, list[Transform], np.ndarray]], SearchStats]:
    from .engine import SearchStats

    if _core is None:
        raise RuntimeError("C++ core not installed: pip install ./cpp")
    index, arrays = _dense(g)
    leftover = [0.0] * len(index)
    for item, v in w.items():
        if item in index:
            leftover[index[item]] = v
    results, (nodes, lps, pruned, elapsed, complete) = _core.search(
        len(index),
        *arrays,
        leftover,
        params.max_steps,
        params.top_n,
        params.leftover != "discard",
        params.min_gain,
        [index[i] for i in params.must_use if i in index],
        params.start_items is not None,
        [index[i] for i in (params.start_items or ()) if i in index],
        params.connected,
        params.exhaustive,
        params.max_nodes,
        params.time_limit if params.time_limit is not None else 0.0,
        params.threads,
    )
    if params.must_use and any(i not in index for i in params.must_use):
        results = []  # a required item that no move touches can never be satisfied
    found = [(gain, [g.transforms[t] for t in seq], np.array(x)) for seq, gain, x in results]
    return found, SearchStats(nodes, lps, pruned, elapsed, complete)
