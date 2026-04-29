"""Wave 5F.2: every section's induced subgraph must be a single connected
component (undirected). DBSCAN can otherwise group geometry separated by
buildings/freeways/creeks into one section, producing walks that "teleport"."""
import networkx as nx


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
