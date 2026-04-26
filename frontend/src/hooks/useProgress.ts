import { useState, useCallback } from "react";
import type { EdgeStatus, Progress, Section } from "../types";
import {
  loadProgress,
  saveProgress,
  setEdgeStatus,
  recomputeSections,
} from "../utils/storage";

export function useProgress(sections: Section[]) {
  const [progress, setProgress] = useState<Progress>(() => loadProgress());

  const markEdge = useCallback(
    (edgeId: string, status: EdgeStatus) => {
      setProgress((prev) => {
        const updated = setEdgeStatus(prev, edgeId, status);
        const withSections = recomputeSections(sections, updated);
        saveProgress(withSections);
        return withSections;
      });
    },
    [sections]
  );

  const markSection = useCallback(
    (sectionId: number, status: EdgeStatus) => {
      setProgress((prev) => {
        const section = sections.find((s) => s.section_id === sectionId);
        if (!section) return prev;

        let updated = { ...prev, edges: { ...prev.edges } };
        for (const feat of section.edges) {
          const edgeId = (feat.properties as { edge_id: string }).edge_id;
          updated = setEdgeStatus(updated, edgeId, status);
        }
        const withSections = recomputeSections(sections, updated);
        saveProgress(withSections);
        return withSections;
      });
    },
    [sections]
  );

  const markEdges = useCallback(
    (edgeIds: string[], status: EdgeStatus) => {
      setProgress((prev) => {
        let updated = { ...prev, edges: { ...prev.edges } };
        for (const edgeId of edgeIds) {
          updated = setEdgeStatus(updated, edgeId, status);
        }
        const withSections = recomputeSections(sections, updated);
        saveProgress(withSections);
        return withSections;
      });
    },
    [sections]
  );

  const resetProgress = useCallback(() => {
    const empty: Progress = { edges: {}, sections: {} };
    saveProgress(empty);
    setProgress(empty);
  }, []);

  return { progress, markEdge, markEdges, markSection, resetProgress };
}
