# Search algorithm

**Status:** design agreed, not implemented yet. The earlier step-by-step tree search (and its C++
core) was removed. `shimcoins search` and `POST /api/search` report "engine being rebuilt" until
phase 1 lands. Section 10 is the implementation plan.

The engine works in two phases:

1. **Phase 1 – what to do.** Find the best *recipes*: sets of buy / craft / shimmer moves with
   quantities, ignoring order and effort. This is linear programming over a sparse table, solved
   with HiGHS.
2. **Phase 2 – how to do it.** For each top recipe, find the most compact executable sequence and
   its length. Rank by growth per action.

---

## 1. The table

**Rows** are items. **Columns** are concrete moves ("transforms"):

- **buy X** from the cheapest NPC offering it. There's one column per item.
- **craft recipe R.** There's one column per concrete ingredient variant, so "any wood" becomes one
  column per wood.
- **shimmer X → outputs.** There's one column per outcome allowed in the current world.

There are **no sell columns.** Everything sellable that is left at the end is sold (section 2).

`N[i, t]` is the net change of item `i` for one batch of column `t`: negative for items consumed,
positive for items produced. A column can touch many items (a decraft into several outputs), and
an item can appear in many columns (one item used by several recipes). A column with only negative
entries is a sink, for example coins thrown into shimmer.

Per column there is also:

- `cost_t`: copper paid per batch. This is the buy price for buy columns and 0 otherwise.

Per item:

- `p_i`: copper received when selling one unit. It is 0 if the item can't be sold.

World flags, NPCs (happiness, biome, who is present), Travelling Merchant stock and user filters
decide which columns exist. `model/graph.py` already builds exactly this; each `Transform` is one
column.

### Example (toy world, prices in copper)

| item \ column | buy Ore (100) | craft Bar | shimmer Ore→Gem | craft Ring | decraft Bundle | buy Bundle (2000) |
|---|---|---|---|---|---|---|
| Ore | +1 | −3 | −1 | | | |
| Bar | | +1 | | −1 | | |
| Gem | | | +1 | −2 | +2 | |
| Ring (p = 4000) | | | | +1 | | |
| Bundle | | | | | −1 | +1 |
| Junk (p = 0) | | | | | +5 | |

## 2. Recipes

A **recipe** is a vector `v ≥ 0`: how many batches of each column to run.

| quantity | meaning |
|---|---|
| `r = N·v ≥ 0` | the **residue**, what's left at the end. It can never be negative, because you can't use what you don't have. |
| `cost·v` | coins spent (only on buys) |
| `p·r` | coins received by selling the whole residue at the end |
| `gain(v) = p·r / cost·v − 1` | return per coin invested (can be negative) |
| `supp(v) = {t : v_t > 0}` | the moves the recipe uses |

Leftovers are handled by `r ≥ 0`. Sellable leftovers are always sold, because selling never costs
anything. Unsellable leftovers are kept and are worth 0. "Sell 2 bars vs sell 1 bar" can't even be
expressed.

**Example: R1.** Buy 5 Ore, craft 1 Bar, shimmer 2 Ore, craft 1 Ring. So `v = (5, 1, 2, 1, 0, 0)`.

- Residue: Ring +1, everything else 0.
- cost 500, revenue 4000, **gain +7.0**.
- supp = {buy Ore, craft Bar, shimmer, craft Ring}.

**Example: the Bundle recipe.** Buy 1 Bundle and 3 Ore, decraft, craft a Bar, craft a Ring.

- Residue: Ring +1, **Junk +5** (kept, worth 0).
- cost 2300, revenue 4000, gain +0.74.

## 3. Atomic recipes

**Scaling.** `k·v` has the same gain and the same support as `v`, so it is the same recipe.
Recipes are rays.

**Definition.** `v` is **atomic** if `cost·v > 0` and there is no recipe `w ≠ 0` with
`supp(w) ⊊ supp(v)`.

This covers both non-atomic examples:

- "100× A" is A itself (a ray).
- "C = A + B" contains A, which is a strictly smaller recipe.

It is the elementary-flux-mode concept from metabolic network analysis.

**Facts** (standard for this kind of cone):

1. **Decomposition.** Every recipe is a non-negative sum of atomic recipes, each using a subset of
   its moves.
2. **Averaging.** The gain of a sum is the cost-weighted average of the parts' gains. For example,
   R1 + (buy Ore, shimmer, sell Gem) has gain (500·7 + 100·1)/600 = 6.0. **So the best gain is
   always achieved by an atomic recipe**, and ranking atomic recipes loses nothing.
3. **Vertices are atomic.** The atomic recipes are exactly the vertices of the polytope
   `{N·v ≥ 0, cost·v = 1, v ≥ 0}` restricted to non-sink directions. An LP solver returns a
   vertex, so it returns an atomic recipe.
4. **Integer form.** Scaling the rational `v` to the smallest integer vector that respects batch
   sizes gives the minimal input set (your `(X, U)`). The gain is unchanged. This is done by
   `search/quantize.py`.

**Why `cost·v = 1`?** Gain is a ratio and is scale-invariant, and an LP can only maximize linear
things. Without fixing the scale, "maximize revenue" or "maximize profit" is unbounded. Fixing the
spend to 1 copper picks one representative per ray. Then "maximize revenue" means "maximize
revenue per copper", which is the gain. R1 at spend 1 has revenue 8, which is gain 7.

## 4. Phase 1: finding and ranking recipes

### 4.0 Build and prune
Build `N`, `cost` and `p` as `scipy.sparse` / NumPy arrays from `model/graph.py`. Then drop:

- **forward:** columns whose inputs can't be obtained starting from buys;
- **backward:** columns whose outputs can't lead to sellable value.

Both passes are sparse matrix–vector iterations instead of the current Python loops.

### 4.1 Free-money loop check (first)
Some craft/shimmer cycles turn items into *more valuable* items with **no purchase**. These are
duplication exploits, and gain is infinite. The check is:

`maximize p·(N·v)` subject to `N·v ≥ 0`, `cost·v = 0`, `Σv ≤ 1`, `v ≥ 0`.

A positive optimum means a loop exists.

- Report it first ("infinite money loop"). Its moves are the support.
- Ban those columns and repeat until the optimum is 0.
- Only then run 4.2 on the remaining columns. On random data an undetected loop made the main LP
  unbounded and took 41 s to fail.

### 4.2 Best recipe

`maximize p·(N·v)` subject to `N·v ≥ 0`, `cost·v = 1`, `v ≥ 0`.

The gain is the optimum − 1. It's one LP.

### 4.3 Top-K recipes (Lawler ranking)
1. Solve 4.2. The result is recipe #1, with support `S = {t₁ … tₘ}`.
2. Split the rest of the search space into `m` disjoint sub-problems. Sub-problem `i` forbids `tᵢ`
   (upper bound 0) and requires `t₁ … tᵢ₋₁` (lower bound ε, which is well defined because spend is
   fixed to 1). Each is one LP, warm-started from its parent in HiGHS.
3. Keep all open sub-problems in a priority queue by optimum. Pop the best one to get the next
   recipe, split it the same way, and repeat until K recipes are found.
4. **Required moves can yield a sum** of atomic recipes rather than a vertex. In that case,
   decompose it before reporting:
   1. Solve the LP restricted to its support to get an atomic part.
   2. Subtract as much of that part as possible.
   3. Repeat.

   Each part is a candidate. Deduplicate by support.
5. Sub-problems are independent, so they run in a process pool with one HiGHS instance per worker.

### 4.4 Constraints
Every user constraint is a bound or a row:

- **exclude item or move:** `v_t = 0` for columns touching it;
- **only these items:** drop the other columns;
- **must use X:** a row requiring some column that touches X;
- **first purchase in S:** drop the other buy columns;
- **limited stock** (Travelling Merchant): upper bound on the buy column. The recipe is then
  flagged *one-shot*, because it can't be repeated or compounded.
- **cap on the number of moves:** binary `z_t ≥ v_t / M` and `Σz ≤ L` makes it a MILP, still in
  HiGHS. Alternatively, Lawler already produces recipes best-first, so long ones can be filtered
  out.

## 5. Phase 2: ordering and length

**Recipes without cycles.** If the items and moves in a recipe form no cycle, run each move **once,
at full quantity, in dependency order**: all buys → crafts/shimmers topologically → sell all.
Inventory never goes negative, because every producer of an item runs before any consumer and
`N·v ≥ 0`. `search/quantize.simulate` replays the integer plan to verify this.

R1 in order:

| step | Ore | Bar | Gem | Ring | coins |
|---|---|---|---|---|---|
| buy 5 Ore | 5 | 0 | 0 | 0 | −500 |
| craft Bar | 2 | 1 | 0 | 0 | −500 |
| shimmer 2 Ore | 0 | 1 | 2 | 0 | −500 |
| craft Ring | 0 | 0 | 0 | 1 | −500 |
| sell all | 0 | 0 | 0 | 0 | +3500 |

**Recipes with cycles** (e.g. X → Y by shimmer, Y → X by craft) can balance on paper but need X
before any X exists. Phase 2 has to either:

- seed the cycle (buy the missing items, if possible), or
- run it in rounds, or
- reject the recipe.

**This is an open decision** (section 9).

**Length.** A move repeated back to back counts once: holding the mouse to craft 100 of something
is one action. For a recipe without cycles:

`L = Σ_{t ∈ supp} w(kind_t) + w(sell)`

- `w` is a configurable weight per action kind (buy, craft, shimmer, sell), default 1.
- **The exact compression rules are still to be supplied by the user.** They live in a single
  `LengthModel` and nothing else depends on them.

## 6. Ranking

**rate = ln(1 + gain) / L**, the growth per action when the loop is repeated and its proceeds
reinvested.

- 2× in 5 actions (rate 0.139, ×1.149 per action) beats 2.5× in 15 actions (rate 0.061).
- Repeating a loop k times doesn't change its rate.
- Phase 1 ranks by gain, so it generates K' > K candidates. Phase 2 computes `L` for each, and the
  final list is sorted by rate.
- The move-count-capped MILP (4.4), run for s = 1, 2, …, gives the Pareto front of gain vs length,
  on which the best rate always lies.
- One-shot recipes are shown with their rate, labelled as not repeatable.

## 7. Tooling

| job | tool |
|---|---|
| LP / MILP | **HiGHS** via `highspy`: in-place model edits and warm-started re-solves |
| table, pruning | `scipy.sparse`, NumPy |
| parallel top-K | `concurrent.futures` process pool, one HiGHS instance per worker |
| optional backends | SCIP (Apache 2.0) or Gurobi behind a thin `Solver` interface, only if HiGHS proves too slow |
| integer form / replay | `search/quantize.py` (existing, stdlib `fractions` + NumPy) |

**Deliberately not used:**

- **Modelling layers** (Pyomo, PuLP, CVXPY): building the model through them costs more than
  solving it at this size.
- **Full elementary-mode enumeration** (efmtool): the count is astronomically large.

## 8. Measured scale
Synthetic, Terraria-sized model: 5,500 items, 9,400 columns, ~41k non-zeros.

| operation | time |
|---|---|
| HiGHS cold solve | 133 ms |
| warm re-solve after banning one move | ~6 ms median |
| same model with an undetected free loop | 41 s to report unbounded (hence 4.1) |

Estimate for real data:

- prune < 1 s;
- loop check and best recipe ≈ 0.2 s;
- top-50 ≈ 300 re-solves ≈ 2 s single-threaded.

## 9. Open questions
1. **Recipes with cycles** in phase 2: seed, run in rounds, or reject?
2. **Length compression rules.** Pending from the user. `LengthModel` is the only place they go.
3. **Buying the same item from several NPCs.** It is one column today (cheapest offer). Should
   stock limits split it?
4. **Numerical tolerances** for support detection (`v_t > ε`) with prices spanning 1 to 10⁷ copper.
   Scale columns by price.

## 10. Implementation plan
1. **Dependencies:** add `highspy` to the package. `scipy` stays (sparse).
2. **`search/table.py`:** graph → `N` (CSC), `cost`, `p`, column metadata. Also sparse forward and
   backward pruning. The graph-level `_prune` becomes a thin wrapper.
3. **`search/recipes.py`:**
   - `Solver` wrapper over `highspy`: build once, change bounds, warm re-solve;
   - free-loop check (4.1);
   - best recipe (4.2);
   - Lawler top-K with decomposition (4.3);
   - constraints (4.4);
   - integer form via `quantize`.
4. **`search/schedule.py`:**
   - dependency-order schedule for recipes without cycles, checked with `simulate`;
   - cycle detection, which flags the recipe until the open question is settled;
   - `LengthModel` with per-kind weights;
   - rate.
5. **CLI / API / UI:**
   - bring `shimcoins search` and `POST /api/search` back on the new engine;
   - options: `--top`, `--weight kind=w`, `--max-moves`, constraints as today;
   - show gain, length and rate per recipe, plus loop warnings;
   - the UI result card shows the ordered steps and the "sell all" residue.
6. **Tests:**
   - the toy world gives R1 (+7.0) as the best recipe;
   - the Bundle recipe keeps its 5 Junk;
   - a free-loop fixture is detected and banned;
   - Lawler top-K has no duplicate supports and ranks by gain;
   - decomposition returns atomic parts;
   - schedule replay never goes negative;
   - rate ranking puts the A-vs-B example the right way round;
   - a performance smoke test on the synthetic 5,500 × 9,400 model.
7. **Docs:** README usage, and this file updated as questions get settled.
