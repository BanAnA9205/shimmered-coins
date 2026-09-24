// A tiny stand-in for TerrariaServer.exe with the same type/member shapes the extractor reads
// (modelled on 1.4.4). Used only to test the extractor end to end without game files.
// Contents are invented. Build: mcs -out:TerrariaServer.exe MockTerraria.cs

using System;
using System.Collections.Generic;

namespace Terraria.ID
{
    public static class ItemID
    {
        public const short Count = 8;  // ids 1..7
        public static class Sets
        {
            //                                          0   1   2   3   4   5   6   7
            public static int[] ShimmerTransformToItem = { -1, -1, -1, -1, -1, 2, -1, -1 };
        }
    }

    public static class NPCID { public const short Count = 30; }
}

namespace Terraria.Localization
{
    public class LanguageManager
    {
        public static LanguageManager Instance = new LanguageManager();
        public string Culture;
        public void SetLanguage(string culture) { Culture = culture; }
    }
}

namespace Terraria
{
    public static class Main
    {
        public static bool dedServ;
        public static bool hardMode;
        public static string versionNumber = "v9.9.9.9";
        public static Recipe[] recipe = new Recipe[10];
        public static GameContent.ShopHelper ShopHelper = new GameContent.ShopHelper();
    }

    public static class NPC
    {
        public static bool downedBoss3;
        public static bool downedMoonlord;
        public static bool downedGolemBoss;
    }

    public static class WorldGen { public static bool crimson; }

    public static class Lang
    {
        static readonly string[] npcs = { "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Merchant" };
        public static string GetNPCNameValue(int id) { return id < npcs.Length ? npcs[id] : "NPC" + id; }
    }

    public class Item
    {
        static readonly string[] names = { "", "Iron Ore", "Iron Bar", "Chain", "Wood", "Lead Bar", "Chest", "Copper Coin" };
        static readonly int[] values = { 0, 250, 750, 60, 0, 700, 100, 1 };
        public int type, stack = 1, maxStack = 1, value;
        public int? shopCustomPrice;
        public int shopSpecialCurrency = -1;
        public bool buyOnce;
        public string Name { get { return names[type]; } }
        public bool IsACoin { get { return type == 7; } }

        public void SetDefaults(int Type, bool noMatCheck = false)
        {
            type = Type;
            value = values[Type];
            maxStack = Type == 6 ? 1 : 9999;
        }

        public static Item Make(int type, int stack) { var i = new Item(); i.SetDefaults(type); i.stack = stack; return i; }
    }

    public class Recipe
    {
        public static int numRecipes;
        public Item createItem = new Item();
        public List<Item> requiredItem = new List<Item>();
        public List<int> requiredTile = new List<int>();
        public List<int> acceptedGroups = new List<int>();
        public List<Item> customShimmerResults;
        public bool needWater;
        public bool crimson;
        public bool notDecraftable;

        public static void SetupRecipes()
        {
            numRecipes = 0;
            Add(Item.Make(2, 1), new[] { Item.Make(1, 3) }, 17);                      // 0: bar <- 3 ore
            Add(Item.Make(3, 10), new[] { Item.Make(2, 1) }, 16);                     // 1: 10 chain <- bar
            var chest = Add(Item.Make(6, 1), new[] { Item.Make(4, 8), Item.Make(2, 2) }, 18);  // 2: chest <- 8 wood(group) + 2 bar
            chest.acceptedGroups.Add(0);
            var alt = Add(Item.Make(3, 10), new[] { Item.Make(5, 1) }, 16);           // 3: alt chain recipe (crimson)
            alt.crimson = true;
        }

        static Recipe Add(Item result, Item[] ings, int tile)
        {
            var r = new Recipe { createItem = result };
            r.requiredItem.AddRange(ings);
            r.requiredTile.Add(tile);
            Main.recipe[numRecipes++] = r;
            return r;
        }
    }

    public class RecipeGroup
    {
        public static Dictionary<string, int> recipeGroupIDs = new Dictionary<string, int> { { "Wood", 0 } };
        public static Dictionary<int, RecipeGroup> recipeGroups = new Dictionary<int, RecipeGroup>
        {
            { 0, new RecipeGroup { IconicItemId = 4, ValidItems = new HashSet<int> { 4 } } },
        };
        public int IconicItemId;
        public HashSet<int> ValidItems;
    }

    public class Condition
    {
        public static readonly Condition Hardmode = new Condition();
        public static readonly Condition PreHardmode = new Condition();
        public static readonly Condition DownedMoonLord = new Condition();
        public static readonly Condition BloodMoon = new Condition();
        public static readonly Condition NotBloodMoon = new Condition();
    }

    public class Player
    {
        // Mirrors the real signature; the mock formula is deliberately simple.
        public void GetItemExpectedPrice(Item item, out long calcForSelling, out long calcForBuying)
        {
            float adj = Main.ShopHelper.GetShoppingSettings_Adjustment();
            calcForBuying = (long)Math.Round(item.value * adj);
            calcForSelling = (long)Math.Round(item.value / adj);
        }
    }

    public class Chest
    {
        public Item[] item = new Item[40];
        public Chest(bool bank = false) { }
    }
}

namespace Terraria.GameContent
{
    public class ShopHelper
    {
        float _currentPriceAdjustment = 1f;
        public float GetShoppingSettings_Adjustment() { return _currentPriceAdjustment; }
    }

    public class NPCShop
    {
        public class Entry
        {
            public Item Item { get; set; }
            public List<Condition> Conditions { get; set; }
        }
        public int NpcType { get; set; }
        public List<Entry> Entries { get; set; }
    }

    public static class NPCShopDatabase
    {
        public static List<NPCShop> AllShops = new List<NPCShop>();

        public static void Initialize()
        {
            var bar = Item.Make(2, 1);
            bar.shopCustomPrice = 2000;
            AllShops.Clear();
            AllShops.Add(new NPCShop
            {
                NpcType = 17,
                Entries = new List<NPCShop.Entry>
                {
                    new NPCShop.Entry { Item = Item.Make(1, 1), Conditions = new List<Condition>() },
                    new NPCShop.Entry { Item = Item.Make(4, 1), Conditions = new List<Condition> { Condition.PreHardmode } },
                    new NPCShop.Entry { Item = bar, Conditions = new List<Condition> { Condition.Hardmode, Condition.NotBloodMoon } },
                },
            });
        }
    }

    public static class ShimmerTransforms
    {
        public static void UpdateRecipeSets() { }

        public static int GetDecraftingRecipeIndex(int type)
        {
            if (type == 2) return 0;
            if (type == 3) return WorldGen.crimson ? 3 : 1;
            if (type == 6) return 2;
            return -1;
        }

        public static bool IsItemTransformLocked(int type)
        {
            if (type == 6) return !NPC.downedBoss3;  // chest decraft needs Skeletron
            if (type == 5) return !NPC.downedMoonlord;  // lead bar -> iron bar needs Moon Lord
            return false;
        }
    }
}
