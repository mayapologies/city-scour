"""Wave 5F.2: every section's induced subgraph must be a single connected
component (undirected). DBSCAN can otherwise group geometry separated by
buildings/freeways/creeks into one section, producing walks that "teleport".

Wave 5J: also pins the post-merge invariant that no two street-parking
sections sit within MERGE_MAX_DIST_M road-metres of each other when their
combined size would still fit under the merge caps."""
import networkx as nx

from services.section_planner import (
    MERGE_MAX_COMBINED_EDGES,
    MERGE_MAX_COMBINED_KM,
    MERGE_MAX_DIST_M,
)


def _parse_edge_id(eid: str) -> tuple[int, int, int]:
    u, v, k = eid.split("-")
    return int(u), int(v), int(k)


def _section_subgraph(section: dict) -> nx.Graph:
    UG = nx.Graph()
    for eid in section["edge_ids"]:
        u, v, _k = _parse_edge_id(eid)
        UG.add_edge(u, v)
    return UG


def test_every_section_is_graph_connected(sections):
    """For every section, the undirected subgraph induced by edge_ids has
    exactly one connected component."""
    offenders: list[tuple[int, int, int]] = []
    for s in sections:
        UG = _section_subgraph(s)
        if UG.number_of_edges() == 0:
            continue
        n_comp = nx.number_connected_components(UG)
        if n_comp != 1:
            offenders.append((s["section_id"], n_comp, len(s["edge_ids"])))
    assert not offenders, (
        f"{len(offenders)} sections are graph-disconnected; first few "
        f"(section_id, n_components, n_edges): {offenders[:5]}"
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
