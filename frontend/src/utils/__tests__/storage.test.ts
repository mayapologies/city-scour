import { describe, expect, it } from "vitest";
import type { GeoFeature, Progress, Section } from "../../types";
import { getOverallStats } from "../storage";

function edge(edgeId: string, lengthMeters: number): GeoFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [] },
    properties: {
      edge_id: edgeId,
      u: 0,
      v: 0,
      name: "",
      highway: "residential",
      is_highway: false,
      length: lengthMeters,
      is_private: false,
    },
  };
}

function section(
  sectionId: number,
  totalKm: number,
  edges: GeoFeature[]
): Section {
  return {
    section_id: sectionId,
    parking_type: "lot",
    parking_name: "",
    parking_lat: 0,
    parking_lng: 0,
    parking_anchor_key: `anchor-${sectionId}`,
    total_km: totalKm,
    estimated_hours: 0,
    bbox: [0, 0, 0, 0],
    edge_ids: edges.map(
      (e) => (e.properties as { edge_id: string }).edge_id
    ),
    edges,
    is_private: false,
  };
}

describe("getOverallStats", () => {
  it("computes totals from sections without needing walks", () => {
    const completed = section(1, 1.5, [edge("a", 600), edge("b", 900)]);
    const untouched = section(2, 2.5, [edge("c", 1200), edge("d", 1300)]);
    const progress: Progress = {
      edges: { a: "complete", b: "complete" },
      sections: {},
    };

    const stats = getOverallStats([completed, untouched], progress);

    expect(stats.totalSections).toBe(2);
    expect(stats.sectionsComplete).toBe(1);
    expect(stats.kmTotal).toBeCloseTo(4.0, 6);
    expect(stats.kmComplete).toBeCloseTo(1.5, 6);
    expect(stats.percentComplete).toBe(38);
  });
});
