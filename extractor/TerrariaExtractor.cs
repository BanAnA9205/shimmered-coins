// TerrariaExtractor: dumps items, recipes, shops and shimmer rules from a Terraria server build
// into the dataset JSON described in docs/DATA_FORMAT.md.
//
// Everything is done through reflection at runtime, so this compiles without any game files and
// tolerates member renames between versions (each section logs and continues on failure).
//
//   Windows:  TerrariaExtractor.exe  "C:\...\Terraria\TerrariaServer.exe"  --out dump.json
//   Linux:    mono TerrariaExtractor.exe  ./TerrariaServer.exe  --out dump.json
//   Probe:    ... --probe       (prints what the build exposes; paste this when something breaks)
//
// The extracted data is Re-Logic's: keep it local, don't redistribute it.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;

static partial class Program
{
    const BindingFlags ANY = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;
    const BindingFlags STATIC = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
    const BindingFlags INST = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

    static Assembly Game;
    static string GameDir;
    static string TempDir;
    static readonly List<string> Warnings = new List<string>();

    static int Main(string[] args)
    {
        string path = null, outPath = "dump.json", version = null;
        bool probe = false;
        bool forceLegacy = false;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--out") outPath = args[++i];
            else if (args[i] == "--version") version = args[++i];
            else if (args[i] == "--probe") probe = true;
            else if (args[i] == "--legacy-shops") forceLegacy = true;
            else path = args[i];
        }
        if (path == null)
        {
            Console.Error.WriteLine("usage: TerrariaExtractor <TerrariaServer.exe> [--out dump.json] [--version 1.4.5.8] [--probe] [--legacy-shops]");
            return 2;
        }
        path = Path.GetFullPath(path);
        GameDir = Path.GetDirectoryName(path);
        Directory.SetCurrentDirectory(GameDir);
        AppDomain.CurrentDomain.AssemblyResolve += Resolve;
        Game = Assembly.LoadFrom(path);
        Log("loaded " + Game.FullName);

        Init();
        if (probe) { Probe(); return 0; }

        var j = new Json();
        j.Obj();
        j.Key("schema").Val(1);
        j.Key("version").Val(version ?? GameVersion());
        j.Key("meta").Obj();
        j.Key("source").Val(Path.GetFileName(path));
        j.Key("source_sha256").Val(Sha256(path));
        j.Key("extracted_at").Val(DateTime.UtcNow.ToString("o"));
        j.Key("extractor").Val("TerrariaExtractor/2");
        Section("pricing_probe", j, () => PricingProbe(j));
        j.Key("warnings").Arr(); foreach (var w in Warnings) j.Val(w); j.End();
        j.End();

        Section("items", j, () => Items(j));
        Section("groups", j, () => Groups(j));
        Section("recipes", j, () => Recipes(j));
        Section("npcs+shops", j, () => NpcsAndShops(j, forceLegacy));
        Section("shimmer", j, () => Shimmer(j));
        WriteCatalog(j);
        j.End();

        File.WriteAllText(outPath, j.ToString(), new UTF8Encoding(false));
        Log("wrote " + outPath + (Warnings.Count > 0 ? " with " + Warnings.Count + " warnings" : ""));
        foreach (var w in Warnings) Log("  warning: " + w);
        return 0;
    }

    // ------------------------------------------------------------------ bootstrapping --------

    static Assembly Resolve(object sender, ResolveEventArgs e)
    {
        var name = new AssemblyName(e.Name).Name;
        foreach (var ext in new[] { ".dll", ".exe" })
        {
            var f = Path.Combine(GameDir, name + ext);
            if (File.Exists(f)) return Assembly.LoadFrom(f);
        }
        if (Game != null)  // Terraria embeds ReLogic etc. as manifest resources
        {
            foreach (var res in Game.GetManifestResourceNames())
            {
                if (res.EndsWith(name + ".dll", StringComparison.OrdinalIgnoreCase))
                {
                    using (var s = Game.GetManifestResourceStream(res))
                    {
                        var buf = new byte[s.Length];
                        s.Read(buf, 0, buf.Length);
                        return Assembly.Load(buf);
                    }
                }
            }
        }
        return null;
    }

    static void Init()
    {
        // The real game sets Program.SavePath in its own bootstrap before anything static in
        // Terraria.Main runs: Main's static constructor does Path.Combine(Program.SavePath, ...).
        // Without it, Main's cctor throws and the type is poisoned for the rest of the run
        // (TypeInitializationException on every Main member, including Main.recipe).
        TempDir = Path.Combine(Path.GetTempPath(), "shimcoins-extract-" + Path.GetRandomFileName());
        Directory.CreateDirectory(TempDir);
        Try("Program.SavePath", () => SetStatic("Terraria.Program", "SavePath", TempDir));
        Try("dedServ", () => SetStatic("Terraria.Main", "dedServ", true));
        Try("language", () =>
        {
            var lm = GetStatic("Terraria.Localization.LanguageManager", "Instance");
            var m = lm.GetType().GetMethods(ANY).FirstOrDefault(x => x.Name == "SetLanguage"
                && x.GetParameters().Length == 1 && x.GetParameters()[0].ParameterType == typeof(string));
            m.Invoke(lm, new object[] { "en-US" });
        });
        // 1.4.5+: Lang's legacy arrays (Lang.misc, ...) stay null until this runs; recipe group
        // setup and item SetDefaults read them.
        Try("Lang.InitializeLegacyLocalization", () => CallStatic("Terraria.Lang", "InitializeLegacyLocalization"));
        object main = null;
        Try("new Main()", () => { main = Activator.CreateInstance(T("Terraria.Main")); });
        bool full = false;
        if (main != null)
            full = Try("Main.Initialize_AlmostEverything", () => CallInst(main, "Initialize_AlmostEverything"));
        if (!full)
        {
            // Fallback: the pieces we need, in dependency order.
            Try("stand-ins", StandIns);
            Try("Recipe.SetupRecipes", () => CallStatic("Terraria.Recipe", "SetupRecipes"));
            if (Convert.ToInt32(GetStatic("Terraria.Recipe", "numRecipes")) == 0)
            {
                // 1.4.5+ split group setup out of SetupRecipes (which dies on RequireGroup
                // without it); on 1.4.4 SetupRecipes registers the groups itself, so calling
                // this first would double-register them.
                Try("Recipe.SetupRecipeGroups", () => CallStatic("Terraria.Recipe", "SetupRecipeGroups"));
                Try("ContentSamples.Initialize", () => CallStatic("Terraria.ID.ContentSamples", "Initialize"));
                Try("Recipe.SetupRecipes (retry)", () => CallStatic("Terraria.Recipe", "SetupRecipes"));
            }
            Try("NPCShopDatabase.Initialize", () => CallStatic("Terraria.GameContent.NPCShopDatabase", "Initialize"));
        }
        Try("trim recipe table", () => TrimRecipeTable());
        Try("ShimmerTransforms.UpdateRecipeSets", () => CallStatic("Terraria.GameContent.ShimmerTransforms", "UpdateRecipeSets"));
        var n = Convert.ToInt32(GetStatic("Terraria.Recipe", "numRecipes"));
        if (n == 0) Try("Recipe.SetupRecipes (retry)", () => CallStatic("Terraria.Recipe", "SetupRecipes"));
        Log("recipes: " + GetStatic("Terraria.Recipe", "numRecipes"));
    }

    // The game has these by the time any content code runs; a headless extraction must add them
    // itself or NullReferenceExceptions appear deep inside item/projectile/shop code.
    static void StandIns()
    {
        var players = (Array)GetStatic("Terraria.Main", "player");
        int my = Convert.ToInt32(GetStatic("Terraria.Main", "myPlayer"));
        if (my < 0 || my >= players.Length) { my = 0; SetStatic("Terraria.Main", "myPlayer", 0); }
        if (players.GetValue(my) == null)
            players.SetValue(Activator.CreateInstance(T("Terraria.Player")), my);
        if (GetStatic("Terraria.Main", "rand") == null)
            SetStatic("Terraria.Main", "rand", Activator.CreateInstance(T("Terraria.Utilities.UnifiedRandom")));
        var npcs = (Array)GetStatic("Terraria.Main", "npc");
        for (int i = 0; i < npcs.Length; i++)
            if (npcs.GetValue(i) == null) npcs.SetValue(Activator.CreateInstance(T("Terraria.NPC")), i);
    }

    // SetupRecipes leaves Main.recipe with nulls past numRecipes (the array is sized to
    // maxRecipes); 1.4.5's MapArray-based helpers (ShimmerTransforms.UpdateRecipeSets) walk the
    // whole array and choke on them.
    static void TrimRecipeTable()
    {
        var n = Convert.ToInt32(GetStatic("Terraria.Recipe", "numRecipes"));
        var arr = (Array)GetStatic("Terraria.Main", "recipe");
        if (arr == null || arr.Length <= n || n == 0) return;
        var trimmed = Array.CreateInstance(arr.GetType().GetElementType(), n);
        Array.Copy(arr, trimmed, n);
        SetStatic("Terraria.Main", "recipe", trimmed);
    }

    static string GameVersion()
    {
        try { return Convert.ToString(GetStatic("Terraria.Main", "versionNumber")).TrimStart('v'); }
        catch { return Game.GetName().Version.ToString(); }
    }

    // ------------------------------------------------------------------ items ----------------

    static int ItemCount() { return Convert.ToInt32(GetStatic("Terraria.ID.ItemID", "Count")); }

    static object NewItem(int id)
    {
        var it = Activator.CreateInstance(T("Terraria.Item"));
        var sd = it.GetType().GetMethods(INST).Where(m => m.Name == "SetDefaults")
            .OrderBy(m => m.GetParameters().Length).First(m => m.GetParameters().Length >= 1
                && m.GetParameters()[0].ParameterType == typeof(int));
        var ps = sd.GetParameters();
        var argv = new object[ps.Length];
        argv[0] = id;
        for (int i = 1; i < ps.Length; i++) argv[i] = ps[i].HasDefaultValue ? ps[i].DefaultValue : Default(ps[i].ParameterType);
        sd.Invoke(it, argv);
        return it;
    }

    static readonly Dictionary<int, string> ItemNames = new Dictionary<int, string>();

    static void Items(Json j)
    {
        int count = ItemCount();
        object search = null;
        try { search = GetStatic("Terraria.ID.ItemID", "Search"); } catch { }
        j.Key("items").Arr();
        for (int id = 1; id < count; id++)
        {
            object it;
            try { it = NewItem(id); }
            catch (Exception e) { Warn("item " + id + ": " + Inner(e).Message); continue; }
            string name = Convert.ToString(Get(it, "Name"));
            string internalName = "";
            if (search != null) try { internalName = Convert.ToString(CallInst(search, "GetName", id)); } catch { }
            if (string.IsNullOrEmpty(name)) name = internalName;
            ItemNames[id] = name;
            int value = Convert.ToInt32(Get(it, "value"));
            bool coin = id >= 71 && id <= 74;
            try { coin = (bool)Get(it, "IsACoin"); } catch { }
            j.Obj();
            j.Key("id").Val(id);
            j.Key("name").Val(name);
            j.Key("internal_name").Val(internalName);
            j.Key("value").Val(value);
            j.Key("max_stack").Val(Convert.ToInt32(Get(it, "maxStack")));
            j.Key("is_coin").Val(coin);
            j.Key("sellable").Val(value > 0 && !coin);
            j.End();
        }
        j.End();
        Log("items: " + (count - 1));
    }

    // ------------------------------------------------------------------ recipes --------------

    static readonly Dictionary<int, string> GroupNames = new Dictionary<int, string>();
    static readonly Dictionary<int, object> GroupsById = new Dictionary<int, object>();

    static void Groups(Json j)
    {
        // 1.4.4: recipeGroupIDs maps name -> id. 1.4.5 dropped it; the name comes from the
        // group's own text (a localization key) instead.
        IDictionary ids = null;
        try { ids = (IDictionary)GetStatic("Terraria.RecipeGroup", "recipeGroupIDs"); } catch { }
        if (ids != null)
            foreach (DictionaryEntry e in ids) GroupNames[Convert.ToInt32(e.Value)] = Convert.ToString(e.Key);
        var groups = (IDictionary)GetStatic("Terraria.RecipeGroup", "recipeGroups");
        j.Key("groups").Arr();
        foreach (DictionaryEntry e in groups)
        {
            int gid = Convert.ToInt32(e.Key);
            GroupsById[gid] = e.Value;
            string name;
            if (!GroupNames.TryGetValue(gid, out name)) { name = GroupName(e.Value) ?? "group" + gid; GroupNames[gid] = name; }
            j.Obj();
            j.Key("name").Val(name);
            j.Key("items").Arr();
            foreach (var v in (IEnumerable)Get(e.Value, "ValidItems")) j.Val(Convert.ToInt32(v));
            j.End();
            j.Key("icon_item").Val(GroupIcon(e.Value));
            j.End();
        }
        j.End();
    }

    static string GroupName(object g)
    {
        try
        {
            var text = ((Delegate)Get(g, "GetText")).DynamicInvoke();
            // 1.4.4: LocalizedText (use its Key); 1.4.5: the display string itself.
            if (text is string) return (string)text;
            var key = Convert.ToString(Get(text, "Key"));
            return string.IsNullOrEmpty(key) ? null : key;
        }
        catch { return null; }
    }

    static int GroupIcon(object g)
    {
        foreach (var member in new[] { "IconicItemId", "DecraftItemId" })
            try { return Convert.ToInt32(Get(g, member)); } catch { }
        foreach (var v in (IEnumerable)Get(g, "ValidItems")) return Convert.ToInt32(v);
        return 0;
    }

    static IEnumerable<object> Seq(object listOrArray)
    {
        if (listOrArray == null) yield break;
        foreach (var x in (IEnumerable)listOrArray) yield return x;
    }

    static List<object> RecipeTable()
    {
        int n = Convert.ToInt32(GetStatic("Terraria.Recipe", "numRecipes"));
        var arr = (Array)GetStatic("Terraria.Main", "recipe");
        var list = new List<object>();
        for (int i = 0; i < n; i++) list.Add(arr.GetValue(i));
        return list;
    }

    static List<int> AcceptedGroups(object r)
    {
        var res = new List<int>();
        try { foreach (var g in Seq(Get(r, "acceptedGroups"))) { int v = Convert.ToInt32(g); if (v >= 0) res.Add(v); } } catch { }
        return res;
    }

    static void Recipes(Json j)
    {
        var table = RecipeTable();
        j.Key("recipes").Arr();
        for (int idx = 0; idx < table.Count; idx++)
        {
            var r = table[idx];
            var create = Get(r, "createItem");
            int result = Convert.ToInt32(Get(create, "type"));
            if (result <= 0) continue;
            var groups = AcceptedGroups(r);
            j.Obj();
            j.Key("id").Val(idx);
            j.Key("result").Val(result);
            j.Key("result_stack").Val(Convert.ToInt32(Get(create, "stack")));
            j.Key("ingredients").Arr();
            foreach (var ing in Seq(Get(r, "requiredItem")))
            {
                int type = Convert.ToInt32(Get(ing, "type"));
                if (type <= 0) continue;
                int stack = Convert.ToInt32(Get(ing, "stack"));
                string grp = null;
                foreach (var gid in groups)
                {
                    object g;
                    if (!GroupsById.TryGetValue(gid, out g)) continue;
                    if (GroupIcon(g) == type || Contains(Get(g, "ValidItems"), type))
                    { grp = GroupNames[gid]; break; }
                }
                j.Obj();
                if (grp != null) j.Key("group").Val(grp); else j.Key("item").Val(type);
                j.Key("count").Val(stack);
                j.End();
            }
            j.End();
            j.Key("stations").Arr();
            foreach (var t in StationTiles(r)) j.Val(t);
            j.End();;
            j.Key("conditions").Arr();
            foreach (var c in RecipeConditions(r)) j.Val(c);
            j.End();
            j.End();
        }
        j.End();
    }

    // 1.4.4: int[] requiredTile. 1.4.5: a single int.
    static IEnumerable<int> StationTiles(object r)
    {
        var rt = Get(r, "requiredTile");
        if (rt is IEnumerable && !(rt is string))
        {
            foreach (var t in (IEnumerable)rt) { int v = Convert.ToInt32(t); if (v >= 0) yield return v; }
            yield break;
        }
        var single = Convert.ToInt32(rt);
        if (single >= 0) yield return single;
    }

    static IEnumerable<string> RecipeConditions(object r)
    {
        // need* flags (water, honey, lava, snow biome, graveyard, ...) and world-evil flags
        foreach (var f in r.GetType().GetFields(INST))
        {
            if (f.FieldType != typeof(bool) || !(bool)f.GetValue(r)) continue;
            var n = f.Name;
            if (n.StartsWith("need", StringComparison.Ordinal))
            {
                var lit = "need:" + n.Substring(4).ToLowerInvariant();
                Register(lit, "craft_location", "Crafted " + n.Substring(4), null);
                yield return lit;
            }
            else if (n == "crimson") yield return "crimson";
            else if (n == "corruption") yield return "!crimson";
        }
        // 1.4.4+: List<Condition> Conditions. Recipes can't hold alternatives, so the first way
        // the conditions can hold is used (location conditions are treated as reachable anyway).
        object conds = null;
        try { conds = Get(r, "Conditions"); } catch { }
        var list = Seq(conds).ToList();
        if (list.Count > 0)
            foreach (var lit in Expand(list, 1)[0])
                yield return lit.StartsWith("zone:", StringComparison.Ordinal) ? "need:" + lit.Substring(5) : lit;
    }

    // ------------------------------------------------------------------ npcs & shops ---------

    static void NpcsAndShops(Json j, bool forceLegacy)
    {
        var shopNpcs = new SortedSet<int>();
        var entries = new List<string[]>();  // npc, item, price, currency, conditions(json), limited
        bool modern = false;
        try
        {
            if (!forceLegacy) Try("NPCShopDatabase", () => { modern = ModernShops(shopNpcs, entries); });
            if (!modern) Try("Chest.SetupShop (legacy)", () => LegacyShops(shopNpcs, entries));
        }
        finally { RestoreKnobs(); }
        Log("condition probing: " + probeEvaluations + " evaluations, " + Catalog.Count + " condition names");

        int npcCount = Convert.ToInt32(GetStatic("Terraria.ID.NPCID", "Count"));
        j.Key("npcs").Arr();
        foreach (var id in shopNpcs)
        {
            if (id <= 0 || id >= npcCount) continue;
            j.Obj();
            j.Key("id").Val(id);
            j.Key("name").Val(NpcName(id));
            j.Key("has_shop").Val(true);
            j.End();
        }
        j.End();
        j.Key("shops").Arr();
        foreach (var e in entries)
        {
            j.Obj();
            j.Key("npc").Val(int.Parse(e[0]));
            j.Key("item").Val(int.Parse(e[1]));
            if (e[2] != null) j.Key("price").Val(int.Parse(e[2]));
            j.Key("currency").Val(e[3]);
            j.Key("conditions").Raw(e[4]);
            j.Key("limited").Val(e[5] == "1");
            j.End();
        }
        j.End();
        Log("shop entries: " + entries.Count);
    }

    static string NpcName(int id)
    {
        try { return Convert.ToString(CallStatic("Terraria.Lang", "GetNPCNameValue", id)); }
        catch { return "npc" + id; }
    }

    static string[] Entry(int npc, object item, IEnumerable<string> conds, bool limited)
    {
        int type = Convert.ToInt32(Get(item, "type"));
        string price = null;
        try { var cp = Get(item, "shopCustomPrice"); if (cp != null) price = Convert.ToString(cp); } catch { }
        string currency = "coins";
        try { int cur = Convert.ToInt32(Get(item, "shopSpecialCurrency")); if (cur != -1) currency = "special:" + cur; } catch { }
        try { if ((bool)Get(item, "buyOnce")) limited = true; } catch { }
        var cj = "[" + string.Join(",", conds.Distinct().Select(Json.Quote)) + "]";
        return new[] { npc.ToString(), type.ToString(), price, currency, cj, limited ? "1" : "0" };
    }

    static bool ModernShops(SortedSet<int> npcs, List<string[]> entries)
    {
        var db = T("Terraria.GameContent.NPCShopDatabase");
        if (db == null) return false;  // 1.4.5 went back to hard-coded Chest.SetupShop
        IEnumerable shops = null;
        try { shops = (IEnumerable)GetStatic(db, "AllShops"); } catch { }
        if (shops == null)
        {
            var dict = db.GetFields(STATIC).FirstOrDefault(f => typeof(IDictionary).IsAssignableFrom(f.FieldType));
            if (dict == null) return false;
            shops = ((IDictionary)dict.GetValue(null)).Values;
        }
        var list = shops.Cast<object>().ToList();
        foreach (var shop in list) shopNpcIds.Add(Convert.ToInt32(Get(shop, "NpcType")));
        int n = 0;
        foreach (var shop in list)
        {
            int npc = Convert.ToInt32(Get(shop, "NpcType"));
            object es = null;
            try { es = Get(shop, "Entries"); } catch { }
            if (es == null) { Warn("shop without fixed entries (random stock?): npc " + npc + " " + shop.GetType().Name); continue; }
            npcs.Add(npc);
            foreach (var e in (IEnumerable)es)
            {
                var item = Get(e, "Item");
                // one entry per way the conditions can hold (e.g. "moon phase 2 or 6")
                foreach (var conds in Expand(Seq(Get(e, "Conditions")), 32))
                {
                    entries.Add(Entry(npc, item, conds, false));
                    n++;
                }
            }
        }
        return n > 0;
    }

    // Pre-1.4.4 shops are hard-coded in Chest.SetupShop(int shopIndex). We evaluate them under a
    // few world states and derive conditions from which flags make an item (dis)appear.
    static readonly int[] LegacyShopNpc = {
        0, 17, 19, 20, 38, 54, 107, 108, 124, 142, 160, 178, 207, 208, 209, 227, 228, 229, 353, 368, 453, 550, 588, 633, 663 };

    static object NewShopChest()
    {
        // 1.4.4: new Chest(bank). 1.4.5: the ctor is gone; Chest.CreateShop() makes a shop chest.
        try { return Activator.CreateInstance(T("Terraria.Chest"), new object[] { false }); } catch { }
        try { return CallStatic("Terraria.Chest", "CreateShop"); } catch { }
        return Activator.CreateInstance(T("Terraria.Chest"));
    }

    static void LegacyShops(SortedSet<int> npcs, List<string[]> entries)
    {
        Warn("legacy shop extraction: shop index -> NPC table is approximate and prices set inside SetupShop are not captured");
        var chestType = T("Terraria.Chest");
        foreach (var id in LegacyShopNpc) if (id > 0) shopNpcIds.Add(id);
        for (int shop = 1; shop < LegacyShopNpc.Length; shop++)
        {
            int npc = LegacyShopNpc[shop];
            int captured = shop;
            var ev = new Evaluator<HashSet<int>>(() =>
            {
                var chest = NewShopChest();
                CallInst(chest, "SetupShop", captured);
                var set = new HashSet<int>();
                foreach (var it in (Array)Get(chest, "item"))
                    if (it != null && Convert.ToInt32(Get(it, "type")) > 0) set.Add(Convert.ToInt32(Get(it, "type")));
                return set;
            });
            // every item the shop can ever show: reference state, one change, two changes
            var seen = new HashSet<int>();
            try
            {
                seen.UnionWith(ev.At(new Dictionary<int, int>()) ?? new HashSet<int>());
                foreach (var a in ev.Singles()) seen.UnionWith(ev.At(a) ?? new HashSet<int>());
                foreach (var a in ev.Pairs()) seen.UnionWith(ev.At(a) ?? new HashSet<int>());
            }
            catch (Exception e) { Warn("legacy shop " + shop + ": " + Inner(e).Message); }
            if (seen.Count == 0) continue;
            npcs.Add(npc);
            foreach (var type in seen.OrderBy(x => x))
            {
                int t = type;
                var dnf = Dnf(ev, set => set != null && set.Contains(t), null);
                var item = NewItem(type);
                foreach (var conds in dnf.Take(32))
                    entries.Add(Entry(npc, item, conds, npc == 368));
            }
        }
    }

    // ------------------------------------------------------------------ shimmer --------------

    static void Shimmer(Json j)
    {
        var sets = T("Terraria.ID.ItemID+Sets");
        var toItem = (int[])GetStatic(sets, "ShimmerTransformToItem");
        var st = T("Terraria.GameContent.ShimmerTransforms");
        MethodInfo locked = st == null ? null : st.GetMethod("IsItemTransformLocked", STATIC);
        MethodInfo decraftIdx = st == null ? null : st.GetMethod("GetDecraftingRecipeIndex", STATIC);
        var table = RecipeTable();
        int count = ItemCount();
        int nT = 0, nD = 0;
        j.Key("shimmer").Arr();
        for (int id = 1; id < count; id++)
        {
            int target = id < toItem.Length ? toItem[id] : -1;
            if (target > 0)
            {
                int captured = id;
                var variants = Conditional(() =>
                    locked != null && (bool)locked.Invoke(null, new object[] { captured }) ? null : new HashSet<int> { 1 });
                foreach (var v in variants)
                {
                    j.Obj();
                    j.Key("kind").Val("transmute");
                    j.Key("item").Val(id);
                    j.Key("count").Val(1);
                    j.Key("outputs").Arr().Arr().Val(target).Val(1).End().End();
                    j.Key("conditions").Arr(); foreach (var c in v.Key) j.Val(c); j.End();
                    j.End();
                    nT++;
                }
                continue;
            }
            if (decraftIdx == null) continue;
            int cid = id;
            var dv = ConditionalValue(() =>
            {
                int idx = Convert.ToInt32(decraftIdx.Invoke(null, new object[] { cid }));
                if (idx < 0) return null;
                if (locked != null && (bool)locked.Invoke(null, new object[] { cid })) return null;
                return DecraftSignature(table[idx]);
            });
            foreach (var v in dv)
            {
                j.Obj();
                j.Key("kind").Val("decraft");
                j.Key("item").Val(id);
                j.Key("count").Val(v.Value.Item1);
                j.Key("outputs").Arr();
                foreach (var o in v.Value.Item2) j.Arr().Val(o.Key).Val(o.Value).End();
                j.End();
                j.Key("conditions").Arr(); foreach (var c in v.Key) j.Val(c); j.End();
                j.End();
                nD++;
            }
        }
        j.End();
        Log("shimmer: " + nT + " transmutations, " + nD + " decrafts");
    }

    static Tuple<int, List<KeyValuePair<int, int>>> DecraftSignature(object r)
    {
        int stack = Convert.ToInt32(Get(Get(r, "createItem"), "stack"));
        object custom = null;
        try { custom = Get(r, "customShimmerResults"); } catch { }
        var outs = new List<KeyValuePair<int, int>>();
        foreach (var it in Seq(custom ?? Get(r, "requiredItem")))
        {
            int type = Convert.ToInt32(Get(it, "type"));
            if (type > 0) outs.Add(new KeyValuePair<int, int>(type, Convert.ToInt32(Get(it, "stack"))));
        }
        return Tuple.Create(stack, outs);
    }

    // ------------------------------------------------------------------ world-state probing --
    // Flags we can toggle: Main.hardMode, every NPC.downed* bool, WorldGen.crimson.

    class Flag { public string Name; public FieldInfo Field; }

    static List<Flag> flags;

    static List<Flag> Flags()
    {
        if (flags != null) return flags;
        flags = new List<Flag>();
        var hm = T("Terraria.Main").GetField("hardMode", STATIC);
        if (hm != null) flags.Add(new Flag { Name = "hardmode", Field = hm });
        foreach (var f in T("Terraria.NPC").GetFields(STATIC))
            if (f.FieldType == typeof(bool) && f.Name.StartsWith("downed", StringComparison.Ordinal))
                flags.Add(new Flag { Name = f.Name.ToLowerInvariant(), Field = f });
        var cr = T("Terraria.WorldGen").GetField("crimson", STATIC);
        if (cr != null) flags.Add(new Flag { Name = "crimson", Field = cr });
        foreach (var f in flags) Register(f.Name, "world", f.Field.DeclaringType.Name + "." + f.Field.Name, f.Name, false);
        return flags;
    }

    static void SetAll(bool v, Flag except = null)
    {
        foreach (var f in Flags()) f.Field.SetValue(null, f == except ? !v : v);
    }

    // Evaluate `eval` in the fully unlocked world, then find which single flags change the result.
    // Returns (conditions -> result) variants; results equal to null are dropped.
    static List<KeyValuePair<List<string>, TR>> ConditionalValue<TR>(Func<TR> eval) where TR : class
    {
        var saved = Flags().Select(f => f.Field.GetValue(null)).ToList();
        var outList = new List<KeyValuePair<List<string>, TR>>();
        try
        {
            SetAll(true);
            var top = eval();
            var need = new List<string>();
            foreach (var f in Flags())
            {
                SetAll(true, f);
                var r = eval();
                if (Same(r, top)) continue;
                need.Add(f.Name);
                if (r != null) outList.Add(new KeyValuePair<List<string>, TR>(new List<string> { "!" + f.Name }, r));
            }
            if (top != null) outList.Insert(0, new KeyValuePair<List<string>, TR>(need, top));
        }
        finally
        {
            for (int i = 0; i < Flags().Count; i++) Flags()[i].Field.SetValue(null, saved[i]);
        }
        return outList;
    }

    // Set-valued variant used for shops: items only present when some flags are off get "!flag".
    static List<KeyValuePair<List<string>, HashSet<int>>> Conditional(Func<HashSet<int>> eval)
    {
        var saved = Flags().Select(f => f.Field.GetValue(null)).ToList();
        var outList = new List<KeyValuePair<List<string>, HashSet<int>>>();
        try
        {
            SetAll(true);
            var top = eval() ?? new HashSet<int>();
            SetAll(false);
            var bottom = eval() ?? new HashSet<int>();
            var all = new HashSet<int>(top); all.UnionWith(bottom);
            var conds = new Dictionary<int, List<string>>();
            foreach (var x in all) conds[x] = new List<string>();
            foreach (var f in Flags())
            {
                SetAll(true, f);   // everything on except f
                var withoutF = eval() ?? new HashSet<int>();
                SetAll(false, f);  // everything off except f
                var onlyF = eval() ?? new HashSet<int>();
                foreach (var x in all)
                {
                    if (top.Contains(x) && !withoutF.Contains(x)) conds[x].Add(f.Name);
                    else if (bottom.Contains(x) && !onlyF.Contains(x)) conds[x].Add("!" + f.Name);
                }
            }
            foreach (var g in all.GroupBy(x => string.Join("&", conds[x])))
                outList.Add(new KeyValuePair<List<string>, HashSet<int>>(conds[g.First()], new HashSet<int>(g)));
        }
        finally
        {
            for (int i = 0; i < Flags().Count; i++) Flags()[i].Field.SetValue(null, saved[i]);
        }
        return outList;
    }

    static bool Same<TR>(TR a, TR b) where TR : class
    {
        if (a == null || b == null) return a == null && b == null;
        var ta = a as Tuple<int, List<KeyValuePair<int, int>>>;
        var tb = b as Tuple<int, List<KeyValuePair<int, int>>>;
        if (ta != null) return ta.Item1 == tb.Item1 && ta.Item2.SequenceEqual(tb.Item2);
        var sa = a as HashSet<int>;
        if (sa != null) return sa.SetEquals((HashSet<int>)(object)b);
        return a.Equals(b);
    }

    // ------------------------------------------------------------------ pricing probe --------
    // Records what Player.GetItemExpectedPrice returns for a few happiness adjustments, with and
    // without the Discount Card / Greedy Ring flag, so the Python side can check its pricing
    // formula (including the discount's rounding and order) against the real game.

    static void PricingProbe(Json j)
    {
        var player = LocalPlayer();
        var m = player.GetType().GetMethod("GetItemExpectedPrice", ANY);
        // Where the price adjustment lives: 1.4.4 reads Main.ShopHelper._currentPriceAdjustment,
        // 1.4.5 the player's currentShoppingSettings.PriceAdjustment.
        Action<float> setAdj = null;
        try
        {
            var helper = GetStatic("Terraria.Main", "ShopHelper");
            var adjField = helper.GetType().GetFields(INST).FirstOrDefault(f =>
                (f.FieldType == typeof(float) || f.FieldType == typeof(double))
                && f.Name.ToLowerInvariant().Contains("priceadjustment"));
            if (helper != null && adjField != null)
                setAdj = v => adjField.SetValue(helper, v);
        }
        catch { }
        if (setAdj == null)
        {
            var ssField = player.GetType().GetField("currentShoppingSettings", INST);
            var adjField = ssField == null ? null : ssField.FieldType.GetFields(INST).FirstOrDefault(f =>
                (f.FieldType == typeof(float) || f.FieldType == typeof(double))
                && f.Name.ToLowerInvariant().Contains("priceadjustment"));
            if (adjField == null) throw new MissingMemberException("price adjustment knob not found");
            setAdj = v => { var ss = ssField.GetValue(player); adjField.SetValue(ss, v); ssField.SetValue(player, ss); };
        }
        // Bool members like discountAvailable / discountEquipped; set together, whatever the version.
        var discountFields = player.GetType().GetFields(INST)
            .Where(f => f.FieldType == typeof(bool) && f.Name.ToLowerInvariant().Contains("discount")).ToList();
        var probeList = new List<string>();
        foreach (var discount in discountFields.Count > 0 ? new[] { false, true } : new[] { false })
        foreach (var adj in new[] { 0.75f, 0.9f, 1.0f, 1.12f, 1.5f })
        {
            foreach (var value in new[] { 1, 7, 100, 12345, 5000000 })
            {
                foreach (var f in discountFields) f.SetValue(player, discount);
                setAdj(adj);
                var item = NewItem(1);
                Set(item, "value", value);
                var ps = m.GetParameters();
                var argv = new object[ps.Length];
                argv[0] = item;
                m.Invoke(player, argv);
                object sell = null, buy = null;
                for (int i = 1; i < ps.Length; i++)
                {
                    var pn = ps[i].Name.ToLowerInvariant();
                    if (pn.Contains("sell")) sell = argv[i]; else if (pn.Contains("buy")) buy = argv[i];
                }
                probeList.Add("{\"adjustment\":" + adj.ToString(CultureInfo.InvariantCulture) + ",\"value\":" + value
                    + ",\"discount\":" + (discount ? "true" : "false")
                    + ",\"sell\":" + (sell ?? "null") + ",\"buy\":" + (buy ?? "null") + "}");
            }
        }
        j.Key("pricing_probe").Raw("[" + string.Join(",", probeList) + "]");
    }

    // ------------------------------------------------------------------ probe mode -----------

    static void Probe()
    {
        Console.WriteLine("version: " + GameVersion());
        Console.WriteLine("ItemID.Count: " + Safe(() => ItemCount()));
        Console.WriteLine("Recipe.numRecipes: " + Safe(() => GetStatic("Terraria.Recipe", "numRecipes")));
        TypeSearch("Shop");
        TypeSearch("Shimmer");
        foreach (var tn in new[] { "Terraria.Recipe", "Terraria.RecipeGroup", "Terraria.GameContent.ShimmerTransforms",
                                    "Terraria.GameContent.NPCShopDatabase", "Terraria.GameContent.NPCShop",
                                    "Terraria.Condition", "Terraria.Chest", "Terraria.GameContent.ShopHelper" })
        {
            var t = T(tn);
            Console.WriteLine("\n== " + tn + (t == null ? " (missing)" : ""));
            if (t == null) continue;
            foreach (var mem in t.GetMembers(ANY).Where(x => x is FieldInfo || x is PropertyInfo || x is MethodInfo)
                                 .Take(tn == "Terraria.Condition" ? 400 : 120))
                Console.WriteLine("  " + Describe(mem));
        }
        Console.WriteLine("\nflags: " + string.Join(", ", Flags().Select(f => f.Name)));
        try { Console.WriteLine("\nprobe knobs: " + string.Join(", ", Knobs().Select(k => k.Name))); }
        finally { RestoreKnobs(); }
        Console.WriteLine("\nwarnings:");
        foreach (var w in Warnings) Console.WriteLine("  " + w);
    }

    // Lists every loadable type whose full name contains `part`: how relocated classes (e.g. the
    // 1.4.5 shop database) are found when a fixed type name goes missing.
    static void TypeSearch(string part)
    {
        Type[] types;
        try { types = Game.GetTypes(); }
        catch (ReflectionTypeLoadException e) { types = e.Types; }
        Console.WriteLine("\n== types containing " + part);
        foreach (var t in types.Where(t => t != null && t.FullName != null && t.FullName.Contains(part))
                               .OrderBy(t => t.FullName))
            Console.WriteLine("  " + t.FullName);
    }

    static string Describe(MemberInfo m)
    {
        var f = m as FieldInfo; if (f != null) return (f.IsStatic ? "static " : "") + f.FieldType.Name + " " + f.Name;
        var p = m as PropertyInfo; if (p != null) return "prop " + p.PropertyType.Name + " " + p.Name;
        var me = (MethodInfo)m;
        return (me.IsStatic ? "static " : "") + me.ReturnType.Name + " " + me.Name + "(" + string.Join(", ", me.GetParameters().Select(x => x.ParameterType.Name)) + ")";
    }

    // ------------------------------------------------------------------ reflection helpers ---

    static Type T(string name)
    {
        var t = Game.GetType(name);
        if (t == null && name.Contains("+"))
        {
            var parts = name.Split('+');
            var outer = Game.GetType(parts[0]);
            t = outer == null ? null : outer.GetNestedType(parts[1], ANY);
        }
        return t;
    }

    static object GetStatic(string type, string member) { return GetStatic(T(type), member); }

    static object GetStatic(Type t, string member)
    {
        if (t == null) throw new Exception("type not found for " + member);
        var f = t.GetField(member, STATIC); if (f != null) return f.GetValue(null);
        var p = t.GetProperty(member, STATIC); if (p != null) return p.GetValue(null, null);
        throw new MissingMemberException(t.Name, member);
    }

    static void SetStatic(string type, string member, object v)
    {
        var t = T(type);
        var f = t.GetField(member, STATIC); if (f != null) { f.SetValue(null, v); return; }
        var p = t.GetProperty(member, STATIC); if (p != null) { p.SetValue(null, v, null); return; }
        throw new MissingMemberException(t.Name, member);
    }

    static object Get(object o, string member)
    {
        var t = o.GetType();
        var f = t.GetField(member, INST); if (f != null) return f.GetValue(o);
        var p = t.GetProperty(member, INST); if (p != null) return p.GetValue(o, null);
        throw new MissingMemberException(t.Name, member);
    }

    static void Set(object o, string member, object v)
    {
        var t = o.GetType();
        var f = t.GetField(member, INST); if (f != null) { f.SetValue(o, v); return; }
        t.GetProperty(member, INST).SetValue(o, v, null);
    }

    static object CallStatic(string type, string method, params object[] args)
    {
        var t = T(type);
        if (t == null) throw new Exception("type " + type + " not found");
        return Invoke(t.GetMethods(STATIC), method, null, args);
    }

    static object CallInst(object o, string method, params object[] args)
    {
        return Invoke(o.GetType().GetMethods(INST), method, o, args);
    }

    static object Invoke(MethodInfo[] ms, string name, object target, object[] args)
    {
        foreach (var m in ms.Where(x => x.Name == name).OrderBy(x => x.GetParameters().Length))
        {
            var ps = m.GetParameters();
            if (ps.Length < args.Length) continue;
            if (ps.Skip(args.Length).Any(p => !p.HasDefaultValue)) continue;
            var argv = new object[ps.Length];
            for (int i = 0; i < ps.Length; i++) argv[i] = i < args.Length ? args[i] : ps[i].DefaultValue;
            try { return m.Invoke(target, argv); }
            catch (ArgumentException) { continue; }
        }
        throw new MissingMethodException(name);
    }

    static object Default(Type t) { return t.IsValueType ? Activator.CreateInstance(t) : null; }

    static bool Contains(object set, int v)
    {
        foreach (var x in Seq(set)) if (Convert.ToInt32(x) == v) return true;
        return false;
    }

    static Exception Inner(Exception e) { while (e.InnerException != null) e = e.InnerException; return e; }

    static bool Try(string what, Action a)
    {
        try { a(); return true; }
        catch (Exception e) { Warn(what + ": " + Describe(e)); return false; }
    }

    static string Describe(Exception e)
    {
        e = Inner(e);
        var frames = (e.StackTrace ?? "").Split('\n').Where(l => l.Trim().StartsWith("at Terraria")).Take(3);
        return e.GetType().Name + ": " + e.Message + (frames.Any() ? "\n      " + string.Join("\n      ", frames) : "");
    }

    // A failed section is rolled back out of the JSON and skipped: the dump keeps whatever the
    // other sections produced (README: "logs warnings and keeps going section by section").
    static void Section(string what, Json j, Action a)
    {
        object mark = j.Mark();
        try { a(); }
        catch (Exception e)
        {
            j.Restore(mark);
            Warn("section " + what + " failed, skipped: " + Inner(e).GetType().Name + ": " + Inner(e).Message);
        }
    }

    static string Safe(Func<object> f) { try { return Convert.ToString(f()); } catch (Exception e) { return "ERR " + Inner(e).Message; } }

    static void Warn(string s) { Warnings.Add(s); Console.Error.WriteLine("[warn] " + s); }

    static void Log(string s) { Console.Error.WriteLine("[extractor] " + s); }

    static string Sha256(string path)
    {
        using (var sha = SHA256.Create())
        using (var s = File.OpenRead(path))
            return BitConverter.ToString(sha.ComputeHash(s)).Replace("-", "").ToLowerInvariant();
    }
}

// Minimal streaming JSON writer (no external dependencies).
class Json
{
    readonly StringBuilder sb = new StringBuilder();
    readonly Stack<char> closers = new Stack<char>();
    readonly Stack<bool> first = new Stack<bool>();
    bool afterKey;

    void Sep()
    {
        if (afterKey) { afterKey = false; return; }
        if (first.Count > 0) { if (!first.Peek()) sb.Append(','); first.Pop(); first.Push(false); }
    }

    Json Open(char open, char close) { Sep(); sb.Append(open); closers.Push(close); first.Push(true); return this; }
    public Json Obj() { return Open('{', '}'); }
    public Json Arr() { return Open('[', ']'); }

    public object Mark()
    {
        return Tuple.Create(sb.Length, closers.ToArray(), first.ToArray(), afterKey);
    }

    public void Restore(object mark)
    {
        var m = (Tuple<int, char[], bool[], bool>)mark;
        sb.Length = m.Item1;
        closers.Clear(); foreach (var c in m.Item2) closers.Push(c);
        first.Clear(); foreach (var f in m.Item3) first.Push(f);
        afterKey = m.Item4;
    }

    public Json End()
    {
        first.Pop();
        sb.Append(closers.Pop());
        if (closers.Count <= 1) sb.Append('\n');
        return this;
    }

    public Json Key(string k) { Sep(); sb.Append(Quote(k)).Append(':'); afterKey = true; return this; }
    public Json Val(string s) { Sep(); sb.Append(s == null ? "null" : Quote(s)); return this; }
    public Json Val(int v) { Sep(); sb.Append(v.ToString(CultureInfo.InvariantCulture)); return this; }
    public Json Val(bool v) { Sep(); sb.Append(v ? "true" : "false"); return this; }
    public Json Raw(string raw) { Sep(); sb.Append(raw); return this; }

    public static string Quote(string s)
    {
        var b = new StringBuilder("\"");
        foreach (var c in s)
        {
            switch (c)
            {
                case '"': b.Append("\\\""); break;
                case '\\': b.Append("\\\\"); break;
                case '\n': b.Append("\\n"); break;
                case '\r': b.Append("\\r"); break;
                case '\t': b.Append("\\t"); break;
                default:
                    if (c < 0x20) b.Append("\\u").Append(((int)c).ToString("x4")); else b.Append(c);
                    break;
            }
        }
        return b.Append('"').ToString();
    }

    public override string ToString() { return sb.ToString(); }
}
