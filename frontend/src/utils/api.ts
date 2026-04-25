import type {
  GeoFeature,
  GeoFeatureCollection,
  Section,
  WalkDetail,
  WalksResponse,
} from "../types";

const BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${path}: ${res.status} — ${text}`);
  }
  return res.json();
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  getDefaultBoundary: () => request<GeoFeature>("/default-boundary"),

  uploadBoundary: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<GeoFeature>("/boundary", { method: "POST", body: fd });
  },

  getRoads: (forceRefresh = false) =>
    request<GeoFeatureCollection>(`/roads${forceRefresh ? "?force_refresh=true" : ""}`),

  getSections: (forceRefresh = false) =>
    request<Section[]>(`/sections${forceRefresh ? "?force_refresh=true" : ""}`),

  getSectionWalks: (sectionId: number, hoursPerWalk = 1.0) =>
    request<WalksResponse>(
      `/sections/${sectionId}/walks?hours_per_walk=${hoursPerWalk}`
    ),

  getWalk: (sectionId: number, walkId: string, hoursPerWalk = 1.0) =>
    request<WalkDetail>(
      `/sections/${sectionId}/walks/${walkId}?hours_per_walk=${hoursPerWalk}`
    ),

  getStats: () =>
    request<{
      loaded: boolean;
      node_count?: number;
      edge_count?: number;
      total_length_km?: number;
      estimated_walk_hours?: number;
      section_count?: number;
    }>("/stats"),
};
