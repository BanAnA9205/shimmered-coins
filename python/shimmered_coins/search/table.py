"""The recipe table (docs/ALGORITHM.md 1-2): items x moves, per-column cost, per-item price.

Rows are items, columns are the graph's transforms minus sells (selling everything sellable at
the end is modelled by ``price``: an item's copper value per unit sold). ``N[i, t]`` is the net
change of item ``i`` per batch of column ``t``.

The pruning of section 4.0 is here as sparse forward/backward reachability; ``graph._prune`` is a
thin wrapper over it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import scipy.sparse as sp

if TYPE_CHECKING:
    from ..model.graph import Graph, Transform


@dataclass
class Table:
    N: sp.csc_matrix  # n_items x n_cols: net item change per batch
    cost: np.ndarray  # (n_cols,) copper spent per batch (buys only)
    price: np.ndarray  # (n_items,) copper per unit when sold (0 = unsellable)
    cols: list[Transform]
    item_index: dict[int, int]

    def revenue(self) -> np.ndarray:
        """Copper per batch of each column: everything it produces, sold (p'N)."""
        return np.asarray(self.N.T @ self.price).ravel()


def build_table(g: Graph, start_items: frozenset[int] | None = None) -> Table:
    """Compile the graph into an LP table. ``start_items`` keeps only those buy columns."""
    cols = [t for t in g.transforms if t.kind != "sell"]
    if start_items is not None:
        cols = [t for t in cols if t.kind != "buy" or t.outputs[0][0] in start_items]
    item_index: dict[int, int] = {}
    for t in cols:
        for i, _ in t.inputs + t.outputs:
            item_index.setdefault(i, len(item_index))
    N, _, _ = column_matrix(cols, item_index)
    price = np.zeros(len(item_index))
    for item, t in g.sell_of.items():
        if item in item_index:
            price[item_index[item]] = t.revenue
    cost = np.array([float(t.cost) for t in cols])
    keep = prune_columns(N, price > 0, np.zeros(len(cols), dtype=bool))
    if not keep.all():
        N = N[:, keep]
        cost = cost[keep]
        cols = [t for t, k in zip(cols, keep) if k]
    return Table(N=N, cost=cost, price=price, cols=cols, item_index=item_index)


def column_matrix(
    ts: list[Transform], item_index: dict[int, int]
) -> tuple[sp.csc_matrix, np.ndarray, np.ndarray]:
    """N for a transform list, plus a sell-column mask and a "is a sell's input" item mask."""
    rows: list[int] = []
    cinds: list[int] = []
    vals: list[float] = []
    is_sell = np.zeros(len(ts), dtype=bool)
    liquid = np.zeros(len(item_index), dtype=bool)
    for c, t in enumerate(ts):
        if t.kind == "sell":
            is_sell[c] = True
            for i, _ in t.inputs:
                liquid[item_index[i]] = True
        for i, k in t.outputs:
            rows.append(item_index[i])
            cinds.append(c)
            vals.append(float(k))
        for i, k in t.inputs:
            rows.append(item_index[i])
            cinds.append(c)
            vals.append(-float(k))
    N = sp.csc_matrix((vals, (rows, cinds)), shape=(len(item_index), len(ts)))
    return N, is_sell, liquid


def prune_columns(N: sp.csc_matrix, base_liquid: np.ndarray, always_keep: np.ndarray) -> np.ndarray:
    """Keep mask: a column stays when its inputs are reachable from zero-input columns (buys)
    and its outputs can reach an item with sell value (or the column is in ``always_keep``)."""
    m, n = N.shape
    neg = (N < 0).tocsc()
    pos = (N > 0).tocsc()
    deg = np.diff(neg.indptr)  # distinct consumed items per column
    usable = deg == 0
    reach = np.zeros(m, dtype=bool)
    cnt = np.zeros(n, dtype=np.int64)
    neg_t = neg.tocsr()  # item -> columns consuming it
    stack = np.flatnonzero(usable).tolist()
    while stack:
        c = stack.pop()
        for i in pos.indices[pos.indptr[c] : pos.indptr[c + 1]]:
            if reach[i]:
                continue
            reach[i] = True
            for cc in neg_t.indices[neg_t.indptr[i] : neg_t.indptr[i + 1]]:
                cnt[cc] += 1
                if cnt[cc] == deg[cc] and not usable[cc]:
                    usable[cc] = True
                    stack.append(cc)

    liquid = base_liquid.copy()
    useful = np.zeros(n, dtype=bool)
    pos_t = pos.tocsr()  # item -> columns producing it
    stack = np.flatnonzero(liquid).tolist()
    while stack:
        i = stack.pop()
        for cc in pos_t.indices[pos_t.indptr[i] : pos_t.indptr[i + 1]]:
            if useful[cc]:
                continue
            useful[cc] = True
            for ii in neg.indices[neg.indptr[cc] : neg.indptr[cc + 1]]:
                if not liquid[ii]:
                    liquid[ii] = True
                    stack.append(ii)

    return usable & (useful | always_keep)
