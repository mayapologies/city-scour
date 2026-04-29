import { describe, expect, it } from "vitest";
import type { GeoFeature } from "../../types";
import JSZip from "jszip";
import {
  buildSectionZip,
  buildWalkGpx,
  concatEdgeCoords,
  gpxFilenameForWalk,
  sanitizeForFilename,
  zipFilenameForSection,
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
    expect(zipFilenameForSection("San José", 42)).toBe(
      "san-jose-section-42-walks.zip",
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
    expect(gpx).toMatch(
      /<trkpt lat="37\.32" lon="-122\.05"><ele>0<\/ele><time>[^<]+<\/time><\/trkpt>/,
    );
    expect(gpx).toMatch(
      /<trkpt lat="37\.33" lon="-122\.04"><ele>0<\/ele><time>[^<]+<\/time><\/trkpt>/,
    );
    expect(gpx).toMatch(
      /<trkpt lat="37\.34" lon="-122\.03"><ele>0<\/ele><time>[^<]+<\/time><\/trkpt>/,
    );
  });

  it("emits <ele>0</ele> for the parking <wpt> and every <trkpt>", () => {
    expect(countMatches(gpx, /<ele>0<\/ele>/g)).toBe(1 + 3);
  });

  it("includes the duration phrase in the track <desc>", () => {
    const minutes = Math.round((4.2 / 5) * 60);
    expect(gpx).toContain(`~${minutes} min at 5 km/h`);
  });

  it("uses the metadata <time> as the first <trkpt> time and is monotonic", () => {
    const trkptTimes = [
      ...gpx.matchAll(/<trkpt [^>]*><ele>0<\/ele><time>([^<]+)<\/time><\/trkpt>/g),
    ].map((m) => Date.parse(m[1]));
    expect(trkptTimes).toHaveLength(3);
    expect(trkptTimes[0]).toBe(NOW.getTime());
    for (let i = 1; i < trkptTimes.length; i++) {
      expect(trkptTimes[i]).toBeGreaterThan(trkptTimes[i - 1]);
    }
  });

  it("computes the per-trkpt time delta as distance / 5 km/h", () => {
    // Two points exactly 1 km apart along a meridian (R = 6371000 m).
    const oneKmDeg = (1000 / 6371000) * (180 / Math.PI);
    const eOneKm = lineEdge([
      [0, 0],
      [0, oneKmDeg],
    ]);
    const g = buildWalkGpx(
      {
        cityName: "Test City",
        sectionId: 1,
        sectionName: null,
        walkIndex: 1,
        walkTotal: 1,
        totalKm: 1,
        edges: [eOneKm],
        parking: { lat: 0, lng: 0, name: "P", type: "lot" },
      },
      NOW,
    );
    const times = [
      ...g.matchAll(/<trkpt [^>]*><ele>0<\/ele><time>([^<]+)<\/time><\/trkpt>/g),
    ].map((m) => Date.parse(m[1]));
    expect(times).toHaveLength(2);
    // 1 km at 5 km/h = 12 min = 720000 ms.
    expect(times[1] - times[0]).toBe(720000);
  });
});

describe("buildSectionZip", () => {
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
  const sectionInput = {
    cityName: "Cupertino, CA",
    sectionId: 170,
    sectionName: "Oak Valley",
    parking: PARKING,
    walks: [
      { walkIndex: 1, walkTotal: 2, totalKm: 2.0, edges: w1 },
      { walkIndex: 2, walkTotal: 2, totalKm: 2.5, edges: w2 },
    ],
  };

  it("returns a Blob with content-type application/zip", async () => {
    const blob = await buildSectionZip(sectionInput, NOW);
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe("application/zip");
  });

  it("contains exactly one entry per walk, named via gpxFilenameForWalk", async () => {
    const blob = await buildSectionZip(sectionInput, NOW);
    const zip = await JSZip.loadAsync(await blob.arrayBuffer());
    const names = Object.keys(zip.files).sort();
    expect(names).toEqual([
      gpxFilenameForWalk("Cupertino, CA", 170, 1),
      gpxFilenameForWalk("Cupertino, CA", 170, 2),
    ]);
  });

  it("each entry is a valid GPX 1.1 doc with metadata <name> and at least one <trk>", async () => {
    const blob = await buildSectionZip(sectionInput, NOW);
    const zip = await JSZip.loadAsync(await blob.arrayBuffer());
    for (const walkIndex of [1, 2]) {
      const name = gpxFilenameForWalk("Cupertino, CA", 170, walkIndex);
      const content = await zip.file(name)!.async("string");
      expect(content.startsWith('<?xml version="1.0" encoding="UTF-8"?>')).toBe(true);
      expect(content).toContain('<gpx version="1.1" creator="City Scour"');
      expect(content).toContain(
        "<name>City Scour — Cupertino, CA — Section 170 (Oak Valley)</name>",
      );
      expect(countMatches(content, /<trk>/g)).toBe(1);
      expect(content).toContain(
        `Walk ${walkIndex} of 2`,
      );
    }
  });
});

