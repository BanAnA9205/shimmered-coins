// A tiny stand-in for TerrariaServer.exe with the same type/member shapes the extractor reads
// (modelled on 1.4.4). Used only to test the extractor end to end without game files.
// Contents are invented. Build: mcs -out:TerrariaServer.exe MockTerraria.cs

using System;
using System.Collections.Generic;

namespace Terraria.ID
{
    public static class ItemID
    {
        public const short Count = 16;  // ids 1..15
        public static class Sets
        {
            //                                          0   1   2   3   4   5   6   7
            public static int[] ShimmerTransformToItem = { -1, -1, -1, -1, -1, 2, -1, -1,
                                                           -1, -1, -1, -1, -1, -1, -1, -1 };
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
        public static bool dayTime = true;
        public static bool bloodMoon;
        public static int moonPhase;
        public static int myPlayer = 255;
        public static Player[] player = new Player[256];
        public static NPC[] npc = new NPC[201];
        public static Player LocalPlayer { get { return player[myPlayer]; } }
        public static string versionNumber = "v9.9.9.9";
        public static Recipe[] recipe = new Recipe[10];
        public static GameContent.ShopHelper ShopHelper = new GameContent.ShopHelper();
    }

    public class NPC
    {
        public static bool downedBoss3;
        public static bool downedMoonlord;
        public static bool downedGolemBoss;
        public static bool downedMechBoss1, downedMechBoss2, downedMechBoss3;
        public int type;
        public bool active;

        public static bool AnyNPCs(int type)
        {
            foreach (var n in Main.npc) if (n != null && n.active && n.type == type) return true;
            return false;
        }
    }

    public static class WorldGen { public static bool crimson; }

    public static class Lang
    {
        static readonly string[] npcs = { "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Merchant" };
        public static string GetNPCNameValue(int id) { return id < npcs.Length ? npcs[id] : "NPC" + id; }
    }

    public class Item
    {
        static readonly string[] names = { "", "Iron Ore", "Iron Bar", "Chain", "Wood", "Lead Bar", "Chest", "Copper Coin",
            "Snow Globe", "Moon Charm", "Snow Pylon", "Guide Book", "Golf Trophy", "Quarter Charm", "Night Owl", "Mech Part" };
        static readonly int[] values = { 0, 250, 750, 60, 0, 700, 100, 1, 900, 800, 100000, 50, 5000, 400, 300, 2500 };
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

    public class LocalizedText { public string Value; }

    public class Condition
    {
        public LocalizedText Description;
        public Func<bool> Predicate;
        public Condition(string text, Func<bool> predicate) { Description = new LocalizedText { Value = text }; Predicate = predicate; }
        public bool IsMet() { return Predicate(); }

        public static readonly Condition Hardmode = new Condition("In Hardmode", () => Main.hardMode);
        public static readonly Condition PreHardmode = new Condition("Before Hardmode", () => !Main.hardMode);
        public static readonly Condition DownedMoonLord = new Condition("Moon Lord defeated", () => NPC.downedMoonlord);
        public static readonly Condition BloodMoon = new Condition("During a Blood Moon", () => Main.bloodMoon);
        public static readonly Condition NotBloodMoon = new Condition("Not during a Blood Moon", () => !Main.bloodMoon);
        public static readonly Condition InSnow = new Condition("In Snow", () => Main.LocalPlayer.ZoneSnow);
        public static readonly Condition TimeNight = new Condition("At night", () => !Main.dayTime);
        public static readonly Condition MoonPhaseFull = new Condition("During a full moon", () => Main.moonPhase == 0);
        public static readonly Condition MoonPhasesQuarters = new Condition("During a quarter moon", () => Main.moonPhase == 2 || Main.moonPhase == 6);
        public static readonly Condition HappyEnoughToSellPylons = new Condition("When the NPC is happy",
            () => Main.LocalPlayer.currentShoppingSettings.PriceAdjustment <= 0.8999999761581421);
        public static readonly Condition DownedMechBossAll = new Condition("All mechanical bosses defeated",
            () => NPC.downedMechBoss1 && NPC.downedMechBoss2 && NPC.downedMechBoss3);

        public static Condition NpcIsPresent(int type) { return new Condition("When NPC " + type + " is present", () => NPC.AnyNPCs(type)); }
        public static Condition GolfScoreOver(int score) { return new Condition("Golf score over " + score, () => Main.LocalPlayer.golferScoreAccumulated > score); }
    }

    public struct ShoppingSettings { public double PriceAdjustment; }

    public class Player
    {
        public ShoppingSettings currentShoppingSettings = new ShoppingSettings { PriceAdjustment = 1 };
        public int golferScoreAccumulated;
        public bool ZoneSnow { get; set; }
        public bool ZoneDesert { get; set; }
        public bool ZoneOverworldHeight { get; set; }

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

        // Old-style hard-coded shop (1.4.0-1.4.3 shape): shop 1 is the Merchant.
        public void SetupShop(int type)
        {
            int n = 0;
            if (type != 1) return;
            item[n++] = Item.Make(1, 1);
            if (Main.LocalPlayer.ZoneSnow) item[n++] = Item.Make(8, 1);
            else if (Main.LocalPlayer.ZoneDesert) item[n++] = Item.Make(9, 1);
            if (!Main.dayTime) item[n++] = Item.Make(14, 1);
            if (!Main.dayTime && Main.LocalPlayer.ZoneDesert) item[n++] = Item.Make(13, 1);
            if (Main.hardMode) item[n++] = Item.Make(2, 1);
        }
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
            for (int i = 0; i < Main.npc.Length; i++) Main.npc[i] = new NPC();
            Main.player[Main.myPlayer] = new Player();
            var bar = Item.Make(2, 1);
            bar.shopCustomPrice = 2000;
            AllShops.Clear();
            Func<int, Condition[], NPCShop.Entry> E = (t, cs) => new NPCShop.Entry { Item = Item.Make(t, 1), Conditions = new List<Condition>(cs) };
            AllShops.Add(new NPCShop
            {
                NpcType = 17,
                Entries = new List<NPCShop.Entry>
                {
                    E(1, new Condition[0]),
                    E(4, new[] { Condition.PreHardmode }),
                    new NPCShop.Entry { Item = bar, Conditions = new List<Condition> { Condition.Hardmode, Condition.NotBloodMoon } },
                    E(8, new[] { Condition.InSnow }),
                    E(9, new[] { Condition.MoonPhaseFull }),
                    E(13, new[] { Condition.MoonPhasesQuarters }),
                    E(10, new[] { Condition.InSnow, Condition.HappyEnoughToSellPylons }),
                    E(11, new[] { Condition.NpcIsPresent(22) }),
                    E(12, new[] { Condition.GolfScoreOver(500) }),
                    E(14, new[] { Condition.TimeNight }),
                    E(15, new[] { Condition.DownedMechBossAll }),
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
