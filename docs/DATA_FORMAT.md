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
  // limited: buy-once stock. Kept in the data; the search leaves these entries out.
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

Conditions are strings. Prefix one with `!` to negate it. A list of conditions means all of them
must hold. When a shop item is available in several alternative situations (for example, "moon
phase 2 or 6"), the extractor writes one shop entry per alternative.

The prefix decides what a condition is checked against:

| form | scope | checked against |
|------|-------|-----------------|
| `zone:<biome>` (`zone:snow`, `zone:desert`, `zone:surface`, …) | `zone` | the zones of the **NPC selling the item**. This is where you stand when you talk to it. |
| `happy<=<adj>` | `happiness` | that NPC's happiness price adjustment (pylons) |
| `npc:<id>` | `npc_present` | whether that NPC lives in town |
| `need:<thing>` | `craft_location` | crafting next to water, in snow, … These are assumed reachable. |
| `player:<slug>` | `player` | player state no probe could explain (golf score, bestiary %, …). This is a flag and off by default. |
| anything else (`hardmode`, `downedmoonlord`, `daytime`, `moon:3`, `bloodmoon`, `crimson`, `mode:expert`, seeds, …) | `world` | the world flags you set |

Matching is case-insensitive.

### Condition catalog

The optional `conditions` array describes each condition name (without `!`):

```jsonc
"conditions": [
  {"name": "zone:snow", "scope": "zone", "description": "In Snow", "depends": ["zone:snow"]},
  {"name": "happy<=0.9", "scope": "happiness", "description": "When the NPC is happy", "depends": ["happy"]}
]
```

The extractor builds this catalog by probing. For each game `Condition`, and for every item of a
pre-1.4.4 hard-coded shop, it changes one piece of game state at a time and records what changes
the result. For conditions that are still false, it also tries changing two pieces at once. The
state it changes: progression, time, moon phase, events, seeds, difficulty, the player's biome
zones, which other NPCs are present, and happiness. The UI uses the catalog to group flags and to
list the biomes an NPC can live in.

## Curation overrides

`shimcoins data build dump.json --overrides fixes.json` applies manual patches. See
`apply_overrides` in `python/shimmered_coins/data/curate.py` for the keys: `items`,
`remove_recipes`, `remove_shimmer`, `add_shimmer`, `remove_shops`.
