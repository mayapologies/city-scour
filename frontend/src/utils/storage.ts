import type { EdgeStatus, Progress, Section, Walk } from "../types";

const STORAGE_KEY = "city-scour-progress";
const SECTION_NAMES_KEY = "city-scour-section-names";
const SECTION_NAMES_VERSION_KEY = "city-scour-section-names-v";

export function loadSectionNames(): Record<string, string> {
  try {
    const raw = localStorage.getItem(SECTION_NAMES_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(parsed)) {
        if (typeof v === "string" && v.length > 0) out[k] = v;
      }
      return out;
    }
  } catch {
    // ignore corrupt storage
  }
  return {};
}

export function saveSectionName(
  anchorKey: string,
  name: string
): Record<string, string> {
  const all = loadSectionNames();
  const trimmed = name.trim();
  if (trimmed.length === 0) {
    delete all[anchorKey];
  } else {
    all[anchorKey] = trimmed;
  }
  try {
    localStorage.setItem(SECTION_NAMES_KEY, JSON.stringify(all));
  } catch {
    // ignore quota errors
  }
  return all;
}

/**
 * One-time migration: old format keyed names by section_id (number).
 * After successful migration, set version flag so we never re-run.
 * Called once when sections first load with a non-empty list.
 */
export function migrateSectionNamesIfNeeded(
  sections: Section[]
): Record<string, string> {
  const version = localStorage.getItem(SECTION_NAMES_VERSION_KEY);
  if (version === "2") return loadSectionNames();
  const current = loadSectionNames();
  const numericEntries: Array<[number, string]> = [];
  const newEntries: Record<string, string> = {};
  for (const [k, v] of Object.entries(current)) {
    if (/^[0-9]+$/.test(k)) {
      numericEntries.push([Number(k), v]);
    } else {
      newEntries[k] = v;
    }
  }
  if (numericEntries.length === 0) {
    localStorage.setItem(SECTION_NAMES_VERSION_KEY, "2");
    return current;
  }
  for (const [secId, name] of numericEntries) {
    const sec = sections.find((s) => s.section_id === secId);
    if (sec && sec.parking_anchor_key) {
      newEntries[sec.parking_anchor_key] = name;
    }
    // If section_id no longer matches anything, drop the entry — better to lose
    // a stale name than to anchor it to a wrong section.
  }
  try {
    localStorage.setItem(SECTION_NAMES_KEY, JSON.stringify(newEntries));
    localStorage.setItem(SECTION_NAMES_VERSION_KEY, "2");
  } catch {
    // ignore quota errors
  }
  return newEntries;
}

// Walking pace in km/h (used for hours-walked / hours-remaining stats).
const WALK_KMH = 5.0;

function migrateStatus(raw: unknown): EdgeStatus {
  if (raw === "walked" || raw === "driven" || raw === "complete") return "complete";
  return "unvisited";
}

export function loadProgress(): Progress {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as { edges?: Record<string, unknown> };
      const edges: Record<string, EdgeStatus> = {};
      for (const [k, v] of Object.entries(parsed.edges ?? {})) {
        const status = migrateStatus(v);
        if (status === "complete") edges[k] = status;
      }
      return { edges, sections: {} };
    }
  } catch {
    // ignore
  }
  return { edges: {}, sections: {} };
}

export function saveProgress(progress: Progress): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(progress));
}

export function setEdgeStatus(
  progress: Progress,
  edgeId: string,
  status: EdgeStatus
): Progress {
  const edges = { ...progress.edges };
  if (status === "unvisited") {
    delete edges[edgeId];
  } else {
    edges[edgeId] = status;
  }
  return { ...progress, edges };
}

export function computeSectionStatus(
  section: Section,
  edgeStatuses: Record<string, EdgeStatus>
): "complete" | "partial" | "unvisited" {
  const edgeIds = section.edges.map(
    (f) => (f.properties as { edge_id: string }).edge_id
  );
  if (edgeIds.length === 0) return "unvisited";

  const done = edgeIds.filter((id) => edgeStatuses[id] === "complete").length;

  if (done === 0) return "unvisited";
  if (done === edgeIds.length) return "complete";
  return "partial";
}

export function recomputeSections(
  sections: Section[],
  progress: Progress
): Progress {
  const updated = { ...progress, sections: { ...progress.sections } };
  for (const s of sections) {
    updated.sections[s.section_id] = computeSectionStatus(s, progress.edges);
  }
  return updated;
}

export function isWalkComplete(walk: Walk, progress: Progress): boolean {
  if (walk.edge_ids.length === 0) return false;
  return walk.edge_ids.every((id) => progress.edges[id] === "complete");
}

export interface OverallStats {
  totalSections: number;
  sectionsComplete: number;
  kmTotal: number;
  kmComplete: number;
  hoursWalked: number;
  hoursRemaining: number;
  percentComplete: number;
  walksLoaded: boolean;
}

export function getOverallStats(
  sections: Section[],
  walksBySection: Record<number, Walk[]>,
  progress: Progress
): OverallStats {
  let kmTotal = 0;
  let kmComplete = 0;
  let sectionsComplete = 0;
  let walksLoaded = sections.length > 0;

  for (const section of sections) {
    const walks = walksBySection[section.section_id];
    if (!walks) {
      walksLoaded = false;
      continue;
    }
    let allWalksDone = walks.length > 0;
    for (const w of walks) {
      kmTotal += w.total_km;
      if (isWalkComplete(w, progress)) {
        kmComplete += w.total_km;
      } else {
        allWalksDone = false;
      }
    }
    if (allWalksDone) sectionsComplete++;
  }

  const kmRemaining = Math.max(0, kmTotal - kmComplete);
  return {
    totalSections: sections.length,
    sectionsComplete,
    kmTotal,
    kmComplete,
    hoursWalked: kmComplete / WALK_KMH,
    hoursRemaining: kmRemaining / WALK_KMH,
    percentComplete: kmTotal > 0 ? Math.round((kmComplete / kmTotal) * 100) : 0,
    walksLoaded,
  };
}
