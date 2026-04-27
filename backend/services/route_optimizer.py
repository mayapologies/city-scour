"""
Chinese Postman route optimizer.

For each section's subgraph:
  1. Find all odd-degree nodes.
  2. Compute shortest paths between all pairs of odd nodes.
  3. Find minimum-weight perfect matching (using NetworkX blossom via scipy).
  4. Add duplicate edges for each matched pair to make graph Eulerian.
  5. Find Eulerian circuit — the optimal walk that covers every road once.
"""
import logging
import time
import networkx as nx
import osmnx as ox
import itertools
from typing import Any
from shapely.geometry import mapping, LineString
import numpy as np

log = logging.getLogger(__name__)

# Min-weight perfect matching is O(n^3). Cap the odd-vertex count of the
# Frederickson R-graph; clusters past this threshold fall back to closed-CPP.
MAX_RPP_ODD_NODES = 30
RPP_TIMEOUT_S = 10.0


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



def _local_subgraph(full_UG: nx.MultiGraph, cluster_nodes, hops: int = 2) -> nx.MultiGraph:
    """k-hop neighborhood around ``cluster_nodes`` in ``full_UG``.

    Frederickson's RPP runs all of its shortest-path operations on this
    bounded subgraph, NOT on the full road graph — that's the difference
    between Wave 4B (full-graph APSP, intractable) and Wave 5 (local-only).
    """
    nodes = set(cluster_nodes)
    for _ in range(hops):
        new = set()
        for n in nodes:
            if n in full_UG:
                new.update(full_UG.neighbors(n))
        nodes |= new
    return full_UG.subgraph(nodes).copy()


def _add_duplicate_path(R: nx.MultiGraph, local_UG: nx.MultiGraph,
                        path: list, mark_duplicate: bool, prefix: str) -> None:
    """Copy each edge of ``path`` (smallest-length parallel) into R."""
    for a, b in zip(path[:-1], path[1:]):
        ed = min(local_UG[a][b].values(),
                 key=lambda d: d.get("length", 0.0) or 0.0)
        R.add_edge(a, b,
                   length=float(ed.get("length", 0.0) or 0.0),
                   geometry=ed.get("geometry"),
                   edge_id=ed.get("edge_id", f"{prefix}-{a}-{b}"),
                   name=ed.get("name", ""),
                   highway=ed.get("highway", ""),
                   is_duplicate=mark_duplicate)
        for nid in (a, b):
            if nid in local_UG and "x" not in R.nodes[nid]:
                R.nodes[nid]["x"] = local_UG.nodes[nid].get("x", 0.0)
                R.nodes[nid]["y"] = local_UG.nodes[nid].get("y", 0.0)


def optimize_rural_postman_route(
    cluster_features: list[dict],
    anchor_node,
    full_UG: nx.MultiGraph,
    *,
    cluster_id: str = "",
    max_odd: int = MAX_RPP_ODD_NODES,
    timeout_s: float = RPP_TIMEOUT_S,
) -> tuple[list[dict] | None, float, dict]:
    """Frederickson's RPP heuristic with a hard size cap and per-cluster timeout.

    Returns ``(features, cost_m, telemetry)``. ``features`` is ``None`` when
    RPP isn't usable (size cap, timeout, infeasible) — the caller should fall
    back to closed-CPP. Returned features form an Eulerian circuit that
    starts and ends at ``anchor_node``.
    """
    start = time.monotonic()
    telemetry = {
        "cluster_id": cluster_id,
        "odd_node_count": 0,
        "used_rpp": False,
        "elapsed_ms": 0.0,
        "km_result": 0.0,
        "capped": False,
        "timed_out": False,
    }

    def _bail_timeout(stage: str):
        telemetry["timed_out"] = True
        telemetry["elapsed_ms"] = (time.monotonic() - start) * 1000.0
        log.warning("RPP cluster %s timed out after %.1fs (%s); falling back",
                    cluster_id, timeout_s, stage)
        return None, float("inf"), telemetry

    def _finalize(features: list[dict] | None, cost: float):
        telemetry["elapsed_ms"] = (time.monotonic() - start) * 1000.0
        if features is not None:
            telemetry["used_rpp"] = True
            telemetry["km_result"] = round(cost / 1000.0, 3)
        return features, cost, telemetry

    # Build R = multigraph on the required cluster edges.
    R = nx.MultiGraph()
    cluster_nodes: set = set()
    for f in cluster_features:
        p = f["properties"]
        u, v, k = p["u"], p["v"], p["key"]
        R.add_edge(u, v, key=k,
                   length=float(p.get("length", 0.0) or 0.0),
                   geometry=f["geometry"],
                   edge_id=p["edge_id"],
                   name=p.get("name", ""),
                   highway=p.get("highway", ""),
                   section_id=p.get("section_id"))
        cluster_nodes.add(u); cluster_nodes.add(v)
        for nid in (u, v):
            if nid in full_UG and "x" not in R.nodes[nid]:
                R.nodes[nid]["x"] = full_UG.nodes[nid].get("x", 0.0)
                R.nodes[nid]["y"] = full_UG.nodes[nid].get("y", 0.0)

    if R.number_of_edges() == 0:
        return _finalize(None, float("inf"))

    # Hard size cap (matching is O(n^3)) — checked against R before augmentation.
    odd_initial = sum(1 for n in cluster_nodes if R.degree(n) % 2 == 1)
    telemetry["odd_node_count"] = odd_initial
    if odd_initial > max_odd:
        telemetry["capped"] = True
        log.info("RPP cluster %s capped: %d odd nodes > %d; falling back",
                 cluster_id, odd_initial, max_odd)
        return _finalize(None, float("inf"))

    # Local subgraph for ALL shortest-path work inside RPP.
    seed_nodes = set(cluster_nodes)
    if anchor_node in full_UG:
        seed_nodes.add(anchor_node)
    local_UG = _local_subgraph(full_UG, seed_nodes, hops=2)
    if anchor_node in full_UG and anchor_node not in local_UG:
        local_UG = full_UG.subgraph(set(local_UG.nodes) | {anchor_node}).copy()

    # Connect disconnected components of R via local-subgraph shortest paths.
    components = [set(c) for c in nx.connected_components(R)]
    while len(components) > 1:
        if time.monotonic() - start > timeout_s:
            return _bail_timeout("component-connect")
        sources = [n for n in components[0] if n in local_UG]
        if not sources:
            log.info("RPP cluster %s: component head missing from local subgraph; falling back",
                     cluster_id)
            return _finalize(None, float("inf"))
        try:
            lengths, paths = nx.multi_source_dijkstra(local_UG, sources, weight="length")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return _finalize(None, float("inf"))
        best = None
        for ci, comp in enumerate(components[1:], start=1):
            for v in comp:
                if v in lengths and (best is None or lengths[v] < best[0]):
                    best = (lengths[v], ci, v)
        if best is None:
            log.info("RPP cluster %s: cannot connect components in local subgraph; falling back",
                     cluster_id)
            return _finalize(None, float("inf"))
        _, ci, v = best
        _add_duplicate_path(R, local_UG, paths[v], mark_duplicate=True, prefix="conn")
        components = [set(c) for c in nx.connected_components(R)]

    # Add a spur from the anchor to the nearest cluster node (local-subgraph SP).
    if anchor_node not in R.nodes:
        if anchor_node not in local_UG:
            log.info("RPP cluster %s: anchor not in local subgraph; falling back",
                     cluster_id)
            return _finalize(None, float("inf"))
        try:
            lengths, paths = nx.single_source_dijkstra(local_UG, anchor_node, weight="length")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return _finalize(None, float("inf"))
        cand = sorted(((lengths[v], v) for v in cluster_nodes if v in lengths),
                      key=lambda x: (x[0], x[1]))
        if not cand:
            log.info("RPP cluster %s: no reachable cluster node from anchor; falling back",
                     cluster_id)
            return _finalize(None, float("inf"))
        _, near = cand[0]
        _add_duplicate_path(R, local_UG, paths[near], mark_duplicate=False, prefix="spur")

    # Find odd-degree vertices on the augmented R.
    odd_nodes = [n for n, d in R.degree() if d % 2 == 1]

    # Min-weight perfect matching using local-subgraph shortest-path distances.
    sp_paths: dict = {}
    if odd_nodes:
        sp_costs: dict = {}
        for u in odd_nodes:
            if time.monotonic() - start > timeout_s:
                return _bail_timeout("matching-sp")
            if u not in local_UG:
                return _finalize(None, float("inf"))
            lengths, paths = nx.single_source_dijkstra(local_UG, u, weight="length")
            sp_costs[u] = lengths
            sp_paths[u] = paths

        wG = nx.Graph()
        wG.add_nodes_from(odd_nodes)
        for i in range(len(odd_nodes)):
            for j in range(i + 1, len(odd_nodes)):
                u, v = odd_nodes[i], odd_nodes[j]
                d = sp_costs[u].get(v)
                if d is None:
                    continue
                wG.add_edge(u, v, weight=d)

        if time.monotonic() - start > timeout_s:
            return _bail_timeout("matching-build")
        try:
            matching = list(nx.min_weight_matching(wG))
        except Exception:
            return _finalize(None, float("inf"))

        if len({n for pair in matching for n in pair}) != len(odd_nodes):
            log.info("RPP cluster %s: matching not perfect (%d/%d); falling back",
                     cluster_id, len(matching) * 2, len(odd_nodes))
            return _finalize(None, float("inf"))

        for u, v in matching:
            if time.monotonic() - start > timeout_s:
                return _bail_timeout("duplicate-paths")
            path = sp_paths.get(u, {}).get(v)
            if not path:
                try:
                    path = nx.shortest_path(local_UG, u, v, weight="length")
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    return _finalize(None, float("inf"))
            _add_duplicate_path(R, local_UG, path, mark_duplicate=True, prefix="match")

    # Eulerian circuit from the anchor.
    start_node = anchor_node if anchor_node in R.nodes else next(iter(cluster_nodes))
    try:
        circuit = list(nx.eulerian_circuit(R, source=start_node, keys=True))
    except (nx.NetworkXError, nx.NetworkXNotImplemented):
        return _finalize(None, float("inf"))
    if not circuit:
        return _finalize(None, float("inf"))

    features: list[dict] = []
    cost = 0.0
    for u, v, k in circuit:
        ed = R[u][v][k]
        features.append(_edge_to_feature(u, v, k, ed, R))
        cost += float(ed.get("length", 0.0) or 0.0)
    return _finalize(features, cost)
