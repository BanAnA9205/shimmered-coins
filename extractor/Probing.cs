// Condition probing: work out what a shop/recipe condition (or a legacy hard-coded shop) depends on
// by changing one piece of game state at a time ("knobs") and watching the result.
//
// Every knob has a reference value (value 0): progression unlocked (hardmode, all bosses down),
// daytime, moon phase 0, no events, no special biome, no other NPCs, neutral happiness. A
// condition's truth table over the knobs it depends on is turned into a small DNF of typed
// condition strings (see docs/DATA_FORMAT.md): "hardmode", "!daytime", "moon:3", "zone:snow",
// "npc:22", "happy<=0.9". Conditions that no knob explains become "player:<slug>" flags.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text;

static partial class Program
{
    class Knob
    {
        public string Name;         // e.g. "hardmode", "zone:snow", "moon", "npc:22", "happy"
        public string Scope;        // world | zone | npc_present | happiness
        public string Description;
        public int Domain = 2;      // values 0..Domain-1, 0 = reference
        public Action<int> Set;
        public Func<int, string> Literal;  // condition string meaning "this knob has value v"
    }

    class CatalogEntry { public string Scope, Description; public bool OwnText; public HashSet<string> Depends = new HashSet<string>(); }

    static List<Knob> knobs;
    static readonly Dictionary<string, CatalogEntry> Catalog = new Dictionary<string, CatalogEntry>();
    static List<Action> restoreKnobs = new List<Action>();
    static int probeEvaluations;
    const int MaxProbeEvaluations = 400000;

    // ------------------------------------------------------------------ knob discovery -------

    static object LocalPlayer()
    {
        var players = (Array)GetStatic("Terraria.Main", "player");
        int my = Convert.ToInt32(GetStatic("Terraria.Main", "myPlayer"));
        if (my < 0 || my >= players.Length) my = 0;
        var p = players.GetValue(my);
        if (p == null)
        {
            p = Activator.CreateInstance(T("Terraria.Player"));
            players.SetValue(p, my);
        }
        return p;
    }

    static void AddBoolKnob(List<Knob> list, Type t, string member, string literal, bool refValue, string scope, string desc, object target = null)
    {
        if (t == null) return;
        var flags = target == null ? STATIC : INST;
        var f = t.GetField(member, flags);
        var p = f == null ? t.GetProperty(member, flags) : null;
        if (f == null && (p == null || !p.CanWrite || p.PropertyType != typeof(bool))) return;
        if (f != null && f.FieldType != typeof(bool)) return;
        Action<bool> set = v => { if (f != null) f.SetValue(target, v); else p.SetValue(target, v, null); };
        var original = f != null ? f.GetValue(target) : p.GetValue(target, null);
        restoreKnobs.Add(() => set((bool)original));
        list.Add(new Knob
        {
            Name = literal, Scope = scope, Description = desc,
            Set = v => set(v == 0 ? refValue : !refValue),
            Literal = v => (v == 0) == refValue ? literal : "!" + literal,
        });
    }

    static string ZoneName(string prop)
    {
        var n = prop.Substring(4);
        switch (n)
        {
            case "OverworldHeight": return "surface";
            case "DirtLayerHeight": return "underground";
            case "RockLayerHeight": return "cavern";
            case "UnderworldHeight": return "underworld";
            case "SkyHeight": return "space";
        }
        return n.ToLowerInvariant();
    }

    static List<Knob> Knobs()
    {
        if (knobs != null) return knobs;
        knobs = new List<Knob>();
        var main = T("Terraria.Main");

        // progression: reference = unlocked
        AddBoolKnob(knobs, main, "hardMode", "hardmode", true, "world", "Hardmode");
        foreach (var f in T("Terraria.NPC").GetFields(STATIC))
            if (f.FieldType == typeof(bool) && f.Name.StartsWith("downed", StringComparison.Ordinal))
                AddBoolKnob(knobs, T("Terraria.NPC"), f.Name, f.Name.ToLowerInvariant(), true, "world", f.Name);

        // time, events, world kinds: reference = daytime, nothing happening
        AddBoolKnob(knobs, main, "dayTime", "daytime", true, "world", "Daytime");
        var worldBools = new[] {
            "bloodMoon:bloodmoon", "eclipse:eclipse", "raining:rain", "pumpkinMoon:pumpkinmoon",
            "snowMoon:frostmoon", "halloween:halloween", "xMas:christmas", "slimeRain:slimerain",
            "drunkWorld:drunkworld", "getGoodWorld:fortheworthy", "tenthAnniversaryWorld:tenthanniversary",
            "dontStarveWorld:dontstarve", "notTheBeesWorld:notthebees", "remixWorld:remix",
            "noTrapsWorld:notraps", "zenithWorld:zenith" };
        foreach (var wb in worldBools)
        {
            var parts = wb.Split(':');
            AddBoolKnob(knobs, main, parts[0], parts[1], false, "world", parts[0]);
        }
        AddBoolKnob(knobs, T("Terraria.WorldGen"), "crimson", "crimson", false, "world", "Crimson world");
        AddBoolKnob(knobs, T("Terraria.GameContent.Events.BirthdayParty"), "ManualParty", "party", false, "world", "Party");

        var moon = main.GetField("moonPhase", STATIC);
        if (moon != null)
        {
            var orig = moon.GetValue(null);
            restoreKnobs.Add(() => moon.SetValue(null, orig));
            knobs.Add(new Knob { Name = "moon", Scope = "world", Description = "Moon phase", Domain = 8,
                                 Set = v => moon.SetValue(null, v), Literal = v => "moon:" + v });
        }
        var mode = main.GetProperty("GameMode", STATIC);
        if (mode != null && mode.CanWrite)
        {
            var orig = mode.GetValue(null, null);
            restoreKnobs.Add(() => { try { mode.SetValue(null, orig, null); } catch { } });
            var names = new[] { "normal", "expert", "master", "journey" };
            knobs.Add(new Knob { Name = "mode", Scope = "world", Description = "Difficulty", Domain = 4,
                                 Set = v => mode.SetValue(null, v, null), Literal = v => "mode:" + names[v] });
        }

        // biome zones of the local player = where you stand when talking to the NPC
        try
        {
            var player = LocalPlayer();
            foreach (var p in player.GetType().GetProperties(INST))
                if (p.Name.StartsWith("Zone", StringComparison.Ordinal) && p.PropertyType == typeof(bool) && p.CanWrite)
                    AddBoolKnob(knobs, player.GetType(), p.Name, "zone:" + ZoneName(p.Name), false, "zone", p.Name, player);
        }
        catch (Exception e) { Warn("zone knobs: " + Inner(e).Message); }

        // other town NPCs present
        try
        {
            var npcArr = (Array)GetStatic("Terraria.Main", "npc");
            var npcType = T("Terraria.NPC");
            int slot = 0;
            foreach (var id in TownNpcIds())
            {
                int s = slot++;
                if (s >= npcArr.Length) break;
                int captured = id;
                Func<object> npcAt = () =>
                {
                    var n = npcArr.GetValue(s);
                    if (n == null) { n = Activator.CreateInstance(npcType); npcArr.SetValue(n, s); }
                    return n;
                };
                restoreKnobs.Add(() => { try { Set(npcAt(), "active", false); } catch { } });
                knobs.Add(new Knob
                {
                    Name = "npc:" + id, Scope = "npc_present", Description = NpcName(id) + " lives in town",
                    Set = v => { var n = npcAt(); Set(n, "type", captured); Set(n, "active", v == 1); },
                    Literal = v => (v == 1 ? "" : "!") + "npc:" + captured,
                });
            }
        }
        catch (Exception e) { Warn("npc knobs: " + Inner(e).Message); }

        // happiness of the NPC you're talking to
        try
        {
            var player = LocalPlayer();
            var ssField = player.GetType().GetField("currentShoppingSettings", INST);
            if (ssField != null)
            {
                var orig = ssField.GetValue(player);
                restoreKnobs.Add(() => ssField.SetValue(player, orig));
                knobs.Add(new Knob
                {
                    Name = "happy", Scope = "happiness", Description = "The NPC is happy enough",
                    Set = v => SetAdjustment(v == 0 ? 1.0 : 0.75),
                    Literal = v => v == 1 ? "happy<=?" : "!happy<=?",  // threshold filled in by the prober
                });
            }
        }
        catch (Exception e) { Warn("happiness knob: " + Inner(e).Message); }

        foreach (var k in knobs) k.Set(0);
        Log("probe knobs: " + knobs.Count);
        return knobs;
    }

    static void SetAdjustment(double adj)
    {
        var player = LocalPlayer();
        var ssField = player.GetType().GetField("currentShoppingSettings", INST);
        var ss = ssField.GetValue(player);  // boxed struct
        var pa = ss.GetType().GetField("PriceAdjustment", INST);
        if (pa != null) pa.SetValue(ss, Convert.ChangeType(adj, pa.FieldType, CultureInfo.InvariantCulture));
        ssField.SetValue(player, ss);
        try
        {
            var helper = GetStatic("Terraria.Main", "ShopHelper");
            var f = helper.GetType().GetFields(INST).FirstOrDefault(x => x.Name.ToLowerInvariant().Contains("priceadjustment"));
            if (f != null) f.SetValue(helper, Convert.ChangeType(adj, f.FieldType, CultureInfo.InvariantCulture));
        }
        catch { }
    }

    static readonly int[] ExtraTownNpcs = { 17, 18, 19, 20, 22, 38, 54, 107, 108, 124, 142, 160, 178, 207, 208, 209, 227, 228, 229, 353, 369, 441, 550, 588, 633, 663 };
    static HashSet<int> shopNpcIds = new HashSet<int>();

    static IEnumerable<int> TownNpcIds()
    {
        int count = Convert.ToInt32(GetStatic("Terraria.ID.NPCID", "Count"));
        return shopNpcIds.Concat(ExtraTownNpcs).Where(i => i > 0 && i < count).Distinct().OrderBy(i => i);
    }

    static void RestoreKnobs() { foreach (var r in restoreKnobs) { try { r(); } catch { } } }

    // ------------------------------------------------------------------ evaluation -----------

    static string Key(Dictionary<int, int> a)
    {
        return string.Join(",", a.Where(kv => kv.Value != 0).OrderBy(kv => kv.Key).Select(kv => kv.Key + "=" + kv.Value));
    }

    static void Apply(Dictionary<int, int> a)
    {
        var ks = Knobs();
        for (int i = 0; i < ks.Count; i++)
        {
            int v;
            try { ks[i].Set(a.TryGetValue(i, out v) ? v : 0); } catch { }
        }
    }

    // Evaluates `eval` under knob assignments, caching by assignment.
    class Evaluator<TR>
    {
        readonly Func<TR> eval;
        readonly Dictionary<string, TR> cache = new Dictionary<string, TR>();
        public Evaluator(Func<TR> eval) { this.eval = eval; }

        public TR Raw() { return eval(); }

        public TR At(Dictionary<int, int> a)
        {
            var k = Key(a);
            TR r;
            if (cache.TryGetValue(k, out r)) return r;
            if (++probeEvaluations > MaxProbeEvaluations) throw new Exception("probe evaluation budget exhausted");
            Apply(a);
            try { r = eval(); } catch { r = default(TR); }
            cache[k] = r;
            return r;
        }

        public IEnumerable<Dictionary<int, int>> Singles()
        {
            var ks = Knobs();
            for (int i = 0; i < ks.Count; i++)
                for (int v = 1; v < ks[i].Domain; v++)
                    yield return new Dictionary<int, int> { { i, v } };
        }

        public IEnumerable<Dictionary<int, int>> Pairs()
        {
            var ks = Knobs();
            for (int i = 0; i < ks.Count; i++)
                for (int j = i + 1; j < ks.Count; j++)
                    for (int vi = 1; vi < ks[i].Domain; vi++)
                        for (int vj = 1; vj < ks[j].Domain; vj++)
                            yield return new Dictionary<int, int> { { i, vi }, { j, vj } };
        }
    }

    // DNF (list of AND-lists of condition strings) for predicate p over the evaluator's results.
    // Returns an empty list when p never held in any probed state.
    static List<List<string>> Dnf<TR>(Evaluator<TR> ev, Func<TR, bool> p, string description)
    {
        var baseline = p(ev.At(new Dictionary<int, int>()));
        var dep = new SortedSet<int>();
        foreach (var a in ev.Singles())
            if (p(ev.At(a)) != baseline) dep.UnionWith(a.Keys);
        if (dep.Count == 0 && !baseline)
        {
            foreach (var a in ev.Pairs())
                if (p(ev.At(a))) { dep.UnionWith(a.Keys); break; }
        }
        if (dep.Count == 0)
            return baseline ? new List<List<string>> { new List<string>() } : new List<List<string>>();

        var ks = Knobs();
        var depList = dep.ToList();
        long combos = depList.Aggregate(1L, (acc, i) => acc * ks[i].Domain);
        if (combos > 512)
        {
            Warn("condition '" + description + "' depends on too many things; keeping single-knob literals only");
            var lits = new List<string>();
            foreach (var i in depList)
                for (int v = 0; v < ks[i].Domain; v++)
                    if (p(ev.At(new Dictionary<int, int> { { i, v } })) && v != 0) lits.Add(ks[i].Literal(v));
            return new List<List<string>> { lits };
        }

        // truth table over the dependent knobs
        var minterms = new List<int[]>();
        var assign = new int[depList.Count];
        for (long c = 0; c < combos; c++)
        {
            long rest = c;
            for (int k = 0; k < depList.Count; k++) { assign[k] = (int)(rest % ks[depList[k]].Domain); rest /= ks[depList[k]].Domain; }
            var a = new Dictionary<int, int>();
            for (int k = 0; k < depList.Count; k++) a[depList[k]] = assign[k];
            if (p(ev.At(a))) minterms.Add((int[])assign.Clone());
        }
        // merge minterms that cover every value of one knob (-1 = don't care)
        bool merged = true;
        while (merged)
        {
            merged = false;
            for (int k = 0; k < depList.Count && !merged; k++)
            {
                int dom = ks[depList[k]].Domain;
                foreach (var g in minterms.Where(m => m[k] >= 0).GroupBy(m => string.Join(",", m.Select((x, idx) => idx == k ? "*" : x.ToString()))))
                {
                    if (g.Select(m => m[k]).Distinct().Count() != dom) continue;
                    var keep = g.First().ToArray();
                    keep[k] = -1;
                    var remove = new HashSet<int[]>(g);
                    minterms.RemoveAll(remove.Contains);
                    minterms.Add(keep);
                    merged = true;
                    break;
                }
            }
        }
        var outList = new List<List<string>>();
        foreach (var m in minterms)
        {
            var lits = new List<string>();
            var a = new Dictionary<int, int>();
            for (int k = 0; k < depList.Count; k++) if (m[k] >= 0) a[depList[k]] = m[k];
            for (int k = 0; k < depList.Count; k++)
            {
                if (m[k] < 0) continue;
                var knob = ks[depList[k]];
                var lit = knob.Literal(m[k]);
                if (lit.Contains("happy<=?")) lit = lit.Replace("?", HappyThreshold(ev, p, a, depList[k]));
                lits.Add(lit);
                // the condition's own text only describes the literal when it isn't negated
                bool own = depList.Count == 1 && !string.IsNullOrEmpty(description) && !lit.StartsWith("!");
                Register(lit, knob.Scope, own ? description : knob.Description, knob.Name, own);
            }
            outList.Add(lits);
        }
        return outList;
    }

    // Largest price adjustment at which p still holds (with the other knobs as in `a`).
    static string HappyThreshold<TR>(Evaluator<TR> ev, Func<TR, bool> p, Dictionary<int, int> a, int happyKnob)
    {
        double best = 0.75;
        for (int step = 0; step <= 75; step++)
        {
            double adj = 0.75 + step * 0.01;
            Apply(a.Where(kv => kv.Key != happyKnob).ToDictionary(kv => kv.Key, kv => kv.Value));
            SetAdjustment((double)(float)adj);  // the game stores adjustments as floats
            bool ok;
            try { ok = p(ev.Raw()); } catch { ok = false; }
            if (ok) best = adj; else if (step > 0) break;
        }
        return best.ToString("0.##", CultureInfo.InvariantCulture);
    }

    static void Register(string literal, string scope, string description, string depends, bool ownText = true)
    {
        var name = literal.TrimStart('!');
        CatalogEntry e;
        if (!Catalog.TryGetValue(name, out e)) { e = new CatalogEntry { Scope = scope, Description = description, OwnText = ownText }; Catalog[name] = e; }
        else if (ownText && !e.OwnText) { e.Description = description; e.OwnText = true; }
        if (depends != null) e.Depends.Add(depends);
    }

    static string Slug(string s)
    {
        var b = new StringBuilder();
        foreach (var c in (s ?? "").ToLowerInvariant())
            if (char.IsLetterOrDigit(c)) b.Append(c); else if (b.Length > 0 && b[b.Length - 1] != '-') b.Append('-');
        return b.ToString().Trim('-');
    }

    // ------------------------------------------------------------------ modern conditions ----

    static readonly Dictionary<object, List<List<string>>> conditionDnf = new Dictionary<object, List<List<string>>>();
    static Dictionary<object, string> conditionFieldNames;

    static string ConditionDescription(object cond)
    {
        try { return Convert.ToString(Get(Get(cond, "Description"), "Value")); } catch { }
        if (conditionFieldNames == null)
        {
            conditionFieldNames = new Dictionary<object, string>();
            var ct = T("Terraria.Condition");
            if (ct != null)
                foreach (var f in ct.GetFields(STATIC))
                    if (f.FieldType == ct) { var v = f.GetValue(null); if (v != null && !conditionFieldNames.ContainsKey(v)) conditionFieldNames[v] = f.Name; }
        }
        string n;
        return conditionFieldNames.TryGetValue(cond, out n) ? n : cond.ToString();
    }

    static bool IsMet(object cond)
    {
        var m = cond.GetType().GetMethod("IsMet", INST, null, Type.EmptyTypes, null);
        if (m != null) return (bool)m.Invoke(cond, null);
        var pred = Get(cond, "Predicate") as Delegate;
        return (bool)pred.DynamicInvoke();
    }

    // DNF for one Condition object; unexplained conditions become a "player:<slug>" flag.
    static List<List<string>> ConditionAlternatives(object cond)
    {
        List<List<string>> dnf;
        if (conditionDnf.TryGetValue(cond, out dnf)) return dnf;
        var desc = ConditionDescription(cond);
        try
        {
            var ev = new Evaluator<bool>(() => IsMet(cond));
            dnf = Dnf(ev, x => x, desc);
        }
        catch (Exception e) { Warn("condition '" + desc + "': " + Inner(e).Message); dnf = new List<List<string>>(); }
        if (dnf.Count == 0)
        {
            var lit = "player:" + Slug(desc);
            Register(lit, "player", desc, null);
            dnf = new List<List<string>> { new List<string> { lit } };
        }
        conditionDnf[cond] = dnf;
        return dnf;
    }

    // All ways a list of conditions (AND) can hold, as AND-lists of condition strings.
    static List<List<string>> Expand(IEnumerable<object> conds, int cap)
    {
        var acc = new List<List<string>> { new List<string>() };
        foreach (var c in conds)
        {
            var next = new List<List<string>>();
            foreach (var left in acc)
                foreach (var alt in ConditionAlternatives(c))
                    if (next.Count < cap) next.Add(left.Concat(alt).Distinct().ToList());
            acc = next;
        }
        return acc;
    }

    static void WriteCatalog(Json j)
    {
        j.Key("conditions").Arr();
        foreach (var kv in Catalog.OrderBy(kv => kv.Key))
        {
            j.Obj();
            j.Key("name").Val(kv.Key);
            j.Key("scope").Val(kv.Value.Scope);
            j.Key("description").Val(kv.Value.Description ?? "");
            j.Key("depends").Arr(); foreach (var d in kv.Value.Depends.OrderBy(x => x)) j.Val(d); j.End();
            j.End();
        }
        j.End();
    }
}
