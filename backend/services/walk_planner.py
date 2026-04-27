"""Build sub-walks for a section.

Every walk begins and ends at the section's parking anchor (snapped to the
nearest graph node). For each section, BFS-peel its edges into clusters of
≤ target_km, then for each cluster build a route:

    spur_in (anchor → cluster)  +  CPP loop on cluster  +  spur_out (back)

Spur edges may overlap with other walks' spurs; that's expected. ``walk_id``
hashes only the cluster edges so it stays stable across spur recomputation.
"""
import hashlib
import logging
import math
import os
from collections import deque

import networkx as nx
from shapely.geometry import mapping

from .route_optimizer import (
    optimize_section_route,
    optimize_open_cpp_route,
    optimize_rural_postman_route,
)

log = logging.getLogger(__name__)


WALK_SPEED_KMH = 5.0
HARD_CAP_KM = 10.0           # absolute ceiling on per-cluster unique edge length
PEEL_HEADROOM = 0.85         # backtrack headroom: actual route may exceed cluster sum
SPUR_CANDIDATES = 6          # top-K cluster nodes considered as spur entry/exit


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
                    highway=p.get("highway", ""), section_id=p.get("section_id"),
                    access=p.get("access", ""), is_private=p.get("is_private", False))
        for nid in (u, v):
            if "x" not in UG.nodes[nid] and nid in full_G.nodes:
                UG.nodes[nid]["x"] = full_G.nodes[nid].get("x", 0.0)
                UG.nodes[nid]["y"] = full_G.nodes[nid].get("y", 0.0)
    return UG


def _nearest_node(UG: nx.MultiGraph, lat: float, lng: float):
    if UG.number_of_nodes() == 0:
        return None
    return min(
        UG.nodes(),
        key=lambda n: _haversine_m(
            UG.nodes[n].get("y", lat), UG.nodes[n].get("x", lng), lat, lng,
        ),
    )


_undirected_cache: dict[int, nx.MultiGraph] = {}


def _full_undirected(full_G: nx.MultiDiGraph) -> nx.MultiGraph:
    """Undirected projection of the full road graph for spur shortest-paths."""
    cache_key = id(full_G)
    cached = _undirected_cache.get(cache_key)
    if cached is not None:
        return cached
    UG = nx.MultiGraph()
    for u, v, k, data in full_G.edges(keys=True, data=True):
        a, b = (u, v) if u <= v else (v, u)
        if UG.has_edge(a, b, key=k):
            continue
        length = float(data.get("length", 0.0) or 0.0)
        access_raw = data.get("access", "") or ""
        if isinstance(access_raw, list):
            access_raw = access_raw[0] if access_raw else ""
        access = str(access_raw).lower() if access_raw else ""
        UG.add_edge(a, b, key=k, length=length,
                    geometry=data.get("geometry"),
                    edge_id=f"{a}-{b}-{k}",
                    name=data.get("name", ""),
                    highway=data.get("highway", ""),
                    access=access,
                    is_private=(access == "private"))
    for n, d in full_G.nodes(data=True):
        if n in UG.nodes:
            UG.nodes[n]["x"] = d.get("x", 0.0)
            UG.nodes[n]["y"] = d.get("y", 0.0)
    _undirected_cache.clear()
    _undirected_cache[cache_key] = UG
    return UG


def _spur_features(full_UG: nx.MultiGraph, source, target) -> list[dict]:
    """Edge features along the shortest path source→target, oriented forward."""
    if source == target:
        return []
    try:
        path = nx.shortest_path(full_UG, source, target, weight="length")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []
    feats = []
    for a, b in zip(path[:-1], path[1:]):
        edges_dict = full_UG[a][b]
        k_min = min(edges_dict.keys(),
                    key=lambda kk: edges_dict[kk].get("length", 0.0) or 0.0)
        d = edges_dict[k_min]
        feats.append(_spur_feature(a, b, k_min, d, full_UG))
    return feats


def _spur_feature(a, b, k, data: dict, full_UG: nx.MultiGraph) -> dict:
    geom = data.get("geometry")
    ax = full_UG.nodes[a].get("x", 0.0); ay = full_UG.nodes[a].get("y", 0.0)
    bx = full_UG.nodes[b].get("x", 0.0); by = full_UG.nodes[b].get("y", 0.0)
    if geom is None:
        geom_dict = {"type": "LineString", "coordinates": [[ax, ay], [bx, by]]}
    else:
        geom_dict = geom if isinstance(geom, dict) else mapping(geom)
        coords = geom_dict.get("coordinates") or []
        if coords:
            d_first_a = (coords[0][0] - ax) ** 2 + (coords[0][1] - ay) ** 2
            d_first_b = (coords[0][0] - bx) ** 2 + (coords[0][1] - by) ** 2
            if d_first_b < d_first_a:
                geom_dict = {"type": "LineString",
                             "coordinates": list(reversed(coords))}
    canonical = _canonical_key(a, b, k)
    return {
        "type": "Feature",
        "geometry": geom_dict,
        "properties": {
            "edge_id": data.get("edge_id",
                                f"{canonical[0]}-{canonical[1]}-{k}"),
            "u": canonical[0], "v": canonical[1],
            "name": data.get("name", ""),
            "highway": data.get("highway", ""),
            "length": float(data.get("length", 0.0) or 0.0),
            "access": data.get("access", ""),
            "is_private": bool(data.get("is_private", False)),
            "is_spur": True,
            "is_duplicate": False,
        },
    }


def _reverse_feature(feat: dict) -> dict:
    coords = list(reversed(feat["geometry"].get("coordinates") or []))
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {**feat["properties"], "is_spur": True,
                       "is_duplicate": False},
    }


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
        in_cluster = {seed}

        # BFS expands only via newly-added cluster edges so the resulting
        # cluster is always a connected subgraph (open CPP requires this).
        while queue and cluster_km < cap_km:
            node = queue.popleft()
            for nb in list(UG.neighbors(node)):
                if cluster_km >= cap_km:
                    break
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
                    if nb not in in_cluster:
                        in_cluster.add(nb)
                        queue.append(nb)
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


def _walk_id_for(cluster_edge_ids: list[str]) -> str:
    """Stable hash of a cluster's edge IDs (spur edges intentionally excluded)."""
    h = hashlib.md5("|".join(sorted(cluster_edge_ids)).encode("utf-8")).hexdigest()
    return h[:12]


def _shortest_path_excluding_edges(
    full_UG: nx.MultiGraph, source, target, excluded: set[tuple],
) -> tuple[list | None, float]:
    """Return (node_path, cost_m) for the shortest source→target path in
    ``full_UG`` whose canonical edge tuples avoid ``excluded``. Returns
    ``(None, inf)`` if no such path exists. Always picks the minimum-length
    surviving parallel edge per (u, v) for cost accounting."""
    if source == target:
        return [source], 0.0
    if not excluded:
        try:
            path = nx.shortest_path(full_UG, source, target, weight="length")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None, float("inf")
    else:
        def weight(u, v, d):
            best = float("inf")
            for k, ed in d.items():
                if _canonical_key(u, v, k) in excluded:
                    continue
                L = float(ed.get("length", 0.0) or 0.0)
                if L < best:
                    best = L
            return None if best == float("inf") else best
        try:
            path = nx.shortest_path(full_UG, source, target, weight=weight)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None, float("inf")

    cost = 0.0
    for a, b in zip(path[:-1], path[1:]):
        edges_dict = full_UG[a][b]
        valid = [(k, ed) for k, ed in edges_dict.items()
                 if _canonical_key(a, b, k) not in excluded]
        if not valid:
            return None, float("inf")
        _k, ed = min(valid, key=lambda x: x[1].get("length", 0.0) or 0.0)
        cost += float(ed.get("length", 0.0) or 0.0)
    return path, cost


def _path_to_spur_features(
    full_UG: nx.MultiGraph, path: list, excluded: set[tuple],
) -> list[dict]:
    """Convert a node path to oriented spur features (forward direction)."""
    if not path or len(path) < 2:
        return []
    feats = []
    for a, b in zip(path[:-1], path[1:]):
        edges_dict = full_UG[a][b]
        valid = [(k, ed) for k, ed in edges_dict.items()
                 if _canonical_key(a, b, k) not in excluded]
        if not valid:
            return []
        k_min, d = min(valid, key=lambda x: x[1].get("length", 0.0) or 0.0)
        feats.append(_spur_feature(a, b, k_min, d, full_UG))
    return feats


def _path_canonical_edges(path: list, full_UG: nx.MultiGraph) -> set[tuple]:
    """Canonical edge tuples used by a node path (smallest-length parallel)."""
    used: set[tuple] = set()
    if not path or len(path) < 2:
        return used
    for a, b in zip(path[:-1], path[1:]):
        if not full_UG.has_edge(a, b):
            continue
        edges_dict = full_UG[a][b]
        k_min = min(edges_dict.keys(),
                    key=lambda kk: edges_dict[kk].get("length", 0.0) or 0.0)
        used.add(_canonical_key(a, b, k_min))
    return used


def _candidate_cluster_nodes(
    cluster_nodes: set, full_UG: nx.MultiGraph,
    anchor_lat: float, anchor_lng: float, k: int,
) -> list:
    """Top-k cluster nodes by haversine distance to anchor; tie-broken by
    node id for determinism."""
    def keyfn(n):
        if n in full_UG.nodes:
            y = full_UG.nodes[n].get("y", anchor_lat)
            x = full_UG.nodes[n].get("x", anchor_lng)
        else:
            y, x = anchor_lat, anchor_lng
        return (_haversine_m(y, x, anchor_lat, anchor_lng), n)
    return sorted(cluster_nodes, key=keyfn)[:k]


def _try_edge_disjoint_walk(
    cluster_features: list[dict],
    cluster_edge_ids: list[str],
    cluster_nodes: set,
    full_G: nx.MultiDiGraph,
    full_UG: nx.MultiGraph,
    anchor_node,
    anchor_lat: float,
    anchor_lng: float,
) -> dict | None:
    """Search (E1, E2) pairs for an edge-disjoint spur-in/spur-out plus an
    open Eulerian path that covers every cluster edge. Returns the best walk
    payload (excluding ``walk_id``/``section_id``/``cluster_edge_ids`` which
    the caller fills in) or ``None`` if no such configuration is found."""
    if os.environ.get("CITY_SCOUR_FORCE_OUT_AND_BACK") == "1":
        return None
    if anchor_node not in cluster_nodes and not cluster_nodes:
        return None
    candidates = _candidate_cluster_nodes(
        cluster_nodes, full_UG, anchor_lat, anchor_lng, SPUR_CANDIDATES,
    )
    if len(candidates) < 2:
        return None

    best: tuple | None = None  # (total_cost, sorted_pair, payload)
    for e1 in candidates:
        spur_in_path, spur_in_cost = _shortest_path_excluding_edges(
            full_UG, anchor_node, e1, set(),
        )
        if spur_in_path is None:
            continue
        spur_in_excl = _path_canonical_edges(spur_in_path, full_UG)
        for e2 in candidates:
            if e2 == e1:
                continue
            spur_out_path, spur_out_cost = _shortest_path_excluding_edges(
                full_UG, e2, anchor_node, spur_in_excl,
            )
            if spur_out_path is None:
                continue
            cpp_feats, cpp_cost = optimize_open_cpp_route(
                cluster_features, full_G, e1, e2,
            )
            if cpp_feats is None:
                continue
            total = spur_in_cost + cpp_cost + spur_out_cost
            sorted_pair = (min(e1, e2), max(e1, e2))
            payload = (spur_in_path, spur_out_path, cpp_feats, e1, e2)
            key = (total, sorted_pair)
            if best is None or key < best[:2]:
                best = (total, sorted_pair, payload)

    if best is None:
        return None

    spur_in_path, spur_out_path, cpp_feats, e1, e2 = best[2]
    spur_in_feats = _path_to_spur_features(full_UG, spur_in_path, set())
    spur_in_excl = _path_canonical_edges(spur_in_path, full_UG)
    spur_out_feats = _path_to_spur_features(full_UG, spur_out_path, spur_in_excl)
    return {
        "_spur_in_feats": spur_in_feats,
        "_spur_out_feats": spur_out_feats,
        "_cpp_feats": cpp_feats,
    }


def _cluster_to_walk(
    cluster_keys: list[tuple],
    section: dict,
    full_G: nx.MultiDiGraph,
    full_UG: nx.MultiGraph,
    anchor_node,
    anchor_lat: float,
    anchor_lng: float,
    *,
    use_rpp: bool = False,
    rpp_telemetry_out: list | None = None,
) -> dict | None:
    """Build a walk dict: spur_in + CPP cluster loop + spur_out."""
    feature_by_key: dict[tuple, dict] = {}
    for f in section["edges"]:
        p = f["properties"]
        k = (min(p["u"], p["v"]), max(p["u"], p["v"]), p["key"])
        feature_by_key[k] = f
    cluster_features = [feature_by_key[ek] for ek in cluster_keys if ek in feature_by_key]
    if not cluster_features:
        return None

    cluster_edge_ids = [f["properties"]["edge_id"] for f in cluster_features]

    cluster_nodes: set = set()
    for f in cluster_features:
        p = f["properties"]
        cluster_nodes.add(p["u"])
        cluster_nodes.add(p["v"])

    # Wave 5: optional Frederickson RPP path (USE_RPP=1). On success, the
    # returned features form a closed circuit anchor→…→anchor with the spur
    # baked in; spur_in/out lists stay empty.
    spur_in_feats: list[dict] = []
    spur_out_feats: list[dict] = []
    cpp_features: list[dict] = []
    rpp_features = None
    if use_rpp:
        cluster_id = _walk_id_for(cluster_edge_ids)
        rpp_features, _rpp_cost, telem = optimize_rural_postman_route(
            cluster_features, anchor_node, full_UG, cluster_id=cluster_id,
        )
        log.info(
            "rpp cluster=%s odd=%d used=%s capped=%s timed_out=%s elapsed_ms=%.1f km=%.3f",
            telem["cluster_id"], telem["odd_node_count"], telem["used_rpp"],
            telem["capped"], telem["timed_out"], telem["elapsed_ms"],
            telem["km_result"],
        )
        if rpp_telemetry_out is not None:
            rpp_telemetry_out.append(telem)
    if rpp_features is not None:
        cpp_features = rpp_features
        edge_disjoint = "_rpp_used_"  # sentinel: skip the legacy branches
    else:
        edge_disjoint = _try_edge_disjoint_walk(
            cluster_features, cluster_edge_ids, cluster_nodes,
            full_G, full_UG, anchor_node, anchor_lat, anchor_lng,
        )
    if edge_disjoint == "_rpp_used_":
        pass  # cpp_features already populated by RPP
    elif edge_disjoint is not None:
        spur_in_feats = edge_disjoint["_spur_in_feats"]
        spur_out_feats = edge_disjoint["_spur_out_feats"]
        cpp_features = edge_disjoint["_cpp_feats"]
    else:
        # Fallback: literal-reverse spur + closed CPP loop.
        if anchor_node in cluster_nodes:
            nearest_cluster_node = anchor_node
        else:
            nearest_cluster_node = min(
                cluster_nodes,
                key=lambda n: _haversine_m(
                    full_UG.nodes[n].get("y", anchor_lat) if n in full_UG.nodes else anchor_lat,
                    full_UG.nodes[n].get("x", anchor_lng) if n in full_UG.nodes else anchor_lng,
                    anchor_lat, anchor_lng,
                ),
            )
        spur_in_feats = _spur_features(full_UG, anchor_node, nearest_cluster_node)
        spur_out_feats = [_reverse_feature(f) for f in reversed(spur_in_feats)]
        sub_section = {
            "section_id": section["section_id"],
            "edges": cluster_features,
            "centroid": {"lat": anchor_lat, "lng": anchor_lng},
        }
        try:
            cpp_features = optimize_section_route(sub_section, full_G)
        except Exception:
            cpp_features = []

    route_features = list(spur_in_feats) + list(cpp_features) + list(spur_out_feats)

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

    # Anchor the visible route literally at the parking coord: prepend a
    # short connector from parking → snapped node, mirror at the end.
    if coords:
        anchor_pt = [float(anchor_lng), float(anchor_lat)]
        if coords[0] != anchor_pt:
            coords.insert(0, anchor_pt)
        if coords[-1] != anchor_pt:
            coords.append(anchor_pt)

    walk_total_km = round(route_total_m / 1000.0, 3)

    # edge_ids = cluster ∪ spur (deduped). When spur_in and spur_out share
    # an edge (literal-reverse fallback), it's traversed twice → backtrack.
    cluster_set = set(cluster_edge_ids)
    spur_in_ids: list[str] = []
    seen: set[str] = set()
    for f in spur_in_feats:
        eid = f["properties"]["edge_id"]
        if eid in seen:
            continue
        seen.add(eid)
        spur_in_ids.append(eid)
    spur_out_ids: list[str] = []
    for f in spur_out_feats:
        eid = f["properties"]["edge_id"]
        if eid in seen:
            spur_out_ids.append(eid)
            continue
        seen.add(eid)
        spur_out_ids.append(eid)
    shared_spur = set(spur_in_ids) & set(spur_out_ids)
    all_spur = list(dict.fromkeys(spur_in_ids + spur_out_ids))
    spur_only = [e for e in all_spur if e not in cluster_set]
    edge_ids = cluster_edge_ids + spur_only
    # Any spur edge walked twice (shared in/out, or also a cluster edge that
    # the CPP loop will traverse) is a backtrack.
    backtrack_edge_ids.extend(
        e for e in all_spur if e in shared_spur or e in cluster_set
    )

    is_private = any(
        bool(f["properties"].get("is_private"))
        for f in cluster_features
    ) or any(
        bool(f["properties"].get("is_private"))
        for f in spur_in_feats
    ) or any(
        bool(f["properties"].get("is_private"))
        for f in spur_out_feats
    )

    return {
        "walk_id": _walk_id_for(cluster_edge_ids),
        "section_id": section["section_id"],
        "cluster_edge_ids": cluster_edge_ids,
        "edge_ids": edge_ids,
        "total_km": walk_total_km,
        "est_hours": round(walk_total_km / WALK_SPEED_KMH, 2),
        "start": {"lat": anchor_lat, "lng": anchor_lng},
        "route": coords,
        "backtrack_edge_ids": backtrack_edge_ids,
        "route_features": route_features,
        "is_private": is_private,
    }


def build_walks(
    section: dict,
    full_G: nx.MultiDiGraph,
    hours_per_walk: float = 1.0,
    walk_speed_kmh: float = WALK_SPEED_KMH,
) -> list[dict]:
    """Return walks that cover every section edge, each anchored at parking."""
    UG = _section_subgraph(section, full_G)
    if UG.number_of_edges() == 0:
        return []
    target_km = max(0.5, hours_per_walk * walk_speed_kmh)
    anchor_lat = section.get("parking_lat", 0.0)
    anchor_lng = section.get("parking_lng", 0.0)
    full_UG = _full_undirected(full_G)
    anchor_node = _nearest_node(full_UG, anchor_lat, anchor_lng)
    if anchor_node is None:
        return []
    section_seed = _nearest_node(UG, anchor_lat, anchor_lng)
    if section_seed is None:
        return []
    clusters = _peel_clusters(UG, section_seed, target_km)
    use_rpp = os.environ.get("USE_RPP") == "1"
    rpp_telem: list[dict] = [] if use_rpp else []
    walks: list[dict] = []
    for cluster in clusters:
        w = _cluster_to_walk(
            cluster, section, full_G, full_UG, anchor_node, anchor_lat, anchor_lng,
            use_rpp=use_rpp,
            rpp_telemetry_out=rpp_telem if use_rpp else None,
        )
        if w is not None:
            walks.append(w)
    if use_rpp:
        n_total = len(rpp_telem)
        n_used = sum(1 for t in rpp_telem if t["used_rpp"])
        n_capped = sum(1 for t in rpp_telem if t["capped"])
        n_timed_out = sum(1 for t in rpp_telem if t["timed_out"])
        total_km = sum(w["total_km"] for w in walks)
        log.info(
            "rpp_section section=%s n_clusters_total=%d n_used_rpp=%d n_capped=%d n_timed_out=%d total_km=%.3f",
            section.get("section_id"), n_total, n_used, n_capped, n_timed_out, total_km,
        )
    return walks
