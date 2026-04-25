import { useState, useEffect, useCallback } from "react";
import { MapView } from "./components/MapView";
import { SectionPanel } from "./components/SectionPanel";
import { useProgress } from "./hooks/useProgress";
import { api } from "./utils/api";
import type {
  GeoFeature,
  Section,
  EdgeStatus,
  Walk,
  WalkDetail,
} from "./types";

export default function App() {
  const [boundary, setBoundary] = useState<GeoFeature | null>(null);
  const [cityName, setCityName] = useState("Loading…");
  const [sections, setSections] = useState<Section[]>([]);
  const [selectedSectionId, setSelectedSectionId] = useState<number | null>(null);
  const [walksBySection, setWalksBySection] = useState<Record<number, Walk[]>>({});
  const [loadingWalks, setLoadingWalks] = useState(false);
  const [selectedWalkId, setSelectedWalkId] = useState<string | null>(null);
  const [walkDetail, setWalkDetail] = useState<WalkDetail | null>(null);
  const [loadingWalkDetail, setLoadingWalkDetail] = useState(false);
  const [isLoadingRoads, setIsLoadingRoads] = useState(false);
  const [isLoadingSections, setIsLoadingSections] = useState(false);
  const [hoursPerWalk, setHoursPerWalk] = useState(1.0);
  const [error, setError] = useState<string | null>(null);

  const { progress, markEdge, markSection, resetProgress } = useProgress(sections);

  // Boot: load default boundary → roads → sections
  useEffect(() => {
    (async () => {
      try {
        const bnd = await api.getDefaultBoundary();
        setBoundary(bnd);
        setCityName("Cupertino, CA");

        setIsLoadingRoads(true);
        await api.getRoads();
        setIsLoadingRoads(false);

        setIsLoadingSections(true);
        const secs = await api.getSections();
        setSections(secs);
        setIsLoadingSections(false);
      } catch (e) {
        setError(String(e));
        setIsLoadingRoads(false);
        setIsLoadingSections(false);
      }
    })();
  }, []);

  // When a section is selected, load its walks for the current hours-per-walk
  useEffect(() => {
    if (selectedSectionId === null) return;
    setLoadingWalks(true);
    api
      .getSectionWalks(selectedSectionId, hoursPerWalk)
      .then((res) =>
        setWalksBySection((prev) => ({ ...prev, [selectedSectionId]: res.walks }))
      )
      .catch((e) => setError(String(e)))
      .finally(() => setLoadingWalks(false));
  }, [selectedSectionId, hoursPerWalk]);

  // Fetch a walk's detailed route when one is selected
  useEffect(() => {
    if (selectedSectionId === null || selectedWalkId === null) {
      setWalkDetail(null);
      return;
    }
    setLoadingWalkDetail(true);
    api
      .getWalk(selectedSectionId, selectedWalkId, hoursPerWalk)
      .then(setWalkDetail)
      .catch(() => setWalkDetail(null))
      .finally(() => setLoadingWalkDetail(false));
  }, [selectedSectionId, selectedWalkId, hoursPerWalk]);

  const handleSelectSection = useCallback((id: number | null) => {
    setSelectedSectionId(id);
    setSelectedWalkId(null);
    setWalkDetail(null);
  }, []);

  const handleUploadBoundary = useCallback(async (file: File) => {
    try {
      setError(null);
      const bnd = await api.uploadBoundary(file);
      setBoundary(bnd);
      setCityName(file.name.replace(/\.[^.]+$/, ""));
      setSections([]);
      setWalksBySection({});
      handleSelectSection(null);

      setIsLoadingRoads(true);
      await api.getRoads(true);
      setIsLoadingRoads(false);

      setIsLoadingSections(true);
      const secs = await api.getSections(true);
      setSections(secs);
      setIsLoadingSections(false);
    } catch (e) {
      setError(String(e));
      setIsLoadingRoads(false);
      setIsLoadingSections(false);
    }
  }, [handleSelectSection]);

  const handleSetHoursPerWalk = useCallback((hours: number) => {
    setHoursPerWalk(hours);
    setWalksBySection({});
    setSelectedWalkId(null);
    setWalkDetail(null);
  }, []);

  const handleEdgeClick = useCallback(
    (edgeId: string, isHighway: boolean, currentStatus: EdgeStatus) => {
      // Cycle: unvisited → walked → driven → unvisited
      // Highways can only be unvisited or driven
      let next: EdgeStatus;
      if (isHighway) {
        next = currentStatus === "driven" ? "unvisited" : "driven";
      } else {
        if (currentStatus === "unvisited") next = "walked";
        else if (currentStatus === "walked") next = "driven";
        else next = "unvisited";
      }
      markEdge(edgeId, next);
    },
    [markEdge]
  );

  return (
    <div
      style={{
        display: "flex",
        height: "100vh",
        width: "100vw",
        overflow: "hidden",
        background: "#0f172a",
      }}
    >
      <SectionPanel
        sections={sections}
        selectedSectionId={selectedSectionId}
        selectedWalkId={selectedWalkId}
        walks={selectedSectionId !== null ? walksBySection[selectedSectionId] ?? [] : []}
        progress={progress}
        loadingWalks={loadingWalks}
        loadingWalkDetail={loadingWalkDetail}
        onSelectSection={handleSelectSection}
        onSelectWalk={setSelectedWalkId}
        onMarkSection={markSection}
        onResetProgress={resetProgress}
        onUploadBoundary={handleUploadBoundary}
        onSetHoursPerWalk={handleSetHoursPerWalk}
        hoursPerWalk={hoursPerWalk}
        cityName={cityName}
        isLoadingRoads={isLoadingRoads}
        isLoadingSections={isLoadingSections}
      />

      <div style={{ flex: 1, position: "relative" }}>
        <MapView
          boundary={boundary}
          sections={sections}
          selectedSectionId={selectedSectionId}
          walkRouteFeatures={walkDetail?.route_features ?? []}
          progress={progress}
          onSelectSection={handleSelectSection}
          onEdgeClick={handleEdgeClick}
        />

        {error && (
          <div
            style={{
              position: "absolute",
              bottom: 16,
              left: "50%",
              transform: "translateX(-50%)",
              background: "#450a0a",
              color: "#fca5a5",
              border: "1px solid #7f1d1d",
              padding: "10px 16px",
              borderRadius: 8,
              fontSize: 13,
              maxWidth: 500,
              textAlign: "center",
            }}
          >
            {error}
            <button
              style={{
                marginLeft: 12,
                background: "none",
                border: "none",
                color: "#fca5a5",
                cursor: "pointer",
              }}
              onClick={() => setError(null)}
            >
              ✕
            </button>
          </div>
        )}

        {/* Map legend */}
        {sections.length > 0 && (
          <div
            style={{
              position: "absolute",
              bottom: 16,
              right: 16,
              background: "rgba(15,23,42,0.92)",
              border: "1px solid #1e293b",
              borderRadius: 8,
              padding: "10px 14px",
              fontSize: 12,
              color: "#94a3b8",
            }}
          >
            <div style={{ fontWeight: 600, color: "#e2e8f0", marginBottom: 6 }}>Legend</div>
            {[
              { color: "#22c55e", label: "Walked" },
              { color: "#60a5fa", label: "Driven" },
              { color: "#94a3b8", label: "Highway (drive only)" },
              { color: "#facc15", label: "Planned route" },
              { color: "#f97316", label: "Route backtrack" },
            ].map(({ color, label }) => (
              <div key={label} style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
                <div
                  style={{
                    width: 24,
                    height: 3,
                    background: color,
                    borderRadius: 2,
                  }}
                />
                <span>{label}</span>
              </div>
            ))}
            <div style={{ marginTop: 8, fontSize: 11, color: "#475569" }}>
              Click a road to mark it
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
