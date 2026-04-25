export interface LatLng {
  lat: number;
  lng: number;
}

export interface ParkingSpot {
  lat: number;
  lng: number;
  name: string;
  type: string;
}

export interface EdgeProperties {
  edge_id: string;
  u: number;
  v: number;
  key?: number;
  section_id?: number;
  name: string;
  highway: string;
  is_highway: boolean;
  length: number;
  is_duplicate?: boolean;
  order?: number | null;
  access?: string;
  is_private?: boolean;
}

export interface GeoFeature {
  type: "Feature";
  geometry: {
    type: string;
    coordinates: number[] | number[][] | number[][][];
  };
  properties: EdgeProperties | Record<string, unknown>;
}

export interface GeoFeatureCollection {
  type: "FeatureCollection";
  features: GeoFeature[];
  stats?: NetworkStats;
}

export interface NetworkStats {
  node_count: number;
  edge_count: number;
  total_length_km: number;
  estimated_walk_hours: number;
}

export type ParkingType = "lot" | "street";

export interface Section {
  section_id: number;
  parking_type: ParkingType;
  parking_name: string;
  parking_lat: number;
  parking_lng: number;
  total_km: number;
  estimated_hours: number;
  bbox: [number, number, number, number];
  edge_ids: string[];
  edges: GeoFeature[];
  color?: string;
  is_private: boolean;
}

export interface Walk {
  walk_id: string;
  section_id: number;
  edge_ids: string[];
  total_km: number;
  est_hours: number;
  start: LatLng;
  route: [number, number][];
  backtrack_edge_ids: string[];
  is_private: boolean;
}

export interface WalkDetail extends Walk {
  route_features: GeoFeature[];
}

export interface WalksResponse {
  section_id: number;
  hours_per_walk: number;
  walks: Walk[];
}

export type EdgeStatus = "unvisited" | "complete";

export type WalkState = "unvisited" | "complete";

export interface Progress {
  // edge_id -> status
  edges: Record<string, EdgeStatus>;
  // section_id -> "complete" | "partial" | "unvisited"
  sections: Record<number, "complete" | "partial" | "unvisited">;
}
