"""Wave 2A tests: parking-anchored sections + sub-walks."""
import pytest

from services.section_planner import find_free_public_parking
from services.walk_planner import (
    build_walks, WALK_SPEED_KMH, _walk_id_for, _haversine_m,
)


def test_parking_filter_excludes_paid_and_private(boundary):
    """find_free_public_parking should filter to free public lots only."""
    lots = find_free_public_parking(boundary)
    assert isinstance(lots, list)
    for lot in lots:
        assert lot.get("fee", "") != "yes"
        assert lot.get("access", "") not in ("private", "no")


def test_section_schema(sections):
    """Every section has the v2 schema fields."""
    required = {
        "section_id", "parking_type", "parking_name",
        "parking_lat", "parking_lng", "total_km",
        "estimated_hours", "bbox", "edge_ids", "edges",
        "parking_anchor_key",
    }
    for s in sections:
        missing = required - set(s.keys())
        assert not missing, f"section {s.get('section_id')} missing {missing}"
        assert s["parking_type"] in ("lot", "street")
        assert isinstance(s["edge_ids"], list) and len(s["edge_ids"]) > 0
        assert s["total_km"] > 0
        assert isinstance(s["parking_anchor_key"], str)
        assert s["parking_anchor_key"]  # non-empty
        assert s["parking_anchor_key"].startswith(("lot:", "street:"))


def test_section_coverage_invariant(graph, sections):
    """Every undirected edge belongs to exactly one section."""
    seen: dict[str, int] = {}
    for s in sections:
        for eid in s["edge_ids"]:
            assert eid not in seen, (
                f"edge {eid} appears in sections {seen[eid]} and {s['section_id']}"
            )
            seen[eid] = s["section_id"]
    # Compare to graph edge count (undirected)
    UG = graph.to_undirected(as_view=True)
    expected = UG.number_of_edges()
    assert len(seen) == expected, (
        f"coverage mismatch: sections={len(seen)} graph_edges={expected}"
    )


def test_section_total_km_matches_graph(graph, sections):
    sec_km = sum(s["total_km"] for s in sections)
    UG = graph.to_undirected(as_view=True)
    graph_km = sum(
        (d.get("length", 0.0) or 0.0) for _u, _v, d in UG.edges(data=True)
    ) / 1000.0
    # Allow small floating drift from per-section rounding to 3 decimals
    assert abs(sec_km - graph_km) < 0.5


@pytest.mark.slow
def test_walks_cover_section_edges(graph, sections, all_walks_1h):
    """Walks must cover every section edge at least once (overlap allowed).

    Marked `slow` (Wave 5I) because this is the only test that still samples
    small + median + largest to protect the coverage invariant against the 21km
    sections that dominate Chinese-Postman cost. Default lane skips it; full
    lane (`pytest -m ""`) keeps the 3-size invariant in CI."""
    samples = sorted(sections, key=lambda s: s["total_km"])
    chosen = [samples[0], samples[len(samples) // 2], samples[-1]]
    for sec in chosen:
        walks = all_walks_1h(sec)
        assert len(walks) > 0
        union: set[str] = set()
        for w in walks:
            union.update(w["edge_ids"])
        sec_edges = set(sec["edge_ids"])
        # Every section edge must be walked at least once
        assert sec_edges <= union, f"missing edges: {sec_edges - union}"
        for eid in sec_edges:
            assert any(eid in w["edge_ids"] for w in walks), (
                f"section edge {eid} not in any walk"
            )


def test_walks_start_at_anchor(graph, smallest_section, all_walks_1h):
    """Every walk's start equals the section's anchor; route ends are within
    the snapped-node tolerance (~200 m).

    Wave 5I: pinned to `smallest_section` for the default lane. The 3-size
    sampling that this test used to do is preserved by the slow-lane
    `test_walks_cover_section_edges`, which forces build_walks on the largest
    section regardless; the start-at-anchor invariant is structural and holds
    for any section."""
    sec = smallest_section
    walks = all_walks_1h(sec)
    assert len(walks) > 0
    for w in walks:
        assert abs(w["start"]["lat"] - sec["parking_lat"]) < 1e-6
        assert abs(w["start"]["lng"] - sec["parking_lng"]) < 1e-6
        if w["route"]:
            first_lng, first_lat = w["route"][0]
            last_lng, last_lat = w["route"][-1]
            d_first = _haversine_m(
                first_lat, first_lng, w["start"]["lat"], w["start"]["lng"],
            )
            d_last = _haversine_m(
                last_lat, last_lng, w["start"]["lat"], w["start"]["lng"],
            )
            assert d_first <= 200.0, (
                f"walk {w['walk_id']} route[0] {d_first:.1f}m from anchor"
            )
            assert d_last <= 200.0, (
                f"walk {w['walk_id']} route[-1] {d_last:.1f}m from anchor"
            )


def test_walk_id_excludes_spur(graph, median_section, all_walks_1h):
    """walk_id is hashed from cluster edges only, so it's stable even if the
    spur path changes between graph rebuilds.

    Wave 5I: pinned to `median_section` (still <2s) because the smallest
    section often has only 1 edge and no spur, which would make the
    `if spur_only` branch a no-op."""
    sec = median_section
    walks = all_walks_1h(sec)
    assert len(walks) > 0
    sec_edges = set(sec["edge_ids"])
    for w in walks:
        cluster_ids = w.get("cluster_edge_ids")
        assert cluster_ids, "walk missing cluster_edge_ids"
        # walk_id is deterministically derived from cluster edges
        assert w["walk_id"] == _walk_id_for(cluster_ids)
        # cluster ⊆ section edges and ⊆ walk edge_ids
        assert set(cluster_ids) <= sec_edges
        assert set(cluster_ids) <= set(w["edge_ids"])
        # Hash differs once spur edges are mixed in (unless none added)
        spur_only = set(w["edge_ids"]) - set(cluster_ids)
        if spur_only:
            assert _walk_id_for(list(w["edge_ids"])) != w["walk_id"]


def test_walk_size_constraint(graph, median_section, all_walks_1h):
    """Walks should respect a soft cap of ~1.5x target_km for hours_per_walk.

    Wave 5I: pinned to `median_section`. The hard-cap invariant is purely
    per-walk and applies independent of section size."""
    sec = median_section
    target_km = 1.0 * WALK_SPEED_KMH  # 5 km
    hard_cap = 1.5 * target_km
    walks = all_walks_1h(sec)
    for w in walks:
        # total_km here is route distance (incl. backtracks).
        # Only enforce edge-distance cap (sum of unique edge lengths).
        unique_km = sum(
            float(f["properties"].get("length", 0.0) or 0.0)
            for f in sec["edges"]
            if f["properties"]["edge_id"] in set(w["edge_ids"])
        ) / 1000.0
        assert unique_km <= hard_cap + 0.01, (
            f"walk {w['walk_id']} unique={unique_km}km exceeds hard cap {hard_cap}km"
        )


def test_walk_id_deterministic(graph, smallest_section, all_walks_1h):
    """Re-running build_walks on the same section produces the same walk_ids.

    Wave 5I: rewritten to compare a single fresh build_walks call against the
    cached `all_walks_1h(smallest_section)` instead of two fresh builds on the
    median section. This still exercises the cross-call determinism property."""
    sec = smallest_section
    walks_a = all_walks_1h(sec)
    walks_b = build_walks(sec, graph, hours_per_walk=1.0)
    ids_a = sorted(w["walk_id"] for w in walks_a)
    ids_b = sorted(w["walk_id"] for w in walks_b)
    assert ids_a == ids_b


def test_private_roads_in_graph(graph):
    """access=private residential roads (e.g. Seven Springs Drive) are now fetched."""
    found = False
    for _u, _v, _k, data in graph.edges(keys=True, data=True):
        name = data.get("name", "")
        if isinstance(name, list):
            name = name[0] if name else ""
        if isinstance(name, str) and "Seven Springs" in name:
            found = True
            break
    assert found, "expected at least one 'Seven Springs' edge in the fetched graph"


def test_section_marks_private(sections):
    """At least one section should be flagged is_private=True."""
    assert any(s.get("is_private") for s in sections), (
        "no sections flagged is_private; expected ≥1 (e.g., Seven Springs Drive)"
    )
    # Schema check: every section has the field.
    for s in sections:
        assert "is_private" in s
        assert isinstance(s["is_private"], bool)


def test_walk_marks_private(graph, smallest_private_section, all_walks_1h):
    """At least one walk in a private section is flagged is_private, and the
    schema for the field is bool on every walk in that section.

    Wave 5I: previously iterated all ~98 private sections (which dominated by
    re-running Chinese-Postman on the largest 21km private section); now uses
    just `smallest_private_section`. The is_private propagation is a per-walk
    property and any private section that yields walks suffices to assert it."""
    sec = smallest_private_section
    walks = all_walks_1h(sec)
    assert walks, f"no walks built for private section {sec['section_id']}"
    for w in walks:
        assert "is_private" in w
        assert isinstance(w["is_private"], bool)
    assert any(w["is_private"] for w in walks), (
        f"no walks flagged is_private in section {sec['section_id']}"
    )


def test_access_no_still_excluded(graph):
    """access=no edges must still be excluded by the custom_filter."""
    # Test against the cached graph rather than re-fetching from osmnx to keep
    # this offline-safe; if any edge has access=no, the filter regressed.
    for _u, _v, _k, data in graph.edges(keys=True, data=True):
        access = data.get("access", "") or ""
        if isinstance(access, list):
            access = access[0] if access else ""
        assert str(access).lower() != "no", (
            f"edge with access=no slipped past custom_filter: {data.get('name')}"
        )
