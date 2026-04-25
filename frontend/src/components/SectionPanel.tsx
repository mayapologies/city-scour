import { useState } from "react";
import type { Section, EdgeStatus, Progress, Walk } from "../types";
import { getOverallStats, computeSectionStatus } from "../utils/storage";

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
  progress: Progress;
  loadingWalks: boolean;
  loadingWalkDetail: boolean;
  onSelectSection: (id: number | null) => void;
  onSelectWalk: (id: string | null) => void;
  onMarkSection: (id: number, status: EdgeStatus) => void;
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
  progress,
  loadingWalks,
  loadingWalkDetail,
  onSelectSection,
  onSelectWalk,
  onMarkSection,
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
  const stats = getOverallStats(sections, progress);

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
            Hours per walk: <strong>{hoursDraft.toFixed(2)} h</strong>
            <input
              type="range"
              min={0.25}
              max={4}
              step={0.25}
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
          <div
            style={{
              display: "flex",
              gap: 16,
              marginTop: 8,
              fontSize: 11,
              color: "#94a3b8",
            }}
          >
            <span>🚶 {stats.walkedEdges} walked</span>
            <span>🚗 {stats.drivenEdges} driven</span>
            <span>
              📋 {stats.sectionsComplete}/{stats.totalSections} sections
            </span>
          </div>
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
          <div style={parkingBadgeStyle(selectedSection.parking_type)}>
            {selectedSection.parking_type === "lot" ? "🅿" : "🛣"}{" "}
            {selectedSection.parking_name}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button
              style={primaryBtnStyle}
              onClick={() => onMarkSection(selectedSection.section_id, "walked")}
            >
              ✓ Mark walked
            </button>
            <button
              style={secondaryBtnStyle}
              onClick={() => onMarkSection(selectedSection.section_id, "driven")}
            >
              🚗 Mark driven
            </button>
          </div>

          {/* Walks list */}
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 11, color: "#64748b", marginBottom: 6 }}>
              WALKS ({walks.length}) · {hoursPerWalk.toFixed(2)}h target
              {loadingWalks && " · ⏳"}
            </div>
            {walks.map((w, i) => {
              const isWalkSelected = w.walk_id === selectedWalkId;
              const walkedFraction =
                w.edge_ids.filter(
                  (id) =>
                    progress.edges[id] === "walked" ||
                    progress.edges[id] === "driven"
                ).length / Math.max(w.edge_ids.length, 1);
              return (
                <button
                  key={w.walk_id}
                  style={{
                    ...walkItemStyle,
                    background: isWalkSelected ? "#1e293b" : "transparent",
                    borderColor: isWalkSelected ? "#facc15" : "#1e293b",
                  }}
                  onClick={() =>
                    onSelectWalk(isWalkSelected ? null : w.walk_id)
                  }
                >
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#e2e8f0", fontSize: 12 }}>
                      Walk {i + 1}
                      {isWalkSelected && loadingWalkDetail && " ⏳"}
                    </span>
                    <span style={{ color: "#94a3b8", fontSize: 11 }}>
                      {w.total_km}km · {w.est_hours}h
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
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Section list */}
      <div style={sectionListStyle}>
        <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8, padding: "0 4px" }}>
          SECTIONS ({sections.length}) — click a section on the map or below
        </div>
        {sections.map((s) => {
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

const secondaryBtnStyle: React.CSSProperties = {
  flex: 1,
  padding: "6px 10px",
  background: "#1e3a5f",
  color: "#bfdbfe",
  border: "1px solid #1d4ed8",
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
