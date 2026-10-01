export type Kind = "buy" | "craft" | "shimmer";

export interface DatasetSummary {
  id: string;
  version?: string;
  items?: number;
  error?: string;
}

export interface DatasetInfo {
  id: string;
  version: string;
  items: number;
  recipes: number;
  shimmer_rules: number;
  npcs: { id: number; name: string }[];
  flag_groups: Record<string, { name: string; description: string }[]>;
  zones: string[];
}

export interface NpcIn {
  adjustment: number;
  zones: string[] | "any";
}

export interface ItemRef {
  id: number;
  name: string;
  value?: number;
}

export interface Stack {
  id: number;
  name: string;
  count: number;
}

export interface MoveOut {
  kind: Kind | "sell"; // "sell" = the final sell-all step
  batches: number;
  text: string;
  inputs: Stack[];
  outputs: Stack[];
  cost: number;
  revenue: number;
  npc: string | null;
}

export interface ResultOut {
  gain: number;
  lp_gain: number;
  capital: number;
  capital_text: string;
  profit: number;
  profit_text: string;
  length: number;
  places: string[];
  score: number;
  per_trip: number;
  moves: MoveOut[];
  leftover: Stack[];
}

export interface SearchResponse {
  version: string;
  moves_available: number;
  stats: { solves: number; loops: number; unschedulable: number; elapsed: number; complete: boolean };
  loops: { moves: MoveOut[] }[];
  results: ResultOut[];
}

export interface SearchRequest {
  dataset: string;
  flags: string[];
  shimmer: boolean;
  discount: boolean;
  npcs: Record<number, NpcIn>;
  kinds: Kind[];
  include: number[] | null;
  exclude: number[];
  must_use: number[];
  start: number[] | null;
  top_n: number;
  overhead: number;
  max_places: number | null;
  wallet: number | null;
  allow_loss: boolean;
  time_limit: number;
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not json */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  datasets: () => call<DatasetSummary[]>("/api/datasets"),
  dataset: (id: string) => call<DatasetInfo>(`/api/datasets/${encodeURIComponent(id)}`),
  items: (id: string, q: string) =>
    call<ItemRef[]>(`/api/datasets/${encodeURIComponent(id)}/items?q=${encodeURIComponent(q)}`),
  search: (req: SearchRequest) =>
    call<SearchResponse>("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  sprites: () => call<SpritesStatus>("/api/sprites"),
  spritePreview: (dataset: string) =>
    call<SpritePreview[]>(`/api/sprites/preview?dataset=${encodeURIComponent(dataset)}`),
  spriteImport: (path?: string) =>
    call<{ started: boolean; path: string }>("/api/sprites/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(path ? { path } : {}),
    }),
  spriteImportStatus: () => call<SpriteImportStatus>("/api/sprites/import"),
  worlds: () => call<WorldSummary[]>("/api/worlds"),
  worldDetail: (path: string, dataset: string) =>
    call<WorldDetail>("/api/worlds/detail", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, dataset }),
    }),
  plan: (req: PlanRequest) =>
    call<PlanResponse>("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
};

export interface SpriteSource {
  app: string;
  path: string;
  items: number;
}

export interface SpritesStatus {
  installed: number;
  sources: SpriteSource[];
}

export interface SpriteImportStatus {
  running: boolean;
  done: number;
  total: number;
  error: string | null;
  installed: number;
}

export interface SpritePreview {
  id: number;
  name: string;
  url: string;
}

export interface WorldSummary {
  name: string;
  seed?: string;
  version?: number;
  path?: string;
  chests?: number;
  npcs?: number;
  items?: number;
  error?: string;
}

export interface WorldDetail {
  name: string;
  seed: string;
  version: number;
  chests: { x: number; y: number; name: string; items: number }[];
  npcs: { type: number; name: string; homeless: boolean }[];
  stock: { id: number; name: string; count: number }[];
}

export interface StockItem {
  id: number;
  name: string;
  count: number;
}

export interface PlanMove {
  kind: "buy" | "sell" | "craft" | "shimmer" | "stock" | "farm";
  batches: number;
  text: string;
  inputs?: Stack[];
  outputs?: Stack[];
  cost?: number;
  revenue?: number;
  npc?: string | null;
}

export interface PlanResult {
  profit: number;
  profit_text: string;
  capital: number;
  capital_text: string;
  kills: number;
  approx: boolean;
  places: string[];
  moves: PlanMove[];
  residue: Stack[];
}

export interface PlanStats {
  solves: number;
  loops: number;
  elapsed: number;
  complete: boolean;
}

export interface PlanResponse {
  version: string;
  liquidation: number;
  stats: PlanStats;
  results: PlanResult[];
  /** every result at once, sharing stock, wallet and kills; null when it adds nothing */
  combined: PlanResult | null;
}

export interface PlanRequest {
  dataset: string;
  world?: string | null;
  stock: Record<number, number> | null;
  npcs: Record<number, NpcIn>;
  flags: string[];
  shimmer: boolean;
  discount: boolean;
  kinds: Kind[];
  include: number[] | null;
  exclude: number[];
  must_use: number[];
  top_n: number;
  wallet: number;
  kills: number;
  expert: boolean;
  min_profit: number;
  show_liquidation: boolean;
  time_limit: number;
}
