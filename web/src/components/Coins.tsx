const UNITS: [number, string, string][] = [
  [1_000_000, "p", "platinum"],
  [10_000, "g", "gold"],
  [100, "s", "silver"],
  [1, "c", "copper"],
];

/** Copper amount rendered the Terraria way: 1p 23g 45s 67c, each denomination in its colour. */
export function Coins({ copper }: { copper: number }) {
  const negative = copper < 0;
  let rest = Math.abs(Math.round(copper));
  const parts: { n: number; sym: string; cls: string }[] = [];
  for (const [unit, sym, cls] of UNITS) {
    if (rest >= unit) {
      parts.push({ n: Math.floor(rest / unit), sym, cls });
      rest %= unit;
    }
  }
  if (!parts.length) parts.push({ n: 0, sym: "c", cls: "copper" });
  return (
    <span className="coins">
      {negative && <span className="coin-neg">-</span>}
      {parts.map((p) => (
        <span key={p.sym} className={`coin coin-${p.cls}`}>
          {p.n}
          {p.sym}
        </span>
      ))}
    </span>
  );
}
