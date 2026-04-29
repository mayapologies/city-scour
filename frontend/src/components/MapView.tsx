import { useEffect, useRef } from "react";
import L from "leaflet";
import type { GeoFeature, Section, EdgeStatus, Progress } from "../types";

// Vibrant, high-saturation palette — readable on dark map background (#1e1e2e).
// Cycles for > 12 sections.
const SECTION_COLORS = [
  "#ef4444", // red
  "#f97316", // orange
  "#eab308", // amber
  "#84cc16", // lime
  "#22c55e", // green
  "#14b8a6", // teal
  "#06b6d4", // cyan
  "#3b82f6", // blue
  "#8b5cf6", // violet
  "#d946ef", // fuchsia
  "#ec4899", // pink
  "#f43f5e", // rose
];

function sectionColor(id: number): string {
  return SECTION_COLORS[id % SECTION_COLORS.length];
}

function edgeColor(
  edgeId: string,
  isHighway: boolean,
  sectionId: number | undefined,
  selectedSectionId: number | null,
  progress: Progress
): string {
  const status = progress.edges[edgeId];
  if (status === "complete") return "#22c55e";
  if (isHighway) return "#94a3b8";
  if (selectedSectionId !== null && sectionId === selectedSectionId) {
    return "#22d3ee";
  }
  return sectionId !== undefined ? sectionColor(sectionId) : "#94a3b8";
}

function edgeWeight(isHighway: boolean, selected: boolean): number {
  if (selected) return 4;
  if (isHighway) return 2;
  return 2.5;
}

interface MapViewProps {
  boundary: GeoFeature | null;
  sections: Section[];
  selectedSectionId: number | null;
  walkRouteFeatures: GeoFeature[];
  progress: Progress;
  onSelectSection: (id: number | null) => void;
  onEdgeClick: (edgeId: string, isHighway: boolean, currentStatus: EdgeStatus) => void;
}

export function MapView({
  boundary,
  sections,
  selectedSectionId,
  walkRouteFeatures,
  progress,
  onSelectSection,
  onEdgeClick,
}: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const boundaryLayerRef = useRef<L.Layer | null>(null);
  const sectionLayersRef = useRef<L.LayerGroup | null>(null);
  const routeLayerRef = useRef<L.LayerGroup | null>(null);
  const parkingLayersRef = useRef<L.LayerGroup | null>(null);

  // Initialize map once
  useEffect(() => {
    if (mapRef.current || !containerRef.current) return;

    const map = L.map(containerRef.current, {
      center: [37.323, -122.032],
      zoom: 13,
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors",
      maxZoom: 19,
    }).addTo(map);

    mapRef.current = map;
    sectionLayersRef.current = L.layerGroup().addTo(map);
    routeLayerRef.current = L.layerGroup().addTo(map);
    parkingLayersRef.current = L.layerGroup().addTo(map);
  }, []);

  // Boundary layer
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !boundary) return;

    if (boundaryLayerRef.current) {
      boundaryLayerRef.current.remove();
    }

    const layer = L.geoJSON(boundary as GeoJSON.Feature, {
      style: {
        color: "#6366f1",
        weight: 3,
        fill: false,
        dashArray: "6 4",
      },
    }).addTo(map);

    boundaryLayerRef.current = layer;
    map.fitBounds(layer.getBounds(), { padding: [20, 20] });
  }, [boundary]);

  // Section road layers
  useEffect(() => {
    const map = mapRef.current;
    const group = sectionLayersRef.current;
    if (!map || !group) return;

    group.clearLayers();

    for (const section of sections) {
      const sid = section.section_id;
      const isSelected = sid === selectedSectionId;

      for (const feat of section.edges) {
        const props = feat.properties as {
          edge_id: string;
          is_highway: boolean;
          highway: string;
          name: string;
          length: number;
          section_id?: number;
          is_private?: boolean;
          access?: string;
        };

        const isPrivate =
          props.is_private === true || props.access === "private";
        const color = edgeColor(
          props.edge_id,
          props.is_highway,
          sid,
          selectedSectionId,
          progress
        );
        const weight = edgeWeight(props.is_highway, isSelected);
        const opacity =
          selectedSectionId === null || isSelected ? 1 : 0.3;

        const polyline = L.geoJSON(feat as GeoJSON.Feature, {
          style: {
            color,
            weight,
            opacity,
            dashArray: isPrivate ? "8 4" : undefined,
          },
        });

        polyline.on("click", () => {
          const status = progress.edges[props.edge_id] ?? "unvisited";
          onEdgeClick(props.edge_id, props.is_highway, status);
        });

        polyline.bindTooltip(
          `<strong>${props.name || props.highway || "road"}</strong><br>` +
            `Section ${sid} · ${Math.round(props.length)}m` +
            (isPrivate ? "<br>🔒 private (may be gated)" : ""),
          { sticky: true }
        );

        // Click section background to select it
        polyline.on("click", (e) => {
          L.DomEvent.stopPropagation(e);
          if (selectedSectionId === sid) {
            onSelectSection(null);
          } else {
            onSelectSection(sid);
          }
          const status = progress.edges[props.edge_id] ?? "unvisited";
          onEdgeClick(props.edge_id, props.is_highway, status);
        });

        group.addLayer(polyline);
      }
    }
  }, [sections, selectedSectionId, progress, onSelectSection, onEdgeClick]);

  // Route overlay for selected section
  useEffect(() => {
    const group = routeLayerRef.current;
    if (!group) return;
    group.clearLayers();

    if (!walkRouteFeatures.length) return;

    walkRouteFeatures.forEach((feat, idx) => {
      const props = feat.properties as { is_duplicate?: boolean; edge_id: string };
      const isDup = props.is_duplicate ?? false;
      const layer = L.geoJSON(feat as GeoJSON.Feature, {
        style: {
          color: isDup ? "#f97316" : "#facc15",
          weight: isDup ? 3 : 4,
          opacity: 0.85,
          dashArray: isDup ? "4 4" : undefined,
        },
      });
      layer.bindTooltip(`Step ${idx + 1}${isDup ? " (backtrack)" : ""}`, {
        sticky: true,
      });
      group.addLayer(layer);
    });
  }, [walkRouteFeatures]);

  // Parking marker — only for the selected section
  useEffect(() => {
    const group = parkingLayersRef.current;
    if (!group) return;
    group.clearLayers();

    if (selectedSectionId === null) return;
    const section = sections.find((s) => s.section_id === selectedSectionId);
    if (!section) return;

    const isLot = section.parking_type === "lot";
    const bg = isLot ? "#6366f1" : "#475569";
    const symbol = isLot ? "P" : "S";

    const icon = L.divIcon({
      html: `<div style="background:${bg};color:#fff;border-radius:50%;width:22px;height:22px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;border:2px solid white;box-shadow:0 2px 4px rgba(0,0,0,.4)">${symbol}</div>`,
      className: "",
      iconAnchor: [11, 11],
    });

    L.marker([section.parking_lat, section.parking_lng], { icon })
      .bindTooltip(
        `<strong>${isLot ? "Parking lot" : "Street parking"}</strong><br>${section.parking_name}<br>Section ${section.section_id} · ${section.total_km}km`
      )
      .on("click", () => onSelectSection(null))
      .addTo(group);
  }, [sections, selectedSectionId, onSelectSection]);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: "100%", background: "#1e1e2e" }}
    />
  );
}
