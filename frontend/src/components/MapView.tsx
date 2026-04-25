import { useEffect, useRef } from "react";
import L from "leaflet";
import type { GeoFeature, Section, EdgeStatus, Progress } from "../types";

// Distinct colors for sections (cycles for > 20 sections)
const SECTION_COLORS = [
  "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
  "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
  "#469990", "#dcbeff", "#9a6324", "#fffac8", "#800000",
  "#aaffc3", "#808000", "#ffd8b1", "#000075", "#a9a9a9",
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
  if (status === "walked") return "#22c55e";
  if (status === "driven") return "#60a5fa";
  if (isHighway) return "#94a3b8";
  if (selectedSectionId !== null && sectionId === selectedSectionId) {
    return "#fff";
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
        };

        const color = edgeColor(
          props.edge_id,
          props.is_highway,
          sid,
          selectedSectionId,
          progress
        );
        const weight = edgeWeight(props.is_highway, isSelected);
        const opacity = selectedSectionId === null || isSelected ? 1 : 0.3;

        const polyline = L.geoJSON(feat as GeoJSON.Feature, {
          style: { color, weight, opacity },
        });

        polyline.on("click", () => {
          const status = progress.edges[props.edge_id] ?? "unvisited";
          onEdgeClick(props.edge_id, props.is_highway, status);
        });

        polyline.bindTooltip(
          `<strong>${props.name || props.highway || "road"}</strong><br>` +
            `Section ${sid} · ${Math.round(props.length)}m`,
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

  // Parking markers — one per section, anchored at parking_lat/lng
  useEffect(() => {
    const group = parkingLayersRef.current;
    if (!group) return;
    group.clearLayers();

    for (const section of sections) {
      const isSelected = section.section_id === selectedSectionId;
      const isLot = section.parking_type === "lot";
      const bg = isLot ? "#6366f1" : "#475569";
      const symbol = isLot ? "P" : "S";
      const dim = isSelected || selectedSectionId === null ? 1 : 0.35;

      const icon = L.divIcon({
        html: `<div style="opacity:${dim};background:${bg};color:#fff;border-radius:50%;width:22px;height:22px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;border:2px solid white;box-shadow:0 2px 4px rgba(0,0,0,.4)">${symbol}</div>`,
        className: "",
        iconAnchor: [11, 11],
      });

      L.marker([section.parking_lat, section.parking_lng], { icon })
        .bindTooltip(
          `<strong>${isLot ? "Parking lot" : "Street parking"}</strong><br>${section.parking_name}<br>Section ${section.section_id} · ${section.total_km}km`
        )
        .on("click", () =>
          onSelectSection(isSelected ? null : section.section_id)
        )
        .addTo(group);
    }
  }, [sections, selectedSectionId, onSelectSection]);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: "100%", background: "#1e1e2e" }}
    />
  );
}
