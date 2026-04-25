"""Build sub-walks for a section.

For each section, BFS-peel edges into clusters of ≤ target_km (hard cap
2 * walk_speed_kmh). For each cluster run the existing Chinese Postman
optimizer to produce an ordered route. Returns a list of walk dicts.
"""
import hashlib
import math
from collections import deque

import networkx as nx

from .route_optimizer import optimize_section_route


WALK_SPEED_KMH = 5.0
HARD_CAP_KM = 10.0           # absolute ceiling on per-cluster unique edge length
PEEL_HEADROOM = 0.85         # backtrack headroom: actual route may exceed cluster sum


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def _section_subgraph(section: dict, full_G: nx.MultiDiGraph) -> nx.MultiGraph:
    UG = nx.MultiGraph()
    for feat in section["edges"]:
        p = feat["properties"]
        u, v, k = p["u"], p["v"], p["key"]
        length = p.get("length", 0.0)
        UG.add_edge(u, v, key=k, length=length, edge_id=p["edge_id"],
                    geometry=feat["geometry"], name=p.get("name", ""),
                    highway=p.get("highway", ""), section_id=p.get("section_id"))
        for nid in (u, v):
            if "x" not in UG.nodes[nid] and nid in full_G.nodes:
                UG.nodes[nid]["x"] = full_G.nodes[nid].get("x", 0.0)
                UG.nodes[nid]["y"] = full_G.nodes[nid].get("y", 0.0)
    return UG


def _anchor_node(UG: nx.MultiGraph, anchor_lat: float, anchor_lng: float):
    if UG.number_of_nodes() == 0:
        return None
    return min(
        UG.nodes(),
        key=lambda n: _haversine_m(
            UG.nodes[n].get("y", anchor_lat),
            UG.nodes[n].get("x", anchor_lng),
            anchor_lat, anchor_lng,
        ),
    )


def _peel_clusters(UG: nx.MultiGraph, start_node, target_km: float) -> list[list[tuple]]:
    """BFS-peel edges from start_node into clusters of ≤ target_km unique edges.

    Each cluster is a list of (u, v, key) tuples. Edges are assigned to exactly
    one cluster; clusters may not be connected if the peel hits a dead-end and
    restarts from the next nearest unassigned edge.
    """
    cap_km = min(target_km, HARD_CAP_KM * PEEL_HEADROOM)
    all_edges = {(u, v, k) for u, v, k in UG.edges(keys=True)}
    if not all_edges:
        return []
    assigned: set[tuple] = set()
    clusters: list[list[tuple]] = []

    while assigned != all_edges:
        # Pick a seed node: anchor first time, then a node closest to anchor
        # that still has unassigned incident edges.
        seed = None
        if not clusters:
            seed = start_node
        else:
            ax = UG.nodes[start_node].get("x", 0.0)
            ay = UG.nodes[start_node].get("y", 0.0)
            candidates = [
                n for n in UG.nodes()
                if any(_canonical_key(n, nb, k) not in assigned
                       for nb in UG.neighbors(n)
                       for k in UG[n][nb].keys())
            ]
            if not candidates:
                break
            seed = min(
                candidates,
                key=lambda n: _haversine_m(
                    UG.nodes[n].get("y", ay), UG.nodes[n].get("x", ax),
                    ay, ax,
                ),
            )

        cluster: list[tuple] = []
        cluster_set: set[tuple] = set()
        cluster_km = 0.0
        queue = deque([seed])
        visited_nodes = {seed}

        while queue:
            node = queue.popleft()
            for nb in list(UG.neighbors(node)):
                # Always allow BFS to traverse through nodes (even via already-
                # assigned edges) so we can reach distant unassigned edges.
                if nb not in visited_nodes:
                    visited_nodes.add(nb)
                    queue.append(nb)
                if cluster_km >= cap_km:
                    continue
                for k in list(UG[node][nb].keys()):
                    ek = _canonical_key(node, nb, k)
                    if ek in assigned or ek in cluster_set:
                        continue
                    edge_km = (UG[node][nb][k].get("length", 0.0) or 0.0) / 1000.0
                    if cluster_km + edge_km > HARD_CAP_KM and cluster:
                        continue
                    cluster.append(ek)
                    cluster_set.add(ek)
                    cluster_km += edge_km
                    if cluster_km >= cap_km:
                        break

        if not cluster:
            # Fallback: take a single remaining edge to make progress
            remaining = list(all_edges - assigned)
            if not remaining:
                break
            cluster = [remaining[0]]

        assigned.update(cluster)
        clusters.append(cluster)

    return clusters


def _canonical_key(u, v, k) -> tuple:
    return (min(u, v), max(u, v), k)



def _walk_id_for(edge_ids: list[str]) -> str:
    h = hashlib.md5("|".join(sorted(edge_ids)).encode("utf-8")).hexdigest()
    return h[:12]


def _cluster_to_walk(
    cluster_keys: list[tuple],
    section: dict,
    full_G: nx.MultiDiGraph,
) -> dict | None:
    """Build a walk dict from a cluster of edge keys (Chinese Postman)."""
    feature_by_key: dict[tuple, dict] = {}
    for f in section["edges"]:
        p = f["properties"]
        k = (min(p["u"], p["v"]), max(p["u"], p["v"]), p["key"])
        feature_by_key[k] = f
    cluster_features = [feature_by_key[ek] for ek in cluster_keys if ek in feature_by_key]
    if not cluster_features:
        return None

    edge_ids = [f["properties"]["edge_id"] for f in cluster_features]
    total_km = round(
        sum(float(f["properties"].get("length", 0.0) or 0.0) for f in cluster_features) / 1000.0,
        3,
    )

    sub_section = {
        "section_id": section["section_id"],
        "edges": cluster_features,
        "centroid": {
            "lat": section.get("parking_lat", 0.0),
            "lng": section.get("parking_lng", 0.0),
        },
    }
    try:
        route_features = optimize_section_route(sub_section, full_G)
    except Exception:
        route_features = []

    backtrack_edge_ids: list[str] = []
    coords: list[list[float]] = []
    route_total_m = 0.0
    for rf in route_features:
        rp = rf.get("properties", {})
        if rp.get("is_duplicate"):
            backtrack_edge_ids.append(rp.get("edge_id", ""))
        try:
            route_total_m += float(rp.get("length", 0.0) or 0.0)
        except (TypeError, ValueError):
            pass
        geom = rf.get("geometry") or {}
        if geom.get("type") == "LineString":
            for c in geom.get("coordinates", []):
                if not coords or coords[-1] != c:
                    coords.append([float(c[0]), float(c[1])])

    walk_total_km = round(route_total_m / 1000.0, 3) if route_features else total_km
    start_lat = section.get("parking_lat", 0.0)
    start_lng = section.get("parking_lng", 0.0)
    if coords:
        start_lng_c, start_lat_c = coords[0][0], coords[0][1]
        if abs(start_lat_c) <= 90 and abs(start_lng_c) <= 180:
            start_lat, start_lng = start_lat_c, start_lng_c

    return {
        "walk_id": _walk_id_for(edge_ids),
        "section_id": section["section_id"],
        "edge_ids": edge_ids,
        "total_km": walk_total_km,
        "est_hours": round(walk_total_km / WALK_SPEED_KMH, 2),
        "start": {"lat": start_lat, "lng": start_lng},
        "route": coords,
        "backtrack_edge_ids": backtrack_edge_ids,
        "route_features": route_features,
    }


def build_walks(
    section: dict,
    full_G: nx.MultiDiGraph,
    hours_per_walk: float = 1.0,
    walk_speed_kmh: float = WALK_SPEED_KMH,
) -> list[dict]:
    """Return a list of walks covering every edge in the section exactly once."""
    UG = _section_subgraph(section, full_G)
    if UG.number_of_edges() == 0:
        return []
    target_km = max(0.5, hours_per_walk * walk_speed_kmh)
    anchor = _anchor_node(
        UG, section.get("parking_lat", 0.0), section.get("parking_lng", 0.0),
    )
    if anchor is None:
        return []
    clusters = _peel_clusters(UG, anchor, target_km)
    walks: list[dict] = []
    for cluster in clusters:
        w = _cluster_to_walk(cluster, section, full_G)
        if w is not None:
            walks.append(w)
    return walks
