"""
Chinese Postman route optimizer.

For each section's subgraph:
  1. Find all odd-degree nodes.
  2. Compute shortest paths between all pairs of odd nodes.
  3. Find minimum-weight perfect matching (using NetworkX blossom via scipy).
  4. Add duplicate edges for each matched pair to make graph Eulerian.
  5. Find Eulerian circuit — the optimal walk that covers every road once.
"""
import networkx as nx
import osmnx as ox
import itertools
from typing import Any
from shapely.geometry import mapping, LineString
import numpy as np


def _build_undirected_section_graph(section: dict, full_G: nx.MultiDiGraph) -> nx.MultiGraph:
    """Extract an undirected subgraph containing only this section's edges."""
    UG = nx.MultiGraph()

    for feat in section["edges"]:
        props = feat["properties"]
        u, v, k = props["u"], props["v"], props["key"]
        length = props["length"]
        geom_dict = feat["geometry"]

        # Add edge with attributes
        UG.add_edge(u, v, key=k, length=length, geometry=geom_dict,
                    edge_id=props["edge_id"], name=props.get("name", ""),
                    highway=props.get("highway", ""), section_id=props["section_id"])

        # Ensure node positions
        if u not in UG.nodes or "x" not in UG.nodes[u]:
            if u in full_G.nodes:
                UG.nodes[u]["x"] = full_G.nodes[u].get("x", 0)
                UG.nodes[u]["y"] = full_G.nodes[u].get("y", 0)
        if v not in UG.nodes or "x" not in UG.nodes[v]:
            if v in full_G.nodes:
                UG.nodes[v]["x"] = full_G.nodes[v].get("x", 0)
                UG.nodes[v]["y"] = full_G.nodes[v].get("y", 0)

    return UG


def _odd_degree_nodes(G: nx.MultiGraph) -> list:
    return [n for n, d in G.degree() if d % 2 == 1]


def _haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371000
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlambda/2)**2
    return R * 2 * np.arcsin(np.sqrt(a))


def _min_weight_matching(G: nx.MultiGraph, odd_nodes: list) -> list[tuple]:
    """Find minimum weight perfect matching on odd-degree nodes."""
    if not odd_nodes:
        return []

    # Build distance matrix between odd nodes using straight-line distance
    n = len(odd_nodes)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            u, v = odd_nodes[i], odd_nodes[j]
            ux = G.nodes[u].get("x", 0)
            uy = G.nodes[u].get("y", 0)
            vx = G.nodes[v].get("x", 0)
            vy = G.nodes[v].get("y", 0)
            dist = _haversine(uy, ux, vy, vx)
            pairs.append((i, j, dist))

    # Use networkx minimum weight matching
    weight_graph = nx.Graph()
    for i, j, dist in pairs:
        weight_graph.add_edge(odd_nodes[i], odd_nodes[j], weight=dist)

    try:
        matching = nx.min_weight_matching(weight_graph)
    except Exception:
        # Fallback: greedy matching
        matched = set()
        matching = set()
        sorted_pairs = sorted(pairs, key=lambda x: x[2])
        for i, j, _ in sorted_pairs:
            u, v = odd_nodes[i], odd_nodes[j]
            if u not in matched and v not in matched:
                matching.add((u, v))
                matched.add(u)
                matched.add(v)

    return list(matching)


def _eulerize_graph(G: nx.MultiGraph) -> nx.MultiGraph:
    """Add minimum duplicate edges to make G Eulerian."""
    odd_nodes = _odd_degree_nodes(G)
    if not odd_nodes:
        return G

    matching = _min_weight_matching(G, odd_nodes)
    G_euler = G.copy()

    for u, v in matching:
        # Find the shortest path between u and v in G
        try:
            path = nx.shortest_path(G, u, v, weight="length")
        except nx.NetworkXNoPath:
            # Add a direct edge if no path (disconnected component edge case)
            G_euler.add_edge(u, v, length=0, geometry=None,
                             edge_id=f"dup-{u}-{v}", name="", highway="")
            continue

        # Duplicate all edges along the shortest path
        for i in range(len(path) - 1):
            a, b = path[i], path[i + 1]
            # Get the edge with minimum length
            edge_data = min(G[a][b].values(), key=lambda d: d.get("length", 0))
            G_euler.add_edge(a, b,
                             length=edge_data.get("length", 0),
                             geometry=edge_data.get("geometry"),
                             edge_id=edge_data.get("edge_id", f"dup-{a}-{b}"),
                             name=edge_data.get("name", ""),
                             highway=edge_data.get("highway", ""),
                             is_duplicate=True)

    return G_euler


def optimize_section_route(section: dict, full_G: nx.MultiDiGraph) -> list[dict]:
    """
    Compute optimized walking route for a section.
    Returns ordered list of GeoJSON LineString features.
    """
    UG = _build_undirected_section_graph(section, full_G)

    if UG.number_of_edges() == 0:
        return []

    # Handle disconnected components: process largest, ignore tiny isolated edges
    components = list(nx.connected_components(UG))
    if len(components) > 1:
        largest = max(components, key=len)
        UG = UG.subgraph(largest).copy()

    G_euler = _eulerize_graph(UG)

    # Find a good start node: prefer one closest to section centroid
    centroid_lat = section["centroid"]["lat"]
    centroid_lng = section["centroid"]["lng"]
    start_node = min(
        G_euler.nodes(),
        key=lambda n: _haversine(
            G_euler.nodes[n].get("y", centroid_lat),
            G_euler.nodes[n].get("x", centroid_lng),
            centroid_lat, centroid_lng
        )
    )

    try:
        circuit = list(nx.eulerian_circuit(G_euler, source=start_node, keys=True))
    except nx.NetworkXError:
        # Graph isn't Eulerian after eulerization attempt — return edges as-is
        circuit = []

    if not circuit:
        # Fallback: return edges in arbitrary order
        route_features = []
        for u, v, k, data in G_euler.edges(keys=True, data=True):
            route_features.append(_edge_to_feature(u, v, k, data, G_euler))
        return route_features

    route_features = []
    for u, v, k in circuit:
        edge_data = G_euler[u][v][k]
        route_features.append(_edge_to_feature(u, v, k, edge_data, G_euler))

    return route_features


def optimize_open_cpp_route(
    cluster_features: list[dict],
    full_G: nx.MultiDiGraph,
    source,
    target,
) -> tuple[list[dict] | None, float]:
    """Open Chinese-Postman: cover every cluster edge along a walk that begins
    at ``source`` and ends at ``target`` (different cluster nodes).

    Returns ``(features, cost_meters)`` on success, or ``(None, float('inf'))``
    if no Eulerian path is possible (disconnected cluster, source/target absent,
    etc.). When ``source == target`` the result is a closed Eulerian circuit.
    """
    UG = nx.MultiGraph()
    for f in cluster_features:
        p = f["properties"]
        u, v, k = p["u"], p["v"], p["key"]
        UG.add_edge(u, v, key=k,
                    length=float(p.get("length", 0.0) or 0.0),
                    geometry=f["geometry"],
                    edge_id=p["edge_id"],
                    name=p.get("name", ""),
                    highway=p.get("highway", ""),
                    section_id=p.get("section_id"))
        for nid in (u, v):
            if "x" not in UG.nodes[nid] and nid in full_G.nodes:
                UG.nodes[nid]["x"] = full_G.nodes[nid].get("x", 0.0)
                UG.nodes[nid]["y"] = full_G.nodes[nid].get("y", 0.0)

    if UG.number_of_edges() == 0:
        return None, float("inf")
    if source not in UG.nodes or target not in UG.nodes:
        return None, float("inf")
    if not nx.is_connected(UG):
        return None, float("inf")

    odd_set = {n for n, d in UG.degree() if d % 2 == 1}
    target_odd = {source, target} if source != target else set()
    to_toggle = odd_set.symmetric_difference(target_odd)
    if len(to_toggle) % 2 != 0:
        return None, float("inf")

    matching = _min_weight_matching(UG, sorted(to_toggle))
    G_euler = UG.copy()
    for u, v in matching:
        try:
            path = nx.shortest_path(UG, u, v, weight="length")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None, float("inf")
        for a, b in zip(path[:-1], path[1:]):
            ed = min(UG[a][b].values(),
                     key=lambda d: d.get("length", 0.0) or 0.0)
            G_euler.add_edge(a, b,
                             length=ed.get("length", 0.0),
                             geometry=ed.get("geometry"),
                             edge_id=ed.get("edge_id", f"dup-{a}-{b}"),
                             name=ed.get("name", ""),
                             highway=ed.get("highway", ""),
                             is_duplicate=True)

    try:
        if source != target:
            circuit = list(nx.eulerian_path(G_euler, source=source, keys=True))
        else:
            circuit = list(nx.eulerian_circuit(G_euler, source=source, keys=True))
    except (nx.NetworkXError, Exception):
        return None, float("inf")

    if not circuit:
        return None, float("inf")

    features: list[dict] = []
    cost = 0.0
    for u, v, k in circuit:
        ed = G_euler[u][v][k]
        features.append(_edge_to_feature(u, v, k, ed, G_euler))
        cost += float(ed.get("length", 0.0) or 0.0)
    return features, cost


def _edge_to_feature(u, v, k, data: dict, G: nx.MultiGraph) -> dict:
    geom = data.get("geometry")
    if geom is None:
        # Synthesize geometry from node coordinates
        ux = G.nodes[u].get("x", 0)
        uy = G.nodes[u].get("y", 0)
        vx = G.nodes[v].get("x", 0)
        vy = G.nodes[v].get("y", 0)
        geom = {"type": "LineString", "coordinates": [[ux, uy], [vx, vy]]}
    elif not isinstance(geom, dict):
        geom = mapping(geom)

    return {
        "type": "Feature",
        "geometry": geom,
        "properties": {
            "edge_id": data.get("edge_id", f"{u}-{v}-{k}"),
            "u": u,
            "v": v,
            "name": data.get("name", ""),
            "highway": data.get("highway", ""),
            "length": data.get("length", 0),
            "is_duplicate": data.get("is_duplicate", False),
            "order": None,
        },
    }
