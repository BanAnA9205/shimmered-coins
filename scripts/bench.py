"""Compare the Python and C++ backends on a synthetic world.

    python scripts/bench.py --items 600 --steps 4
"""

import argparse
import random
import time

from shimmered_coins.data import Dataset
from shimmered_coins.model import NpcSetup, WorldConfig, build_graph
from shimmered_coins.search import SearchParams, native, search


def synthetic(n_items: int, seed: int) -> Dataset:
    rng = random.Random(seed)
    items = [{"id": i, "name": f"I{i}", "value": int(10 ** rng.uniform(1, 5))}
             for i in range(1, n_items + 1)]
    recipes = []
    for rid in range(n_items // 2):
        ins = rng.sample(range(1, n_items + 1), rng.randint(2, 4))
        recipes.append({"id": rid, "result": ins[0], "result_stack": rng.choice([1, 1, 1, 2, 5]),
                        "ingredients": [{"item": i, "count": rng.randint(1, 4)} for i in ins[1:]]})
    shimmer = []
    for _ in range(n_items // 6):
        a, b, c = rng.sample(range(1, n_items + 1), 3)
        shimmer.append({"kind": "decraft", "item": a, "count": 1,
                        "outputs": [[b, rng.randint(1, 3)], [c, 1]]})
    shops = [{"npc": 1, "item": i} for i in rng.sample(range(1, n_items + 1), n_items // 5)]
    return Dataset.from_dict({"version": "synthetic", "items": items, "recipes": recipes,
                              "shimmer": shimmer, "shops": shops, "npcs": [{"id": 1, "name": "N"}]})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", type=int, default=400)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--time-limit", type=float, default=120)
    ap.add_argument("--skip-python", action="store_true")
    a = ap.parse_args()
    g = build_graph(synthetic(a.items, a.seed), WorldConfig(), NpcSetup(happiness={1: 1.0}))
    print(f"{len(g.transforms)} transforms, max_steps={a.steps}")
    backends = (["native"] if native.available() else []) + ([] if a.skip_python else ["python"])
    for backend in backends:
        t = time.perf_counter()
        rep = search(g, SearchParams(max_steps=a.steps, top_n=5, backend=backend,
                                     time_limit=a.time_limit, max_nodes=10**9))
        dt = time.perf_counter() - t
        best = f"{rep.results[0].gain:+.2%}" if rep.results else "none"
        s = rep.stats
        print(f"{backend:>6}: {dt:7.2f}s  nodes={s.nodes:>9} lps={s.lp_solves:>9} "
              f"best={best} complete={s.complete}")


if __name__ == "__main__":
    main()
