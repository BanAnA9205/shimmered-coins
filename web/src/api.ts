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
  limited: boolean;
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
  one_shot: boolean;
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
  travel_stock: number[];
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
};
