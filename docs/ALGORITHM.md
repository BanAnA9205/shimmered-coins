# Search algorithm

**Status:** phase 1 and phase 2 are implemented (`search/table.py`, `search/recipes.py`,
`search/schedule.py`, `search/engine.py`), verified against the toy world and a real 1.4.5.8
dataset. Not implemented yet: the `L`-level Pareto front as a MILP (6.2 — `--max-places` is a
post-filter for now), the neutral catalogue (6.3), and the parallel top-K process pool. Section
10 tracks the implementation plan.

The engine works in two phases:

1. **Phase 1 – what to do.** Find the best *recipes*: sets of buy / craft / shimmer moves with
   quantities, ignoring order and effort. This is linear programming over a sparse table, solved
   with HiGHS.
2. **Phase 2 – how to do it.** For each top recipe, find the most compact executable sequence and
   its length (the number of places visited). Rank by growth per trip.

---

## 1. The table

**Rows** are items. **Columns** are concrete moves ("transforms"):

- **buy X from NPC n.** There's one column per (item, NPC) offer, so phase 2 can prefer an NPC
  already on the tour.
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

**Definition.** The **key** of `v` is its moves plus the items it ends up holding:
`key(v) = supp(v) ∪ {i : (N·v)_i > 0}`. `v` is **atomic** if `cost·v > 0` and there is no recipe
`w ≠ 0` with `key(w) ⊊ key(v)`.

The leftover items have to be part of the key. "Buy Ore, shimmer to Gem, sell Gem" uses a subset
of the moves of "buy Ore, craft Bar, shimmer, craft Ring, sell Ring". On moves alone the Ring
recipe would look non-atomic, even though it can't be split into smaller recipes. With the
leftovers included, the two keys differ (Gem vs Ring is left over). This matches elementary flux
modes with an explicit outflow per item.

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
1. Solve 4.2. The result is recipe #1, with key `S = {e₁ … eₘ}` (moves and leftover items).
2. Split the rest of the search space into `m` disjoint sub-problems. Sub-problem `i` forbids `eᵢ`
   and requires `e₁ … eᵢ₋₁`. Each is one LP, warm-started from its parent in HiGHS.
   - For a move, "forbid" is an upper bound of 0, and "require" is a lower bound of ε. ε is well
     defined because spend is fixed to 1.
   - For a leftover item, "forbid" is `(N·v)_i = 0`, and "require" is a row bound
     `(N·v)_i ≥ ε / pᵢ`: ε copper's worth.
   - **Always split the region on its own optimum's key**, even when the cleaned-up recipe
     reported for it duplicates an earlier one. Skipping the split would lose the whole region.
3. Keep all open sub-problems in a priority queue by optimum. Pop the best one to get the next
   recipe, split it the same way, and repeat until K recipes are found.
4. **Required moves can yield a sum** of atomic recipes rather than a vertex. In that case,
   decompose it before reporting:
   1. Solve the LP restricted to its support to get an atomic part.
   2. Subtract as much of that part as possible.
   3. Repeat.

   Each part is a candidate. Deduplicate by key.
5. Sub-problems are independent, so they run in a process pool with one HiGHS instance per worker.

### 4.4 Constraints
Every user constraint is a bound or a row:

- **exclude item or move:** `v_t = 0` for columns touching it;
- **only these items:** drop the other columns;
- **must use X:** a row requiring some column that touches X;
- **first purchase in S:** drop the other buy columns;
- **limited stock** (Travelling Merchant): upper bound on the buy column. The recipe is then
  flagged *one-shot*, because it can't be repeated or compounded.
- **cap on places visited:** binary visit variables make it a MILP, still in HiGHS (6.2).

## 5. Phase 2: ordering and length

### Cost model
Only **travel** costs anything. Clicking, holding the mouse and hovering are free: holding the
mouse to buy or craft 100 of something is the same effort as one.

Assumptions (agreed):

- **Crafting is free anywhere.** The player uses pylons, Recall or a Magic Mirror, so every crafting
  station is one click away.
- **Selling happens at the happiest NPC present**, the one with the best sell price
  (`NpcSetup.best_sell_adjustment`).
- **Each distinct place visited costs 1** (weights configurable later if needed).

The places a loop can visit are the NPCs it buys from, the shimmer pool (only if it shimmers), and
the happiest NPC, to sell. Crafts need no place of their own, since they happen wherever the
player stands. That includes the pool, so **all crafts and shimmers fit in one pool visit**, however
they interleave.

### Length
A repeatable loop is a closed tour, so its length is the number of distinct places in it:

`L = |{NPCs bought from} ∪ {happiest NPC} ∪ {pool, if the recipe shimmers}|`

| loop | L |
|---|---|
| buy at Merchant, craft, sell to Merchant (the happiest) | 1 |
| buy at Merchant, shimmer, sell to Merchant | 2 (Merchant, pool) |
| buy at Arms Dealer and Merchant, shimmer, sell to Dryad | 4 |

`L` is a small integer and depends only on the support, so phase 1 can use it directly (6.2).

### Order
All buys (grouped by NPC) → travel to the pool → crafts and shimmers → travel to the happiest NPC
→ sell all. If the happiest NPC is also a seller, the loop starts and ends there.

**Inside the pool visit**, moves run in dependency order, each once at full quantity. Inventory
never goes negative, because every producer of an item runs before any consumer and `N·v ≥ 0`.
`search/quantize.simulate` replays the integer plan to verify this.

R1 in order:

| step | Ore | Bar | Gem | Ring | coins |
|---|---|---|---|---|---|
| buy 5 Ore | 5 | 0 | 0 | 0 | −500 |
| craft Bar | 2 | 1 | 0 | 0 | −500 |
| shimmer 2 Ore | 0 | 1 | 2 | 0 | −500 |
| craft Ring | 0 | 0 | 0 | 1 | −500 |
| sell all | 0 | 0 | 0 | 0 | +3500 |

**Cycles** (e.g. X → Y by shimmer, Y → X by craft) run in rounds at the pool. Rounds are free,
since they need no travel. A cycle still needs a first unit of X. If no move outside the cycle
produces an item of it:

- **seed:** buy that unit once and keep it for later loops, because a balanced cycle gives it
  back; or
- **reject** the recipe if nothing in the cycle can be bought.

### Buying from a pricier NPC
One column per (item, NPC) replaces "cheapest offer only". Buying from a slightly pricier NPC who
is already in the tour, such as the seller, can remove a whole trip and raise the score.

## 6. Ranking

### 6.1 Score
**Pending the user's choice.**

- The first proposal was `ln(1 + gain) / L`: exact growth per trip if proceeds are reinvested
  forever, but it felt too harsh on longer loops.
- The recommended score adds a per-loop overhead `c`:

  **score = ln(1 + gain) / (L + c)**

  - `c` is the fixed effort of running a loop at all: opening the inventory, walking to storage,
    teleporting home, and similar. It's a user setting.
  - `c = 0` gives pure compounding. A large `c` ranks by gain alone.
  - Repeating a loop k times doesn't change its score, as it should: k·ln(1+g) / k(L+c).
- `(1 + gain) / √L` was rejected. It rewards getting the stake back: a break-even loop with L = 1
  scores 1.0 and beats +30% with L = 2 (0.92). It also isn't stable under repetition.

Results report gain, `L`, and the score as "×m per trip", where `m = exp(score)`. One-shot recipes
(limited stock) are shown with their score, labelled as not repeatable.

### 6.2 Using L in phase 1
Any score that rises with gain and falls with `L` is maximized on the gain-vs-`L` Pareto front.
`L` counts places, so:

- add a binary "visits place q" per NPC and for the pool;
- for each column, require that its place is visited (for example `v_t ≤ M·y_q`);
- solve "best gain with `L ≤ 1, 2, 3, …`" as a MILP in HiGHS. Each gives one front point.

Lawler top-K (4.3) runs inside each `L` level. The final list is sorted by score. The score's
formula only affects this final sort, so changing it needs no re-solve.

### 6.3 Neutral catalogue (optional, per version)
A precomputed top list that the UI can show before any setup is entered. It covers:

- all shop NPCs present at neutral happiness, in any biome;
- one scenario per progression stage × world evil, about 20 scenarios.

Neutral happiness loses nothing. If every NPC shares one adjustment `a`, buys scale by `a` and sells
by `1/a`, so the gain ranking is unchanged. Only the numbers rescale.

| per version | top 100 per scenario | top 1,000 per scenario |
|---|---|---|
| time | ≈ 80 s single-threaded, ≈ 25 s on 4 cores | ≈ 10 min, ≈ 3 min on 4 cores |
| space | ≈ 0.2 MB | ≈ 2 MB |

Default: top 100, stored next to the version's `db.sqlite`. These estimates come from the synthetic
benchmark (section 8).

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
1. **Score formula** (6.1): `ln(1 + gain) / (L + c)` recommended, with the default `c` to be
   agreed.
2. **Travel cost between places.** Each place counts 1 for now. Per-place weights, for example
   NPCs without a pylon nearby, can come later.
3. **Numerical tolerances** for support detection (`v_t > ε`) with prices spanning 1 to 10⁷ copper.
   Scale columns by price.

Settled:

- movement-only cost;
- all crafting stations one click away;
- sell at the happiest NPC;
- cycles run in rounds at the pool (seed or reject);
- one buy column per (item, NPC);
- the Discount Card / Greedy Ring: −20% on buys, applied before happiness, and not stacking. The
  extractor's pricing probe records samples with and without it, so `shimcoins data info` checks
  the formula against the real game (`discount_match_rate`). **Measured on 1.4.5.8 (50/50
  samples exact):** all arithmetic is float32; the discount is a truncating cast
  (`(int)(value * 0.8f)`) applied *before* happiness; the happiness step is `Math.Round`
  (half-to-even) followed by a truncating cast; selling is `Math.Round(value / adjustment)`
  with the // 5 applied at the sale itself. Mirrored in `model/world.py` (`buy_price`,
  `sell_price`).

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
   - tour: places visited, `L`, the order buys → pool → sell;
   - dependency-order schedule inside the pool visit, checked with `simulate`;
   - cycles: rounds, seed, or reject;
   - score (6.1).
5. **CLI / API / UI:**
   - bring `shimcoins search` and `POST /api/search` back on the new engine;
   - options: `--top`, `--overhead c`, `--max-places`, constraints as today;
   - show gain, `L` and score per recipe, plus loop warnings;
   - the UI result card shows the ordered steps and the "sell all" residue.
6. **Tests:**
   - the toy world gives R1 (+7.0) as the best recipe;
   - the Bundle recipe keeps its 5 Junk;
   - a free-loop fixture is detected and banned;
   - Lawler top-K has no duplicate supports and ranks by gain;
   - decomposition returns atomic parts;
   - schedule replay never goes negative;
   - `L` counts places (the three examples in section 5);
   - score ranking puts the A-vs-B example the right way round;
   - a performance smoke test on the synthetic 5,500 × 9,400 model.
7. **Catalogue:** `shimcoins data catalogue <version>` (6.3).
8. **Docs:** README usage, and this file updated as questions get settled.
