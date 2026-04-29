import { useState } from "react";
import type { Section, EdgeStatus, Progress, Walk } from "../types";
import {
  getOverallStats,
  computeSectionStatus,
  isWalkComplete,
} from "../utils/storage";

const STATUS_LABELS: Record<string, string> = {
  complete: "✓ Done",
  partial: "~ In progress",
  unvisited: "○ Not started",
};

const STATUS_COLORS: Record<string, string> = {
  complete: "#22c55e",
  partial: "#f59e0b",
  unvisited: "#64748b",
};

interface SectionPanelProps {
  sections: Section[];
  selectedSectionId: number | null;
  selectedWalkId: string | null;
  walks: Walk[];
  walksBySection: Record<number, Walk[]>;
  progress: Progress;
  loadingWalks: boolean;
  loadingWalkDetail: boolean;
  onSelectSection: (id: number | null) => void;
  onSelectWalk: (id: string | null) => void;
  onMarkSection: (id: number, status: EdgeStatus) => void;
  onMarkEdges: (edgeIds: string[], status: EdgeStatus) => void;
  onResetProgress: () => void;
  onUploadBoundary: (file: File) => void;
  onSetHoursPerWalk: (hours: number) => void;
  hoursPerWalk: number;
  cityName: string;
  isLoadingRoads: boolean;
  isLoadingSections: boolean;
}

export function SectionPanel({
  sections,
  selectedSectionId,
  selectedWalkId,
  walks,
  walksBySection,
  progress,
  loadingWalks,
  loadingWalkDetail,
  onSelectSection,
  onSelectWalk,
  onMarkSection,
  onMarkEdges,
  onResetProgress,
  onUploadBoundary,
  onSetHoursPerWalk,
  hoursPerWalk,
  cityName,
  isLoadingRoads,
  isLoadingSections,
}: SectionPanelProps) {
  const [showSettings, setShowSettings] = useState(false);
  const [hoursDraft, setHoursDraft] = useState(hoursPerWalk);
  const [hideCompleted, setHideCompleted] = useState(false);
  const stats = getOverallStats(sections, walksBySection, progress);

  const isSectionFullyComplete = (sectionId: number): boolean => {
    const ws = walksBySection[sectionId];
    if (!ws || ws.length === 0) return false;
    return ws.every((w) => isWalkComplete(w, progress));
  };

  const visibleSections = hideCompleted
    ? sections.filter((s) => !isSectionFullyComplete(s.section_id))
    : sections;
  const visibleWalks = hideCompleted
    ? walks.filter((w) => !isWalkComplete(w, progress))
    : walks;

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) onUploadBoundary(file);
  };

  const selectedSection = sections.find((s) => s.section_id === selectedSectionId);

  return (
    <div style={panelStyle}>
      {/* Header */}
      <div style={headerStyle}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#e2e8f0" }}>
            City Scour
          </div>
          <div style={{ fontSize: 12, color: "#94a3b8" }}>{cityName}</div>
        </div>
        <button
          style={iconBtnStyle}
          onClick={() => setShowSettings((v) => !v)}
          title="Settings"
        >
          ⚙
        </button>
      </div>

      {/* Settings drawer */}
      {showSettings && (
        <div style={settingsStyle}>
          <label style={labelStyle}>
            Upload boundary file
            <input
              type="file"
              accept=".geojson,.json,.kml,.zip,.csv"
              onChange={handleFileChange}
              style={{ display: "block", marginTop: 4, color: "#e2e8f0", fontSize: 12 }}
            />
          </label>
          <label style={{ ...labelStyle, marginTop: 12 }}>
            Hours per walk: <strong>{hoursDraft.toFixed(1)} h</strong>{" "}
            <span style={{ color: "#475569" }}>(0.5–2.0)</span>
            <input
              type="range"
              min={0.5}
              max={2}
              step={0.5}
              value={hoursDraft}
              onChange={(e) => setHoursDraft(Number(e.target.value))}
              onMouseUp={() => onSetHoursPerWalk(hoursDraft)}
              onTouchEnd={() => onSetHoursPerWalk(hoursDraft)}
              style={{ display: "block", width: "100%", marginTop: 4 }}
            />
          </label>
          <button
            style={{ ...dangerBtnStyle, marginTop: 12 }}
            onClick={() => {
              if (confirm("Reset all progress?")) onResetProgress();
            }}
          >
            Reset progress
          </button>
        </div>
      )}

      {/* Loading states */}
      {(isLoadingRoads || isLoadingSections) && (
        <div style={loadingStyle}>
          {isLoadingRoads
            ? "⏳ Fetching road network from OpenStreetMap…"
            : "⏳ Building section plan…"}
        </div>
      )}

      {/* Overall progress */}
      {sections.length > 0 && (
        <div style={progressBlockStyle}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
            <span style={{ fontSize: 13, color: "#94a3b8" }}>Overall progress</span>
            <span style={{ fontSize: 13, fontWeight: 700, color: "#e2e8f0" }}>
              {stats.percentComplete}%
            </span>
          </div>
          <div style={progressBarBgStyle}>
            <div
              style={{
                ...progressBarFillStyle,
                width: `${stats.percentComplete}%`,
              }}
            />
          </div>
          <div style={statsLineStyle}>
            <span>
              <strong style={statsNumStyle}>{stats.sectionsComplete}</strong> /{" "}
              {stats.totalSections} sections
            </span>
            <span style={statsDotStyle}>·</span>
            <span>
              <strong style={statsNumStyle}>{stats.kmComplete.toFixed(1)}</strong> /{" "}
              {stats.walksLoaded ? stats.kmTotal.toFixed(1) : "…"} km
            </span>
          </div>
          <div style={statsLineStyle}>
            <span>~{stats.hoursWalked.toFixed(1)} h walked</span>
            <span style={statsDotStyle}>·</span>
            <span>
              ~
              {stats.walksLoaded ? stats.hoursRemaining.toFixed(1) : "…"} h
              remaining
            </span>
          </div>
          <label style={hideToggleStyle}>
            <input
              type="checkbox"
              checked={hideCompleted}
              onChange={(e) => setHideCompleted(e.target.checked)}
              style={{ accentColor: "#22c55e" }}
            />
            Hide completed
          </label>
        </div>
      )}

      {/* Selected section detail */}
      {selectedSection && (
        <div style={sectionDetailStyle}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 600, color: "#e2e8f0" }}>
              Section {selectedSection.section_id}
            </div>
            <button
              style={closeBtnStyle}
              onClick={() => onSelectSection(null)}
            >
              ✕
            </button>
          </div>
          <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>
            {selectedSection.total_km} km · ~{selectedSection.estimated_hours}h walk
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <div style={parkingBadgeStyle(selectedSection.parking_type)}>
              {selectedSection.parking_type === "lot" ? "🅿" : "🛣"}{" "}
              {selectedSection.parking_name}
            </div>
            {selectedSection.is_private && (
              <span
                style={privateBadgeStyle}
                title="Contains private roads — may be gated"
              >
                🔒
              </span>
            )}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button
              style={
                isSectionFullyComplete(selectedSection.section_id)
                  ? unmarkBtnStyle
                  : primaryBtnStyle
              }
              onClick={() => {
                const ws = walksBySection[selectedSection.section_id] ?? [];
                const allDone =
                  ws.length > 0 && ws.every((w) => isWalkComplete(w, progress));
                onMarkSection(
                  selectedSection.section_id,
                  allDone ? "unvisited" : "complete"
                );
              }}
            >
              {isSectionFullyComplete(selectedSection.section_id)
                ? "↺ Unmark section"
                : "✓ Mark section complete"}
            </button>
          </div>

          {/* Walks list */}
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 11, color: "#64748b", marginBottom: 6 }}>
              WALKS ({visibleWalks.length}
              {hideCompleted && walks.length !== visibleWalks.length
                ? `/${walks.length}`
                : ""}
              ) · {hoursPerWalk.toFixed(1)}h target
              {loadingWalks && " · ⏳"}
            </div>
            {visibleWalks.map((w) => {
              const i = walks.indexOf(w);
              const isWalkSelected = w.walk_id === selectedWalkId;
              const walkComplete = isWalkComplete(w, progress);
              const walkedFraction =
                w.edge_ids.filter((id) => progress.edges[id] === "complete")
                  .length / Math.max(w.edge_ids.length, 1);
              return (
                <div
                  key={w.walk_id}
                  role="button"
                  tabIndex={0}
                  style={{
                    ...walkItemStyle,
                    background: isWalkSelected ? "#1e293b" : "transparent",
                    borderColor: isWalkSelected ? "#facc15" : "#1e293b",
                  }}
                  onClick={() =>
                    onSelectWalk(isWalkSelected ? null : w.walk_id)
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelectWalk(isWalkSelected ? null : w.walk_id);
                    }
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#e2e8f0", fontSize: 12 }}>
                      Walk {i + 1}
                      {isWalkSelected && loadingWalkDetail && " ⏳"}
                    </span>
                    <span style={{ color: "#94a3b8", fontSize: 11, display: "flex", alignItems: "center", gap: 4 }}>
                      {w.total_km}km · {w.est_hours}h
                      {w.is_private && (
                        <span
                          style={privateChipStyle}
                          title="Walk includes private roads — may be gated/inaccessible"
                        >
                          🔒 private
                        </span>
                      )}
                    </span>
                  </div>
                  <div
                    style={{
                      fontSize: 10,
                      color: "#64748b",
                      marginTop: 2,
                      display: "flex",
                      justifyContent: "space-between",
                    }}
                  >
                    <span>
                      {w.edge_ids.length} edges · {w.backtrack_edge_ids.length}{" "}
                      backtrack
                    </span>
                    <span>{Math.round(walkedFraction * 100)}% done</span>
                  </div>
                  <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                    <button
                      style={walkComplete ? walkBtnUnmarkStyle : walkBtnCompleteStyle}
                      onClick={(e) => {
                        e.stopPropagation();
                        onMarkEdges(
                          w.edge_ids,
                          walkComplete ? "unvisited" : "complete"
                        );
                      }}
                    >
                      {walkComplete ? "↺ Unmark" : "✓ Mark complete"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Section list */}
      <div style={sectionListStyle}>
        <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8, padding: "0 4px" }}>
          SECTIONS ({visibleSections.length}
          {hideCompleted && sections.length !== visibleSections.length
            ? `/${sections.length}`
            : ""}
          ) — click a section on the map or below
        </div>
        {visibleSections.map((s) => {
          const secStatus = computeSectionStatus(s, progress.edges);
          const isSelected = s.section_id === selectedSectionId;
          return (
            <button
              key={s.section_id}
              style={{
                ...sectionItemStyle,
                background: isSelected ? "#1e293b" : "transparent",
                borderLeft: `3px solid ${STATUS_COLORS[secStatus]}`,
              }}
              onClick={() =>
                onSelectSection(isSelected ? null : s.section_id)
              }
            >
              <span
                style={{
                  fontSize: 11,
                  color: s.parking_type === "lot" ? "#818cf8" : "#94a3b8",
                  width: 14,
                }}
                title={s.parking_type === "lot" ? "Parking lot" : "Street parking"}
              >
                {s.parking_type === "lot" ? "🅿" : "🛣"}
              </span>
              <span style={{ color: "#e2e8f0", fontSize: 13, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {s.parking_name}
              </span>
              <span style={{ fontSize: 11, color: "#64748b" }}>
                {s.total_km}km
              </span>
              <span
                style={{
                  fontSize: 11,
                  color: STATUS_COLORS[secStatus],
                }}
              >
                {STATUS_LABELS[secStatus]}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

const parkingBadgeStyle = (type: "lot" | "street"): React.CSSProperties => ({
  fontSize: 12,
  marginTop: 6,
  padding: "3px 8px",
  borderRadius: 4,
  display: "inline-block",
  background: type === "lot" ? "#1e1b4b" : "#1e293b",
  color: type === "lot" ? "#a5b4fc" : "#94a3b8",
  border: `1px solid ${type === "lot" ? "#3730a3" : "#334155"}`,
});

const privateBadgeStyle: React.CSSProperties = {
  fontSize: 12,
  marginTop: 6,
  padding: "3px 6px",
  borderRadius: 4,
  display: "inline-block",
  background: "#1e1b3b",
  color: "#c4b5fd",
  border: "1px solid #5b21b6",
};

const privateChipStyle: React.CSSProperties = {
  fontSize: 9,
  padding: "1px 5px",
  borderRadius: 3,
  background: "#1e1b3b",
  color: "#c4b5fd",
  border: "1px solid #5b21b6",
  whiteSpace: "nowrap",
};

const walkItemStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  textAlign: "left",
  padding: "6px 8px",
  marginBottom: 4,
  background: "transparent",
  border: "1px solid #1e293b",
  borderRadius: 4,
  cursor: "pointer",
};

const walkBtnCompleteStyle: React.CSSProperties = {
  flex: 1,
  padding: "3px 6px",
  background: "#14532d",
  color: "#bbf7d0",
  border: "1px solid #166534",
  borderRadius: 4,
  cursor: "pointer",
  fontSize: 10,
};

const walkBtnUnmarkStyle: React.CSSProperties = {
  flex: 1,
  padding: "3px 6px",
  background: "#1e293b",
  color: "#cbd5e1",
  border: "1px solid #334155",
  borderRadius: 4,
  cursor: "pointer",
  fontSize: 10,
};

const statsLineStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  marginTop: 8,
  fontSize: 11,
  color: "#94a3b8",
  flexWrap: "wrap",
};

const statsNumStyle: React.CSSProperties = {
  color: "#e2e8f0",
  fontWeight: 600,
};

const statsDotStyle: React.CSSProperties = {
  color: "#475569",
};

const hideToggleStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  marginTop: 10,
  fontSize: 11,
  color: "#94a3b8",
  cursor: "pointer",
  userSelect: "none",
};

const unmarkBtnStyle: React.CSSProperties = {
  flex: 1,
  padding: "6px 10px",
  background: "#1e293b",
  color: "#cbd5e1",
  border: "1px solid #334155",
  borderRadius: 6,
  cursor: "pointer",
  fontSize: 12,
};

/* ── Styles ── */
const panelStyle: React.CSSProperties = {
  width: 300,
  height: "100%",
  background: "#0f172a",
  borderRight: "1px solid #1e293b",
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
  fontFamily: "system-ui, sans-serif",
};

const headerStyle: React.CSSProperties = {
  padding: "14px 16px",
  borderBottom: "1px solid #1e293b",
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
};

const settingsStyle: React.CSSProperties = {
  padding: "12px 16px",
  borderBottom: "1px solid #1e293b",
  background: "#0a0f1e",
};

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#94a3b8",
  display: "block",
};

const loadingStyle: React.CSSProperties = {
  padding: "10px 16px",
  fontSize: 12,
  color: "#f59e0b",
  background: "#1c1304",
  borderBottom: "1px solid #1e293b",
};

const progressBlockStyle: React.CSSProperties = {
  padding: "12px 16px",
  borderBottom: "1px solid #1e293b",
};

const progressBarBgStyle: React.CSSProperties = {
  height: 6,
  background: "#1e293b",
  borderRadius: 3,
  overflow: "hidden",
};

const progressBarFillStyle: React.CSSProperties = {
  height: "100%",
  background: "linear-gradient(90deg, #22c55e, #4ade80)",
  borderRadius: 3,
  transition: "width 0.4s ease",
};

const sectionDetailStyle: React.CSSProperties = {
  padding: "12px 16px",
  borderBottom: "1px solid #1e293b",
  background: "#0c1526",
  maxHeight: "50vh",
  overflowY: "auto",
  flexShrink: 0,
};

const sectionListStyle: React.CSSProperties = {
  flex: 1,
  overflowY: "auto",
  padding: "8px 0",
};

const sectionItemStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 12px 8px 10px",
  background: "transparent",
  border: "none",
  borderLeft: "3px solid transparent",
  borderBottom: "1px solid #0f1929",
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  gap: 8,
  textAlign: "left",
};

const iconBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  cursor: "pointer",
  color: "#64748b",
  fontSize: 18,
};

const closeBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  cursor: "pointer",
  color: "#64748b",
  fontSize: 16,
};

const primaryBtnStyle: React.CSSProperties = {
  flex: 1,
  padding: "6px 10px",
  background: "#166534",
  color: "#bbf7d0",
  border: "1px solid #15803d",
  borderRadius: 6,
  cursor: "pointer",
  fontSize: 12,
};

const dangerBtnStyle: React.CSSProperties = {
  padding: "6px 12px",
  background: "#450a0a",
  color: "#fca5a5",
  border: "1px solid #7f1d1d",
  borderRadius: 6,
  cursor: "pointer",
  fontSize: 12,
};
