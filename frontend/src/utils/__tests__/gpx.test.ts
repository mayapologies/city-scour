import { describe, expect, it } from "vitest";
import type { GeoFeature } from "../../types";
import {
  buildSectionGpx,
  buildWalkGpx,
  concatEdgeCoords,
  gpxFilenameForSection,
  gpxFilenameForWalk,
  sanitizeForFilename,
  type ParkingAnchor,
} from "../gpx";

const PARKING: ParkingAnchor = {
  lat: 37.32,
  lng: -122.05,
  name: "Oak Valley Lot",
  type: "lot",
};

function lineEdge(coords: [number, number][], opts: Partial<{ edge_id: string; is_private: boolean; length: number }> = {}): GeoFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      edge_id: opts.edge_id ?? `e-${Math.random().toString(36).slice(2, 7)}`,
      u: 0,
      v: 0,
      name: "",
      highway: "residential",
      is_highway: false,
      length: opts.length ?? 100,
      is_private: opts.is_private ?? false,
    },
  };
}

const NOW = new Date("2026-04-29T12:00:00.000Z");

describe("sanitizeForFilename", () => {
  it("lowercases, hyphenates, and trims punctuation", () => {
    expect(sanitizeForFilename("My Park / Trail")).toBe("my-park-trail");
    expect(sanitizeForFilename("  Hello!! World  ")).toBe("hello-world");
    expect(sanitizeForFilename("Already-Clean")).toBe("already-clean");
    expect(sanitizeForFilename("--__--")).toBe("");
  });

  it("strips diacritics and produces city slugs", () => {
    expect(sanitizeForFilename("Cupertino, CA")).toBe("cupertino-ca");
    expect(sanitizeForFilename("San José")).toBe("san-jose");
    expect(sanitizeForFilename("Mountain View")).toBe("mountain-view");
  });
});

describe("gpx filenames", () => {
  it("prefixes city slug to section/walk filenames", () => {
    expect(gpxFilenameForWalk("Cupertino, CA", 170, 3)).toBe(
      "cupertino-ca-section-170-walk-3.gpx",
    );
    expect(gpxFilenameForSection("San José", 42)).toBe(
      "san-jose-section-42-walks.gpx",
    );
  });
});

describe("concatEdgeCoords", () => {
  it("dedupes shared join nodes between consecutive edges", () => {
    const edges = [
      lineEdge([
        [-122.05, 37.32],
        [-122.04, 37.33],
      ]),
      lineEdge([
        [-122.04, 37.33],
        [-122.03, 37.34],
      ]),
    ];
    const pts = concatEdgeCoords(edges);
    expect(pts).toEqual([
      { lat: 37.32, lon: -122.05 },
      { lat: 37.33, lon: -122.04 },
      { lat: 37.34, lon: -122.03 },
    ]);
  });

  it("reverses an edge whose tail joins the previous edge's tail", () => {
    const edges = [
      lineEdge([
        [-122.05, 37.32],
        [-122.04, 37.33],
      ]),
      lineEdge([
        [-122.03, 37.34],
        [-122.04, 37.33],
      ]),
    ];
    const pts = concatEdgeCoords(edges);
    expect(pts).toEqual([
      { lat: 37.32, lon: -122.05 },
      { lat: 37.33, lon: -122.04 },
      { lat: 37.34, lon: -122.03 },
    ]);
    for (let i = 1; i < pts.length; i++) {
      const dLat = Math.abs(pts[i].lat - pts[i - 1].lat);
      const dLon = Math.abs(pts[i].lon - pts[i - 1].lon);
      expect(Math.max(dLat, dLon)).toBeLessThan(0.05);
    }
  });

  it("skips empty / non-LineString geometries", () => {
    const edges = [
      lineEdge([
        [-122.05, 37.32],
        [-122.04, 37.33],
      ]),
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [0, 0] },
        properties: {},
      } as GeoFeature,
    ];
    expect(concatEdgeCoords(edges)).toHaveLength(2);
  });
});

function countMatches(s: string, re: RegExp): number {
  return (s.match(re) ?? []).length;
}

describe("buildWalkGpx", () => {
  const edges = [
    lineEdge([
      [-122.05, 37.32],
      [-122.04, 37.33],
    ]),
    lineEdge([
      [-122.04, 37.33],
      [-122.03, 37.34],
    ]),
  ];
  const gpx = buildWalkGpx(
    {
      cityName: "Cupertino, CA",
      sectionId: 170,
      sectionName: "Oak Valley",
      walkIndex: 1,
      walkTotal: 5,
      totalKm: 4.2,
      edges,
      parking: PARKING,
    },
    NOW,
  );

  it("emits a GPX 1.1 declaration with the City Scour creator", () => {
    expect(gpx.startsWith('<?xml version="1.0" encoding="UTF-8"?>')).toBe(true);
    expect(gpx).toContain('<gpx version="1.1" creator="City Scour"');
    expect(gpx).toContain('xmlns="http://www.topografix.com/GPX/1/1"');
    expect(gpx).toContain(`<time>${NOW.toISOString()}</time>`);
    expect(gpx).toContain("<bounds minlat=");
  });

  it("includes metadata <name> and <keywords> with city slug", () => {
    expect(gpx).toContain(
      "<name>City Scour — Cupertino, CA — Section 170 (Oak Valley)</name>",
    );
    expect(gpx).toContain("<keywords>city-scour, cupertino-ca</keywords>");
  });

  it("includes exactly one <wpt> for the parking anchor with sym=Parking Area", () => {
    expect(countMatches(gpx, /<wpt /g)).toBe(1);
    expect(gpx).toContain("<sym>Parking Area</sym>");
    expect(gpx).toContain("<name>Oak Valley Lot</name>");
    expect(gpx).toContain('<wpt lat="37.32" lon="-122.05">');
    expect(gpx).toContain(
      "<desc>Parking for Section 170 in Cupertino, CA (lot)</desc>",
    );
  });

  it("emits exactly one <trk> with the expected name", () => {
    expect(countMatches(gpx, /<trk>/g)).toBe(1);
    expect(gpx).toContain(
      "<name>Cupertino, CA — Section 170 (Oak Valley) — Walk 1 of 5 (4.2 km)</name>",
    );
  });

  it("drops empty parens when sectionName is null", () => {
    const g2 = buildWalkGpx(
      {
        cityName: "Cupertino, CA",
        sectionId: 170,
        sectionName: null,
        walkIndex: 1,
        walkTotal: 5,
        totalKm: 4.2,
        edges,
        parking: PARKING,
      },
      NOW,
    );
    expect(g2).toContain(
      "<name>Cupertino, CA — Section 170 — Walk 1 of 5 (4.2 km)</name>",
    );
    expect(g2).not.toContain("Section 170 ()");
  });

  it("emits one <trkpt> per unique vertex with lat/lon ordering correct", () => {
    expect(countMatches(gpx, /<trkpt /g)).toBe(3);
    expect(gpx).toContain('<trkpt lat="37.32" lon="-122.05"/>');
    expect(gpx).toContain('<trkpt lat="37.33" lon="-122.04"/>');
    expect(gpx).toContain('<trkpt lat="37.34" lon="-122.03"/>');
  });
});

describe("buildSectionGpx", () => {
  const w1 = [
    lineEdge([
      [-122.05, 37.32],
      [-122.04, 37.33],
    ]),
  ];
  const w2 = [
    lineEdge([
      [-122.05, 37.32],
      [-122.06, 37.31],
    ]),
  ];
  const gpx = buildSectionGpx(
    {
      cityName: "Cupertino, CA",
      sectionId: 170,
      sectionName: "Oak Valley",
      parking: PARKING,
      walks: [
        { walkIndex: 1, walkTotal: 2, totalKm: 2.0, edges: w1 },
        { walkIndex: 2, walkTotal: 2, totalKm: 2.5, edges: w2 },
      ],
    },
    NOW,
  );

  it("includes one <wpt> and N <trk> elements for N walks", () => {
    expect(countMatches(gpx, /<wpt /g)).toBe(1);
    expect(countMatches(gpx, /<trk>/g)).toBe(2);
  });

  it("preserves the supplied walk order in the track names", () => {
    const walk1Idx = gpx.indexOf("Walk 1 of 2");
    const walk2Idx = gpx.indexOf("Walk 2 of 2");
    expect(walk1Idx).toBeGreaterThan(-1);
    expect(walk2Idx).toBeGreaterThan(walk1Idx);
  });

  it("computes bounds across the union of all tracks", () => {
    const m = gpx.match(
      /<bounds minlat="([-\d.]+)" minlon="([-\d.]+)" maxlat="([-\d.]+)" maxlon="([-\d.]+)"/,
    );
    expect(m).not.toBeNull();
    const [, minlat, minlon, maxlat, maxlon] = m!;
    expect(parseFloat(minlat)).toBeCloseTo(37.31, 5);
    expect(parseFloat(maxlat)).toBeCloseTo(37.33, 5);
    expect(parseFloat(minlon)).toBeCloseTo(-122.06, 5);
    expect(parseFloat(maxlon)).toBeCloseTo(-122.04, 5);
  });
});

