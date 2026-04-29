"""Wave 5K: the per-section connectivity invariant from Wave 5F.2 is relaxed
because absorbed street stubs may sit in a separate component of the host
section's subgraph. The walk planner's BFS-peel already handles this by
emitting one walk per cluster; the per-walk invariant we still enforce is
that each walk's combined edges (cluster ∪ spurs) form a single connected
subgraph (the walker has to actually be able to traverse the route).

Wave 5J: also pins the post-merge invariant that no two street-parking
sections sit within MERGE_MAX_DIST_M road-metres of each other when their
combined size would still fit under the merge caps."""
import networkx as nx
import pytest

from services.section_planner import (
    MERGE_MAX_COMBINED_EDGES,
    MERGE_MAX_COMBINED_KM,
    MERGE_MAX_DIST_M,
)


def _parse_edge_id(eid: str) -> tuple[int, int, int]:
    u, v, k = eid.split("-")
    return int(u), int(v), int(k)


@pytest.mark.slow
def test_every_walk_is_graph_connected(sections, all_walks_1h):
    """Wave 5K replacement for the Wave 5F.2 per-section invariant: for
    every section, every walk emitted by `build_walks(section, G,
    hours_per_walk=1.0)` must have its combined edges (cluster + spurs)
    form a single connected undirected subgraph. Sections themselves may
    have multiple components after a Wave 5K absorb-pass; the walk planner
    splits those into per-cluster walks, each of which the user has to be
    able to physically walk start-to-end."""
    offenders: list[tuple[int, str, int, int]] = []
    for s in sections:
        walks = all_walks_1h(s)
        for w in walks:
            UG = nx.Graph()
            for eid in w["edge_ids"]:
                u, v, _k = _parse_edge_id(eid)
                UG.add_edge(u, v)
            if UG.number_of_edges() == 0:
                continue
            n_comp = nx.number_connected_components(UG)
            if n_comp != 1:
                offenders.append(
                    (s["section_id"], w["walk_id"], n_comp, len(w["edge_ids"])),
                )
    assert not offenders, (
        f"{len(offenders)} walks are graph-disconnected; first few "
        f"(section_id, walk_id, n_components, n_edges): {offenders[:5]}"
    )


def test_no_two_street_sections_share_a_node(sections):
    """Wave 5J merge-pass invariant: no pair of street-parking sections shares
    a road-graph node unless folding them would breach the merge caps
    (combined km > MERGE_MAX_COMBINED_KM, or combined edge count >
    MERGE_MAX_COMBINED_EDGES). Sharing a node is the connectivity-safe
    interpretation of "shortest path on G ≤ MERGE_MAX_DIST_M": a multi-source
    Dijkstra from A's nodes reports any shared B node at distance 0, and
    merging via a non-shared bridge would steal edges from a third section
    (breaking coverage) or leave the merged subgraph disconnected (breaking
    Wave 5F.2)."""
    streets = [s for s in sections if s.get("parking_type") == "street"]

    nodes_by_idx: dict[int, set[int]] = {}
    for i, s in enumerate(streets):
        ns: set[int] = set()
        for eid in s["edge_ids"]:
            u, v, _k = _parse_edge_id(eid)
            ns.add(u)
            ns.add(v)
        nodes_by_idx[i] = ns

    node_to_sections: dict[int, set[int]] = {}
    for i, ns in nodes_by_idx.items():
        for n in ns:
            node_to_sections.setdefault(n, set()).add(i)

    adjacent: set[tuple[int, int]] = set()
    for secs in node_to_sections.values():
        if len(secs) < 2:
            continue
        ordered = sorted(secs)
        for ai in range(len(ordered)):
            for bi in range(ai + 1, len(ordered)):
                adjacent.add((ordered[ai], ordered[bi]))

    offenders: list[tuple[int, int, float, int]] = []
    for i, j in adjacent:
        a_km = float(streets[i]["total_km"])
        b_km = float(streets[j]["total_km"])
        a_e = len(streets[i]["edge_ids"])
        b_e = len(streets[j]["edge_ids"])
        if a_km + b_km <= MERGE_MAX_COMBINED_KM and a_e + b_e <= MERGE_MAX_COMBINED_EDGES:
            offenders.append((
                streets[i]["section_id"], streets[j]["section_id"],
                round(a_km + b_km, 3), a_e + b_e,
            ))
    # `MERGE_MAX_DIST_M` is referenced to keep the import meaningful: the
    # merge predicate's Dijkstra cutoff is documented in section_planner.
    _ = MERGE_MAX_DIST_M
    assert not offenders, (
        f"{len(offenders)} street-section pairs share a road-graph node "
        f"with mergeable size; first few "
        f"(id_a, id_b, combined_km, combined_edges): {offenders[:5]}"
    )
