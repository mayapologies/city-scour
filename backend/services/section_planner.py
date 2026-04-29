"""Parking-anchored section planner.

Sections are grown around eligible (free, public) OSM parking lots using
Voronoi-by-lot assignment of edge midpoints. Edges that are too far from any
lot are clustered with DBSCAN into "street parking" sections.
"""
import colorsys
import hashlib
import math
from typing import Any

import networkx as nx
import numpy as np
import osmnx as ox
from shapely.geometry import Point
from shapely.ops import unary_union
from sklearn.cluster import DBSCAN, KMeans


WALK_SPEED_KMH = 5.0
LOT_PRUNE_RADIUS_M = 300.0     # greedy-prune lots within this of a kept lot
POCKET_RADIUS_M = 600.0         # edges farther than this become "street" sections
DBSCAN_EPS_M = 250.0            # cluster pocket edges within this distance
DBSCAN_MIN_SAMPLES = 3
MAX_SECTION_KM = 30.0           # split sections larger than this via k-means
MIN_COMPONENT_EDGES = 3         # connected-component fragments smaller than this are dropped
MERGE_MAX_DIST_M = 150.0        # Wave 5J: street sections within this on G are merged
MERGE_MAX_COMBINED_KM = 5.0     # don't fold a stub into a large section
MERGE_MAX_COMBINED_EDGES = 300  # sanity cap on merged edge count


def _clean(v, default=""):
    if v is None:
        return default
    if isinstance(v, float) and math.isnan(v):
        return default
    return v


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def _color_for(section_id: int) -> str:
    h = (section_id * 0.6180339887) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.65, 0.85)
    return "#{:02x}{:02x}{:02x}".format(int(r*255), int(g*255), int(b*255))


def _normalize_str(v) -> str:
    v = _clean(v, "")
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v) if v else ""


def find_free_public_parking(boundary_polygon) -> list[dict]:
    """Return eligible parking lots from OSM (free, non-private), greedy-pruned."""
    try:
        gdf = ox.features_from_polygon(boundary_polygon, tags={"amenity": "parking"})
    except Exception:
        return []

    if gdf is None or gdf.empty:
        return []

    gdf = gdf[gdf.geometry.geom_type.isin(["Point", "Polygon", "MultiPolygon"])].copy()
    if gdf.empty:
        return []

    fee = gdf["fee"] if "fee" in gdf.columns else None
    access = gdf["access"] if "access" in gdf.columns else None

    keep_mask = np.ones(len(gdf), dtype=bool)
    if fee is not None:
        keep_mask &= (fee.astype(str).str.lower() != "yes").to_numpy()
    if access is not None:
        keep_mask &= (access.astype(str).str.lower() != "private").to_numpy()
    gdf = gdf[keep_mask]
    if gdf.empty:
        return []

    centroids = gdf.geometry.centroid
    lots = []
    for idx, c in zip(gdf.index, centroids):
        row = gdf.loc[idx]
        # idx is a tuple like ("way", 12345678) or sometimes a scalar.
        if isinstance(idx, tuple) and len(idx) >= 2:
            osm_id = f"{idx[0]}:{idx[1]}"
        else:
            osm_id = str(idx)
        lots.append({
            "lat": float(c.y),
            "lng": float(c.x),
            "name": _normalize_str(row.get("name", "")) or "Public parking",
            "osm_id": osm_id,
        })

    # Greedy prune within LOT_PRUNE_RADIUS_M
    lots.sort(key=lambda l: (l["name"] == "Public parking", l["lat"], l["lng"]))
    kept: list[dict] = []
    for lot in lots:
        too_close = any(
            _haversine_m(lot["lat"], lot["lng"], k["lat"], k["lng"]) < LOT_PRUNE_RADIUS_M
            for k in kept
        )
        if not too_close:
            kept.append(lot)
    return kept


def _edge_midpoints(G: nx.MultiDiGraph) -> tuple[list[tuple], np.ndarray]:
    """Return (edge_keys, midpoints lat/lng array) for unique undirected edges."""
    keys: list[tuple] = []
    pts: list[tuple[float, float]] = []
    seen: set[tuple] = set()
    for u, v, k, data in G.edges(keys=True, data=True):
        ek = (min(u, v), max(u, v), k)
        if ek in seen:
            continue
        seen.add(ek)
        geom = data.get("geometry")
        if geom is not None:
            mid = geom.interpolate(0.5, normalized=True)
            lat, lng = float(mid.y), float(mid.x)
        else:
            ux = G.nodes[u].get("x", 0.0); uy = G.nodes[u].get("y", 0.0)
            vx = G.nodes[v].get("x", 0.0); vy = G.nodes[v].get("y", 0.0)
            lat, lng = (uy + vy) / 2.0, (ux + vx) / 2.0
        keys.append(ek)
        pts.append((lat, lng))
    return keys, np.asarray(pts, dtype=float)


def _meters_per_deg_lat() -> float:
    return 111_320.0


def _meters_per_deg_lng(lat_deg: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat_deg))


def _split_connected_components(
    edge_keys_in_section: list[tuple],
) -> list[list[tuple]]:
    """Split a section's edges into graph-connected components (undirected).

    Uses an undirected helper graph built from the section's `(u_min, u_max, k)`
    edge keys. Components smaller than `MIN_COMPONENT_EDGES` are dropped; their
    edges fall through to the orphan-repair step in `build_sections`, which now
    re-attaches by graph-adjacency to preserve per-section connectivity.
    """
    if not edge_keys_in_section:
        return []
    UG = nx.Graph()
    for u, v, _k in edge_keys_in_section:
        UG.add_edge(u, v)
    if UG.number_of_edges() == 0:
        return []
    comps = list(nx.connected_components(UG))
    if len(comps) <= 1:
        return [edge_keys_in_section]
    node_to_comp: dict[int, int] = {}
    for ci, nodes in enumerate(comps):
        for n in nodes:
            node_to_comp[n] = ci
    grouped: dict[int, list[tuple]] = {}
    for ek in edge_keys_in_section:
        u, _v, _k = ek
        grouped.setdefault(node_to_comp[u], []).append(ek)
    # Drop tiny fragments; documented choice per Wave 5F.2 spec.
    return [eks for eks in grouped.values() if len(eks) >= MIN_COMPONENT_EDGES]


def _closest_node_in_component(
    G: nx.MultiDiGraph,
    component_nodes: set[int],
    ref_lat: float,
    ref_lng: float,
) -> tuple[float, float]:
    """Return (lat, lng) of the road-graph node in `component_nodes` closest to
    the parent anchor. Falls back to the parent coordinate if nodes lack x/y."""
    best_lat, best_lng = ref_lat, ref_lng
    best_d = float("inf")
    for n in component_nodes:
        nd = G.nodes.get(n, {})
        nx_, ny_ = nd.get("x"), nd.get("y")
        if nx_ is None or ny_ is None:
            continue
        d = _haversine_m(float(ny_), float(nx_), ref_lat, ref_lng)
        if d < best_d:
            best_d = d
            best_lat, best_lng = float(ny_), float(nx_)
    return best_lat, best_lng


def _split_oversized(
    edge_keys_in_section: list[tuple],
    edge_mids: dict[tuple, tuple[float, float]],
    edge_lengths_km: dict[tuple, float],
) -> list[list[tuple]]:
    """If a section is > MAX_SECTION_KM, split via k-means on edge midpoints."""
    total = sum(edge_lengths_km[ek] for ek in edge_keys_in_section)
    if total <= MAX_SECTION_KM:
        return [edge_keys_in_section]
    n_splits = max(2, int(math.ceil(total / MAX_SECTION_KM)))
    coords = np.array([edge_mids[ek] for ek in edge_keys_in_section], dtype=float)
    if len(coords) < n_splits:
        return [edge_keys_in_section]
    km = KMeans(n_clusters=n_splits, n_init=5, random_state=0).fit(coords)
    groups: dict[int, list[tuple]] = {}
    for ek, lbl in zip(edge_keys_in_section, km.labels_):
        groups.setdefault(int(lbl), []).append(ek)
    return list(groups.values())


def _edge_features(G: nx.MultiDiGraph, edge_keys: list[tuple], section_id: int) -> list[dict]:
    """Build GeoJSON-ish edge features for a section from a list of (u,v,k) keys."""
    from shapely.geometry import mapping as shp_mapping
    feats = []
    for ek in edge_keys:
        u, v, k = ek
        # Find an existing edge in G matching (u,v,k) or (v,u,k)
        data = None
        if G.has_edge(u, v, k):
            data = G.get_edge_data(u, v, k)
        elif G.has_edge(v, u, k):
            data = G.get_edge_data(v, u, k)
        if data is None:
            continue
        geom = data.get("geometry")
        if geom is None:
            ux = G.nodes[u].get("x", 0.0); uy = G.nodes[u].get("y", 0.0)
            vx = G.nodes[v].get("x", 0.0); vy = G.nodes[v].get("y", 0.0)
            geom_dict = {"type": "LineString", "coordinates": [[ux, uy], [vx, vy]]}
        else:
            geom_dict = shp_mapping(geom)
        highway = _normalize_str(data.get("highway", ""))
        name = _normalize_str(data.get("name", ""))
        access = _normalize_str(data.get("access", "")).lower()
        length_raw = _clean(data.get("length", 0), 0)
        feats.append({
            "type": "Feature",
            "geometry": geom_dict,
            "properties": {
                "edge_id": f"{u}-{v}-{k}",
                "u": int(u),
                "v": int(v),
                "key": int(k),
                "section_id": section_id,
                "name": name,
                "highway": highway,
                "is_highway": highway in {"primary", "primary_link", "secondary", "secondary_link"},
                "length": float(length_raw) if length_raw else 0.0,
                "access": access,
                "is_private": access == "private",
            },
        })
    return feats


def _anchor_key(parking_type: str, lot: dict | None, lat: float, lng: float) -> str:
    if parking_type == "lot" and lot is not None and lot.get("osm_id"):
        return f"lot:{lot['osm_id']}"
    # Street-parking fallback: round to 5 decimals (~1.1 m) for stability.
    return f"street:{round(lat, 5)}:{round(lng, 5)}"


def _section_dict(
    section_id: int,
    edge_keys: list[tuple],
    G: nx.MultiDiGraph,
    edge_lengths_km: dict[tuple, float],
    parking_type: str,
    parking_name: str,
    parking_lat: float,
    parking_lng: float,
    parking_anchor_key: str,
) -> dict:
    edges = _edge_features(G, edge_keys, section_id)
    total_km = round(sum(edge_lengths_km.get(ek, 0.0) for ek in edge_keys), 3)
    geom_pts = []
    for f in edges:
        for c in f["geometry"]["coordinates"]:
            geom_pts.append(c)
    if geom_pts:
        xs = [p[0] for p in geom_pts]; ys = [p[1] for p in geom_pts]
        bbox = [min(xs), min(ys), max(xs), max(ys)]
    else:
        bbox = [parking_lng, parking_lat, parking_lng, parking_lat]
    is_private = any(bool(f["properties"].get("is_private")) for f in edges)
    return {
        "section_id": section_id,
        "parking_type": parking_type,
        "parking_name": parking_name,
        "parking_lat": parking_lat,
        "parking_lng": parking_lng,
        "parking_anchor_key": parking_anchor_key,
        "total_km": total_km,
        "estimated_hours": round(total_km / WALK_SPEED_KMH, 2),
        "bbox": bbox,
        "edge_ids": [f["properties"]["edge_id"] for f in edges],
        "edges": edges,
        "color": _color_for(section_id),
        "is_private": is_private,
    }


def _section_node_set(section: dict) -> set[int]:
    """Return the set of road-graph nodes touched by `section`'s edges."""
    nodes: set[int] = set()
    for f in section["edges"]:
        p = f["properties"]
        nodes.add(int(p["u"]))
        nodes.add(int(p["v"]))
    return nodes


def _merge_adjacent_street_sections(
    sections: list[dict],
    G: nx.MultiDiGraph,
) -> list[dict]:
    """Wave 5J: fold tiny graph-adjacent street-parking sections into neighbors.

    Iterates to fixpoint. Two `street`-typed sections are merge-eligible when:
      - both have `parking_type == "street"` (lot sections never merge),
      - their node sets intersect (so the merged edge-induced subgraph stays
        graph-connected — Wave 5F.2 invariant). A shared node is the only way
        the post-merge subgraph remains connected without stealing bridging
        edges from a third section, which would break the coverage invariant.
        A multi-source Dijkstra on `G.to_undirected` weighted by `length` with
        cutoff=MERGE_MAX_DIST_M trivially reports the shared node at
        distance 0, so this implementation uses direct node-intersection,
      - combined `total_km` ≤ MERGE_MAX_COMBINED_KM (no folding stubs into a
        large blob),
      - combined edge count ≤ MERGE_MAX_COMBINED_EDGES (sanity cap).
    At each step we pick the candidate pair with the smallest combined km so
    that small stubs snowball into one another instead of growing one giant
    blob. The larger constituent contributes the merged section's anchor
    (key/lat/lng/name) so user renames (Wave 5D) survive re-runs when the
    larger half is unchanged. `section_id` is re-numbered (and edge
    `section_id` properties rewritten) only if any merge happened, sorted by
    `parking_anchor_key` for determinism.
    """
    if len(sections) < 2:
        return sections

    merged_any = False

    while True:
        street_idxs = [
            i for i, s in enumerate(sections) if s.get("parking_type") == "street"
        ]
        if len(street_idxs) < 2:
            break

        nodes_by_idx: dict[int, set[int]] = {
            i: _section_node_set(sections[i]) for i in street_idxs
        }
        node_to_sections: dict[int, set[int]] = {}
        for i in street_idxs:
            for n in nodes_by_idx[i]:
                node_to_sections.setdefault(n, set()).add(i)

        # Adjacency derived from shared nodes; equivalent to a multi-source
        # Dijkstra reaching B nodes at distance 0 from A's source set.
        adjacent: set[tuple[int, int]] = set()
        for secs in node_to_sections.values():
            if len(secs) < 2:
                continue
            ordered = sorted(secs)
            for ai in range(len(ordered)):
                for bi in range(ai + 1, len(ordered)):
                    adjacent.add((ordered[ai], ordered[bi]))

        best_pair: tuple[int, int] | None = None
        best_combined_km = float("inf")
        for i, j in adjacent:
            a, b = sections[i], sections[j]
            combined_km = float(a["total_km"]) + float(b["total_km"])
            if combined_km > MERGE_MAX_COMBINED_KM:
                continue
            combined_edges = len(a["edge_ids"]) + len(b["edge_ids"])
            if combined_edges > MERGE_MAX_COMBINED_EDGES:
                continue
            if combined_km < best_combined_km:
                best_combined_km = combined_km
                best_pair = (i, j)

        if best_pair is None:
            break

        i, j = best_pair
        a, b = sections[i], sections[j]
        big, small = (a, b) if float(a["total_km"]) >= float(b["total_km"]) else (b, a)

        merged_edges = list(a["edges"]) + list(b["edges"])
        geom_pts = []
        for f in merged_edges:
            for c in f["geometry"]["coordinates"]:
                geom_pts.append(c)
        if geom_pts:
            xs = [p[0] for p in geom_pts]
            ys = [p[1] for p in geom_pts]
            bbox = [min(xs), min(ys), max(xs), max(ys)]
        else:
            bbox = list(big.get("bbox", [
                big["parking_lng"], big["parking_lat"],
                big["parking_lng"], big["parking_lat"],
            ]))

        total_km = round(float(a["total_km"]) + float(b["total_km"]), 3)
        merged = {
            "section_id": big["section_id"],  # placeholder; renumbered below
            "parking_type": "street",
            "parking_name": big["parking_name"],
            "parking_lat": big["parking_lat"],
            "parking_lng": big["parking_lng"],
            "parking_anchor_key": big["parking_anchor_key"],
            "total_km": total_km,
            "estimated_hours": round(total_km / WALK_SPEED_KMH, 2),
            "bbox": bbox,
            "edge_ids": list(a["edge_ids"]) + list(b["edge_ids"]),
            "edges": merged_edges,
            "color": big.get("color", _color_for(big["section_id"])),
            "is_private": bool(a.get("is_private")) or bool(b.get("is_private")),
        }
        sections = [s for k, s in enumerate(sections) if k != i and k != j]
        sections.append(merged)
        merged_any = True

    if merged_any:
        sections.sort(key=lambda s: (s["parking_anchor_key"],))
        for new_id, s in enumerate(sections):
            s["section_id"] = new_id
            s["color"] = _color_for(new_id)
            for f in s["edges"]:
                f["properties"]["section_id"] = new_id
    return sections


def build_sections(G: nx.MultiDiGraph, boundary_polygon) -> list[dict]:
    """Voronoi-by-lot partition of edges; pockets clustered with DBSCAN."""
    lots = find_free_public_parking(boundary_polygon)
    edge_keys, mids = _edge_midpoints(G)

    # Per-edge length in km
    edge_lengths_km: dict[tuple, float] = {}
    edge_mids: dict[tuple, tuple[float, float]] = {}
    for ek, (lat, lng) in zip(edge_keys, mids):
        u, v, k = ek
        data = G.get_edge_data(u, v, k) or G.get_edge_data(v, u, k) or {}
        l = _clean(data.get("length", 0), 0)
        edge_lengths_km[ek] = (float(l) / 1000.0) if l else 0.0
        edge_mids[ek] = (lat, lng)

    # Voronoi-by-lot: assign each edge to nearest lot (within POCKET_RADIUS_M)
    lot_assignments: dict[int, list[tuple]] = {i: [] for i in range(len(lots))}
    pocket_keys: list[tuple] = []

    if lots:
        lot_arr = np.array([(l["lat"], l["lng"]) for l in lots], dtype=float)
        for ek, (lat, lng) in zip(edge_keys, mids):
            mlat = _meters_per_deg_lat()
            mlng = _meters_per_deg_lng(lat)
            dlat = (lot_arr[:, 0] - lat) * mlat
            dlng = (lot_arr[:, 1] - lng) * mlng
            dists = np.sqrt(dlat*dlat + dlng*dlng)
            nearest = int(np.argmin(dists))
            if dists[nearest] <= POCKET_RADIUS_M:
                lot_assignments[nearest].append(ek)
            else:
                pocket_keys.append(ek)
    else:
        pocket_keys = list(edge_keys)

    # DBSCAN on pocket edges (in meters using local equirectangular)
    pocket_clusters: list[list[tuple]] = []
    if pocket_keys:
        plats = np.array([edge_mids[ek][0] for ek in pocket_keys], dtype=float)
        plngs = np.array([edge_mids[ek][1] for ek in pocket_keys], dtype=float)
        ref_lat = float(plats.mean())
        xs = (plngs - plngs.mean()) * _meters_per_deg_lng(ref_lat)
        ys = (plats - plats.mean()) * _meters_per_deg_lat()
        coords_m = np.column_stack([xs, ys])
        if len(coords_m) >= DBSCAN_MIN_SAMPLES:
            db = DBSCAN(eps=DBSCAN_EPS_M, min_samples=DBSCAN_MIN_SAMPLES).fit(coords_m)
            labels = db.labels_
        else:
            labels = np.zeros(len(coords_m), dtype=int)
        groups: dict[int, list[tuple]] = {}
        noise: list[int] = []
        for i, lbl in enumerate(labels):
            if lbl < 0:
                noise.append(i)
            else:
                groups.setdefault(int(lbl), []).append(pocket_keys[i])
        # Attach noise to nearest cluster (or its own cluster if no clusters)
        if groups and noise:
            cluster_centers = {
                lbl: coords_m[[pocket_keys.index(ek) for ek in eks]].mean(axis=0)
                for lbl, eks in groups.items()
            }
            for i in noise:
                pt = coords_m[i]
                best = min(cluster_centers, key=lambda lbl: float(np.linalg.norm(cluster_centers[lbl] - pt)))
                groups[best].append(pocket_keys[i])
        elif noise:
            groups[0] = [pocket_keys[i] for i in noise]
        pocket_clusters = list(groups.values())

    # Assemble sections, splitting any oversized cluster
    sections: list[dict] = []
    next_id = 0

    def _emit(
        cluster_keys: list[tuple],
        parking_type: str,
        name: str,
        lat: float,
        lng: float,
        lot: dict | None,
    ):
        nonlocal next_id
        if not cluster_keys:
            return
        # First, split by graph-connected component (Wave 5F.2 Bug C). DBSCAN
        # in lat/lng space can merge geometry that is physically separated by
        # buildings, freeways, or creeks. We post-process here so every emitted
        # section's induced subgraph is a single connected component.
        components = _split_connected_components(cluster_keys)
        if not components:
            return
        parent_anchor_key = _anchor_key(parking_type, lot, lat, lng)
        if len(components) == 1:
            for sub in _split_oversized(components[0], edge_mids, edge_lengths_km):
                if not sub:
                    continue
                sections.append(_section_dict(
                    next_id, sub, G, edge_lengths_km,
                    parking_type, name, lat, lng, parent_anchor_key,
                ))
                next_id += 1
            return
        # Multiple components: the component closest to the parent anchor
        # inherits the parent (lot or street) anchor; siblings get a derived
        # street-style anchor at their own road-graph node nearest to the
        # parent anchor, keeping anchor_keys unique and stable across runs.
        comp_info: list[tuple[float, list[tuple], float, float]] = []
        for comp_eks in components:
            comp_nodes: set[int] = set()
            for u, v, _k in comp_eks:
                comp_nodes.add(u); comp_nodes.add(v)
            blat, blng = _closest_node_in_component(G, comp_nodes, lat, lng)
            d = _haversine_m(blat, blng, lat, lng)
            comp_info.append((d, comp_eks, blat, blng))
        comp_info.sort(key=lambda t: (t[0], t[2], t[3]))
        for idx, (_d, comp_eks, blat, blng) in enumerate(comp_info):
            if idx == 0:
                c_type, c_name, c_lat, c_lng, c_key = (
                    parking_type, name, lat, lng, parent_anchor_key,
                )
            else:
                c_type = "street"
                c_name = "Street parking"
                c_lat, c_lng = blat, blng
                c_key = _anchor_key("street", None, blat, blng)
            for sub in _split_oversized(comp_eks, edge_mids, edge_lengths_km):
                if not sub:
                    continue
                sections.append(_section_dict(
                    next_id, sub, G, edge_lengths_km,
                    c_type, c_name, c_lat, c_lng, c_key,
                ))
                next_id += 1

    for i, lot in enumerate(lots):
        _emit(lot_assignments.get(i, []), "lot", lot["name"], lot["lat"], lot["lng"], lot)

    for cluster in pocket_clusters:
        if not cluster:
            continue
        cl_lats = [edge_mids[ek][0] for ek in cluster]
        cl_lngs = [edge_mids[ek][1] for ek in cluster]
        clat = float(np.mean(cl_lats))
        clng = float(np.mean(cl_lngs))
        _emit(cluster, "street", "Street parking", clat, clng, None)

    # Coverage check: every edge in G should appear in exactly one section
    all_edge_keys_set = set(edge_keys)
    section_edge_keys: list[tuple] = []
    for s in sections:
        for f in s["edges"]:
            p = f["properties"]
            section_edge_keys.append((min(p["u"], p["v"]), max(p["u"], p["v"]), p["key"]))
    counts: dict[tuple, int] = {}
    for ek in section_edge_keys:
        counts[ek] = counts.get(ek, 0) + 1
    duplicates = [ek for ek, c in counts.items() if c > 1]
    orphans = list(all_edge_keys_set - set(counts.keys()))
    if duplicates:
        print(f"[section_planner] WARN: {len(duplicates)} duplicated edges across sections; first: {duplicates[:3]}")
    if orphans:
        print(f"[section_planner] WARN: {len(orphans)} orphan edges; attaching by graph adjacency")
        # Repair: attach each orphan to a section that already contains an edge
        # sharing one of the orphan's endpoints, preserving per-section graph
        # connectivity (Wave 5F.2). Orphans whose entire G-connected component
        # touches no existing section (Wave 5H: surfaces with retain_all=True)
        # are emitted as their own street-anchored section instead of being
        # force-attached by anchor distance, which would split the host.
        node_to_sections: dict[int, set[int]] = {}
        for sec_idx, s in enumerate(sections):
            for f in s["edges"]:
                p = f["properties"]
                node_to_sections.setdefault(int(p["u"]), set()).add(sec_idx)
                node_to_sections.setdefault(int(p["v"]), set()).add(sec_idx)

        # Group orphans by their connected component in the orphan-only subgraph.
        OH = nx.Graph()
        for u, v, _k in orphans:
            OH.add_edge(int(u), int(v))
        orphans_by_node: dict[int, list[tuple]] = {}
        for ek in orphans:
            u, v, _k = ek
            orphans_by_node.setdefault(int(u), []).append(ek)
            orphans_by_node.setdefault(int(v), []).append(ek)

        attachable: list[tuple] = []
        standalone_components: list[list[tuple]] = []
        for comp_nodes in nx.connected_components(OH):
            comp_eks_set: set[tuple] = set()
            for n in comp_nodes:
                for ek in orphans_by_node.get(n, []):
                    comp_eks_set.add(ek)
            comp_eks = list(comp_eks_set)
            touches_existing = any(n in node_to_sections for n in comp_nodes)
            if sections and touches_existing:
                attachable.extend(comp_eks)
            else:
                standalone_components.append(comp_eks)

        for ek in attachable:
            u, v, _k = ek
            adj = node_to_sections.get(int(u), set()) | node_to_sections.get(int(v), set())
            if not adj:
                # Sibling orphan in the same G-component will create the bridge
                # later; defer to the standalone path instead of attaching here.
                standalone_components.append([ek])
                continue
            lat, lng = edge_mids[ek]
            best = min(
                adj,
                key=lambda i: _haversine_m(
                    sections[i]["parking_lat"], sections[i]["parking_lng"], lat, lng,
                ),
            )
            sec = sections[best]
            feat = _edge_features(G, [ek], sec["section_id"])
            if feat:
                sec["edges"].extend(feat)
                sec["edge_ids"].append(feat[0]["properties"]["edge_id"])
                sec["total_km"] = round(sec["total_km"] + edge_lengths_km.get(ek, 0.0), 3)
                sec["estimated_hours"] = round(sec["total_km"] / WALK_SPEED_KMH, 2)
                if feat[0]["properties"].get("is_private"):
                    sec["is_private"] = True
                node_to_sections.setdefault(int(u), set()).add(best)
                node_to_sections.setdefault(int(v), set()).add(best)

        next_id = max((s["section_id"] for s in sections), default=-1) + 1
        for comp_eks in standalone_components:
            if not comp_eks:
                continue
            cl_lats = [edge_mids[ek][0] for ek in comp_eks]
            cl_lngs = [edge_mids[ek][1] for ek in comp_eks]
            clat = float(np.mean(cl_lats))
            clng = float(np.mean(cl_lngs))
            anchor_key = _anchor_key("street", None, clat, clng)
            for sub in _split_oversized(comp_eks, edge_mids, edge_lengths_km):
                if not sub:
                    continue
                sections.append(_section_dict(
                    next_id, sub, G, edge_lengths_km,
                    "street", "Street parking", clat, clng, anchor_key,
                ))
                next_id += 1
    # Wave 5J: fold tiny graph-adjacent street-parking sections together.
    sections = _merge_adjacent_street_sections(sections, G)
    return sections
