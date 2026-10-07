// Typed client for the ZipScope API (same-origin /api — proxied by Vite in dev).
import type { Geometry } from "geojson";

export interface ZipFeatureProps {
  zip: string;
  pop: number | null;
  city: string;
  county: string;
  state: string;
  area: number; // square miles
  lat: number; // centroid
  lon: number; // centroid
}

export interface GeoFeature {
  type: "Feature";
  id: string;
  properties: ZipFeatureProps;
  geometry: Geometry;
}

export interface ZipFeatureCollection {
  type: "FeatureCollection";
  features: GeoFeature[];
  meta?: { scope: string; region: string; zips: number; matched?: number };
}

export type RegionScope = "state" | "county" | "place";

export interface Region {
  scope: RegionScope;
  id: string;
  name: string;
  state_fips: string;
  state_abbr: string;  // 2-letter abbreviation (AL, MO, etc.)
  code: string | null;
}

export interface ZipInfo {
  zip: string;
  state: { fips: string; name: string };
  county: { fips: string; name: string };
  city: string;
  population: number | null;
  centroid: { lat: number; lon: number };
  area_sqmi: number;
}

const BASE: string = import.meta.env.VITE_API_BASE ?? "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`${r.status}: ${text.slice(0, 200)}`.trim());
  }
  return r.json() as Promise<T>;
}

export const api = {
  geocode: (lat: number, lon: number) =>
    req<ZipInfo>("/api/geocode", { method: "POST", body: JSON.stringify({ lat, lon }) }),

  zips: (scope: string, region: string, state?: string) => {
    const p = new URLSearchParams({ scope, region });
    if (state) p.set("state", state);
    return req<ZipFeatureCollection>(`/api/zips?${p.toString()}`);
  },

  regions: async (q: string, scope?: RegionScope, state?: string, limit = 8): Promise<Region[]> => {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (scope) p.set("scope", scope);
    if (state) p.set("state", state);
    p.set("limit", String(limit));
    const r = await req<{ regions: Region[] }>(`/api/regions?${p.toString()}`);
    return r.regions;
  },

  health: () => req<{ ok: boolean; geoms_loaded: number }>(`/api/health`),
};
