# shimmered-coins
an engine for searching (possibly constrained) optimal coin-producing loops in terraria using shimmer mechanic 

# Inspiration
https://www.youtube.com/watch?v=Bp3ih9SbIGE

# Purpose
With the introduction of the Shimmering mechanic in Terraria, many material loops have been found (and subsequently fixed in later updates).

# Status

Early MVP, now a crafting assistant in the making. **[`docs/STATUS.md`](docs/STATUS.md) is the
where-are-we summary**; the roadmap is in [`docs/PLAN.md`](docs/PLAN.md) and the engine design
in [`docs/ALGORITHM.md`](docs/ALGORITHM.md).

- [x] Dataset schema, SQLite storage, curation overrides, version diff ([`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md))
- [x] Move graph: buy / sell / craft / shimmer, with NPC happiness pricing and world conditions
- [x] Extractor for real game data (reads `TerrariaServer.exe`), see [`extractor/README.md`](extractor/README.md). `shimcoins data fetch` downloads a server build and runs it. Verified against a real 1.4.5.8 extraction: `data/1.4.5.8/db.sqlite`.
- [x] Web UI (local, Terraria-style): `shimcoins serve`
- [x] Search engine, recipe-based ([`docs/ALGORITHM.md`](docs/ALGORITHM.md)): HiGHS LPs over the item × move table find atomic recipes (with free-loop detection and Lawler top-K); phase 2 orders each into a tour and ranks by growth per trip. Works on real data.

# Quick start

```sh
pip install -e ".[dev]"
pytest

# get real game data: download the (free) dedicated server, extract, build the db
shimcoins data fetch           # picks a version, confirms, cleans up after itself
shimcoins data info data/1.4.5.8

# plan from a world's chest items (the pivot search; --farm adds EV farming plans)
shimcoins plan data/1.4.5.8 --world ~/.local/share/Terraria/Worlds/joe.wld --all-npcs 0.75 --top 5

# search the shop economy from scratch
shimcoins search data/1.4.5.8 --all-npcs 0.75 --flag hardmode --top 5

# the bundled toy world (invented data) works everywhere a dataset does
shimcoins search python/shimmered_coins/demo/toy_world.json --all-npcs 1 --flag downedBoss
```

## Web UI

```sh
pip install -e ".[api]"
(cd web && npm install && npm run build)
shimcoins serve            # http://127.0.0.1:8765, datasets from ./data (plus the demo world)
```

For UI development, run `npm run dev` inside `web/` while `shimcoins serve` is running. The dev server proxies `/api` to it.

- **Font:** put a Terraria-style font at `web/public/fonts/terraria.ttf`. It's gitignored and used automatically.
- **Item sprites:** the UI's **Sprites** tab scans for your Terraria/tModLoader install
  automatically (or `shimcoins sprites` on the CLI) and imports the game's `Item_*.xnb` art —
  XNA LZX and MonoGame LZ4 XNBs both decode — into `web/public/sprites/`. Set
  `$SHIMCOINS_TERRARIA` if Steam lives somewhere unusual. Sprites are Re-Logic's art: they stay
  local and gitignored.

## How the search works

See [`docs/ALGORITHM.md`](docs/ALGORITHM.md). In short:

- **Phase 1:** moves form a sparse item × move table. The search finds the best *atomic
  recipes*: buy/craft/shimmer quantities that can't be split into smaller profitable pieces. It
  solves linear programs with HiGHS instead of walking move sequences. Free-money loops
  (duplication exploits) are detected first and reported separately.
- **Phase 2:** each top recipe becomes a tour: buy at each NPC, one shimmer-pool visit for every
  craft and shimmer, sell all at the happiest NPC. Only travel counts: `L` = places visited.
- **Ranking:** by growth per trip, `score = ln(1 + gain) / (L + c)` with the `--overhead` `c`.

The options below describe the world and NPC setup:

| option | effect |
|--------|--------|
| `--npc Merchant=0.85@snow` | present NPC, its happiness price adjustment (0.75 happiest .. 1.5) and the biome(s) it lives in (`@any`: try every biome) |
| `--all-npcs 1` | every shop NPC present at the given adjustment |
| `--flag hardmode` | world flags (progression, world evil, events, ...) |
| `--include`, `--exclude`, `--must-use`, `--start` | constrained search on specific items |
| `--kinds craft,shimmer` | allowed move kinds (selling is always implicit) |
| `--top N`, `--overhead C`, `--max-places K` | how many recipes, the score's per-loop overhead, and a cap on tour length |
| `--allow-loss` | also report the least-bad recipes when nothing is profitable |
| `--wallet "50g"` | drop recipes you can't afford, scale the rest to your wallet |
| `--json` | machine-readable output |
