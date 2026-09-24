# Dataset format (extractor → engine contract)

The extractor reads one Terraria build and writes a single JSON document. `shimcoins data build`
turns it into `data/<version>/db.sqlite`, which is the curated store the engine loads.
Extracted data is **never committed**: `/data/` is gitignored.

For a complete small example, see `python/shimmered_coins/demo/toy_world.json`. Its names and numbers are invented.

```jsonc
{
  "schema": 1,
  "version": "1.4.5.8",
  "meta": {"source": "TerrariaServer.exe", "source_sha256": "...", "extracted_at": "..."},

  "items": [
    // value: base value in copper. NPCs pay value/5 for it (adjusted by happiness).
    {"id": 1, "name": "Iron Pickaxe", "internal_name": "IronPickaxe",
     "value": 2000, "max_stack": 1, "is_coin": false, "sellable": true}
  ],

  // Recipe order matters: shimmer decrafting uses the first applicable recipe.
  "recipes": [
    {"id": 0, "result": 1, "result_stack": 1,
     "ingredients": [{"item": 22, "count": 12}, {"group": "Wood", "count": 3}],
     "stations": [16], "conditions": []}
  ],
  "groups": [{"name": "Wood", "items": [9, 619, 620], "icon_item": 9}],

  "npcs": [{"id": 17, "name": "Merchant", "has_shop": true}],

  // price: custom shop price in copper (omit to use the item value).
  // currency: "coins" or anything else (ignored by the engine).
  // limited: finite stock (Travelling Merchant, buy-once). Loops using it are not repeatable.
  "shops": [
    {"npc": 17, "item": 8, "price": null, "currency": "coins",
     "conditions": ["!hardmode"], "limited": false}
  ],

  // Emitted explicitly by the extractor. It applies the version's own rules: which recipe
  // decrafts, group icon items, world-evil variants, progression locks, not-decraftable flags.
  "shimmer": [
    {"kind": "transmute", "item": 3, "count": 1, "outputs": [[4, 1]], "conditions": []},
    {"kind": "decraft", "item": 1, "count": 1, "outputs": [[22, 12], [9, 3]],
     "conditions": ["!downedMoonLord"]}
  ]
}
```

## Conditions

Conditions are strings. `"x"` holds when world flag `x` is set, and `"!x"` holds when it is not.
A list of conditions means all of them must hold. The extractor chooses the flag names, and the
UI and CLI pass them as `--flag`. Suggested names:

| flag | meaning |
|------|---------|
| `hardmode` | Wall of Flesh defeated |
| `downed<Boss>` | e.g. `downedMoonLord`, `downedPlantBoss` (mirrors the game's `NPC.downed*` fields) |
| `crimson` / `corruption` | world evil |
| `night`, `bloodMoon`, `eclipse` | time and events |
| `moon:<phase>` | moon phase, e.g. `moon:full` |
| `biome:<name>` | the NPC's current biome, for shops that depend on it |

## Curation overrides

`shimcoins data build dump.json --overrides fixes.json` applies manual patches. See
`apply_overrides` in `python/shimmered_coins/data/curate.py` for the keys: `items`,
`remove_recipes`, `remove_shimmer`, `add_shimmer`, `remove_shops`.
