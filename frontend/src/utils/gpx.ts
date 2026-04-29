import type { GeoFeature, ParkingType } from "../types";

const GPX_CREATOR = "City Scour";
const WALK_KMH = 5;
const JOIN_EPS = 1e-9;

export interface ParkingAnchor {
  lat: number;
  lng: number;
  name: string;
  type: ParkingType;
}

export interface WalkGpxInput {
  sectionId: number;
  sectionName: string;
  walkIndex: number;
  walkTotal: number;
  totalKm: number;
  edges: GeoFeature[];
  parking: ParkingAnchor;
}

export interface SectionGpxInput {
  sectionId: number;
  sectionName: string;
  parking: ParkingAnchor;
  walks: Array<{
    walkIndex: number;
    walkTotal: number;
    totalKm: number;
    edges: GeoFeature[];
  }>;
}

export interface LatLon {
  lat: number;
  lon: number;
}

export function sanitizeForFilename(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function gpxFilenameForWalk(
  sectionId: number,
  sectionName: string | null,
  walkIndex: number
): string {
  const slug = sectionName ? sanitizeForFilename(sectionName) : "";
  const base = slug || `section-${sectionId}`;
  return `${base}-walk-${walkIndex}.gpx`;
}

export function gpxFilenameForSection(
  sectionId: number,
  sectionName: string | null
): string {
  const slug = sectionName ? sanitizeForFilename(sectionName) : "";
  const base = slug || `section-${sectionId}`;
  return `${base}-walks.gpx`;
}

function approxEqual(a: number[], b: number[]): boolean {
  return Math.abs(a[0] - b[0]) < JOIN_EPS && Math.abs(a[1] - b[1]) < JOIN_EPS;
}

function sqDist(a: number[], b: number[]): number {
  const dx = a[0] - b[0];
  const dy = a[1] - b[1];
  return dx * dx + dy * dy;
}

/**
 * Concatenate edge LineStrings into a single track polyline.
 * Input coordinates are GeoJSON `[lng, lat]`. Output is `{lat, lon}`.
 * Each edge after the first is reversed if its tail joins the previous tail
 * better than its head, then its first vertex is dropped to dedupe shared
 * join nodes. Consecutive duplicate points are also removed.
 */
export function concatEdgeCoords(edges: GeoFeature[]): LatLon[] {
  const out: LatLon[] = [];
  let lastLngLat: number[] | null = null;
  for (let i = 0; i < edges.length; i++) {
    const geom = edges[i].geometry;
    if (!geom || geom.type !== "LineString") continue;
    const raw = geom.coordinates as number[][];
    if (!raw || raw.length === 0) continue;
    let coords: number[][] = raw;
    if (lastLngLat !== null && coords.length >= 2) {
      const dHead = sqDist(coords[0], lastLngLat);
      const dTail = sqDist(coords[coords.length - 1], lastLngLat);
      if (dTail < dHead) coords = coords.slice().reverse();
    }
    const startIdx: number =
      lastLngLat !== null && approxEqual(coords[0], lastLngLat) ? 1 : 0;
    for (let j: number = startIdx; j < coords.length; j++) {
      const c: number[] = coords[j];
      out.push({ lat: c[1], lon: c[0] });
      lastLngLat = c;
    }
  }
  return out;
}

interface TrackInfo {
  name: string;
  desc: string;
  points: LatLon[];
}

function fmt(n: number): string {
  return n.toFixed(7).replace(/\.?0+$/, "") || "0";
}

function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function bboxOfPoints(pts: LatLon[]): {
  minlat: number;
  minlon: number;
  maxlat: number;
  maxlon: number;
} | null {
  if (pts.length === 0) return null;
  let minlat = pts[0].lat,
    maxlat = pts[0].lat,
    minlon = pts[0].lon,
    maxlon = pts[0].lon;
  for (let i = 1; i < pts.length; i++) {
    const p = pts[i];
    if (p.lat < minlat) minlat = p.lat;
    if (p.lat > maxlat) maxlat = p.lat;
    if (p.lon < minlon) minlon = p.lon;
    if (p.lon > maxlon) maxlon = p.lon;
  }
  return { minlat, minlon, maxlat, maxlon };
}

function parkingDesc(parking: ParkingAnchor): string {
  const kind = parking.type === "lot" ? "Parking lot" : "Street parking";
  return `${kind} — ${parking.name}`;
}

function buildParkingWpt(parking: ParkingAnchor): string {
  return [
    `  <wpt lat="${fmt(parking.lat)}" lon="${fmt(parking.lng)}">`,
    `    <name>${escapeXml(parking.name)}</name>`,
    `    <sym>Parking Area</sym>`,
    `    <desc>${escapeXml(parkingDesc(parking))}</desc>`,
    `  </wpt>`,
  ].join("\n");
}

function edgeStats(edges: GeoFeature[]): { publicCount: number; privateCount: number } {
  let pub = 0;
  let priv = 0;
  for (const e of edges) {
    const p = e.properties as { is_private?: boolean } | undefined;
    if (p?.is_private) priv++;
    else pub++;
  }
  return { publicCount: pub, privateCount: priv };
}

function buildTrack(info: TrackInfo): string {
  const lines: string[] = [];
  lines.push(`  <trk>`);
  lines.push(`    <name>${escapeXml(info.name)}</name>`);
  lines.push(`    <desc>${escapeXml(info.desc)}</desc>`);
  lines.push(`    <trkseg>`);
  for (const p of info.points) {
    lines.push(`      <trkpt lat="${fmt(p.lat)}" lon="${fmt(p.lon)}"/>`);
  }
  lines.push(`    </trkseg>`);
  lines.push(`  </trk>`);
  return lines.join("\n");
}

function walkTrackInfo(
  sectionId: number,
  sectionName: string,
  walkIndex: number,
  walkTotal: number,
  totalKm: number,
  edges: GeoFeature[],
  points: LatLon[]
): TrackInfo {
  const minutes = Math.round((totalKm / WALK_KMH) * 60);
  const { publicCount, privateCount } = edgeStats(edges);
  const name = `Section ${sectionId} (${sectionName}) — Walk ${walkIndex} of ${walkTotal} (${totalKm} km)`;
  const desc =
    `~${minutes} min at ${WALK_KMH} km/h · ${edges.length} edges ` +
    `(${publicCount} public, ${privateCount} private) · ${points.length} points`;
  return { name, desc, points };
}

function gpxDocument(
  bounds: ReturnType<typeof bboxOfPoints>,
  bodyParts: string[],
  isoTime: string
): string {
  const header =
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<gpx version="1.1" creator="${GPX_CREATOR}" ` +
    `xmlns="http://www.topografix.com/GPX/1/1" ` +
    `xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" ` +
    `xsi:schemaLocation="http://www.topografix.com/GPX/1/1 ` +
    `http://www.topografix.com/GPX/1/1/gpx.xsd">`;
  const meta: string[] = [`  <metadata>`, `    <time>${isoTime}</time>`];
  if (bounds) {
    meta.push(
      `    <bounds minlat="${fmt(bounds.minlat)}" minlon="${fmt(bounds.minlon)}" ` +
        `maxlat="${fmt(bounds.maxlat)}" maxlon="${fmt(bounds.maxlon)}"/>`
    );
  }
  meta.push(`  </metadata>`);
  return [header, meta.join("\n"), ...bodyParts, `</gpx>`, ""].join("\n");
}

export function buildWalkGpx(input: WalkGpxInput, now: Date = new Date()): string {
  const points = concatEdgeCoords(input.edges);
  const bounds = bboxOfPoints(points);
  const trackInfo = walkTrackInfo(
    input.sectionId,
    input.sectionName,
    input.walkIndex,
    input.walkTotal,
    input.totalKm,
    input.edges,
    points
  );
  const body = [buildParkingWpt(input.parking), buildTrack(trackInfo)];
  return gpxDocument(bounds, body, now.toISOString());
}

export function buildSectionGpx(input: SectionGpxInput, now: Date = new Date()): string {
  const allPoints: LatLon[] = [];
  const tracks: string[] = [];
  for (const w of input.walks) {
    const points = concatEdgeCoords(w.edges);
    allPoints.push(...points);
    tracks.push(
      buildTrack(
        walkTrackInfo(
          input.sectionId,
          input.sectionName,
          w.walkIndex,
          w.walkTotal,
          w.totalKm,
          w.edges,
          points
        )
      )
    );
  }
  const bounds = bboxOfPoints(allPoints);
  const body = [buildParkingWpt(input.parking), ...tracks];
  return gpxDocument(bounds, body, now.toISOString());
}

export function triggerGpxDownload(filename: string, content: string): void {
  const blob = new Blob([content], { type: "application/gpx+xml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
