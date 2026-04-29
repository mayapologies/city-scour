"""Fetch and process OSM road network within a city boundary."""
import math
import osmnx as ox
import networkx as nx
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon, mapping
import json


def _clean(v, default=""):
    """Replace NaN/None with a JSON-safe default."""
    if v is None:
        return default
    if isinstance(v, float) and math.isnan(v):
        return default
    return v

# Roads accessible to pedestrians; exclude motorways and their links
EXCLUDED_HIGHWAY_TYPES = {
    "motorway", "motorway_link", "trunk", "trunk_link"
}

# Roads that are technically walkable but count as "highway" (user can mark driven)
HIGHWAY_TYPES = {
    "primary", "primary_link", "secondary", "secondary_link"
}

ox.settings.use_cache = True
ox.settings.log_console = False


def fetch_road_network(polygon: Polygon | MultiPolygon) -> nx.MultiDiGraph:
    """Fetch walkable road network within boundary from OSM."""
    custom_filter = (
        '["highway"]["area"!~"yes"]'
        '["highway"!~"motorway|motorway_link|trunk|trunk_link|'
        'abandoned|construction|planned|proposed|raceway|razed"]'
        '["service"!~"private"]'
        '["access"!~"no"]'
    )
    G = ox.graph_from_polygon(
        polygon,
        network_type="all",
        custom_filter=custom_filter,
        retain_all=True,
        simplify=True,
    )
    return G


def graph_to_geojson(G: nx.MultiDiGraph) -> dict:
    """Convert road network graph to GeoJSON FeatureCollection."""
    _, edges = ox.graph_to_gdfs(G)
    edges = edges.reset_index()

    features = []
    seen = set()
    for _, row in edges.iterrows():
        u, v, k = row["u"], row["v"], row["key"]
        # Deduplicate bidirectional edges for display
        edge_key = (min(u, v), max(u, v), k)
        if edge_key in seen:
            continue
        seen.add(edge_key)

        highway = _clean(row.get("highway", ""), "")
        if isinstance(highway, list):
            highway = highway[0] if highway else ""

        is_highway = highway in HIGHWAY_TYPES
        geom = row["geometry"]

        name = _clean(row.get("name", ""), "")
        if isinstance(name, list):
            name = name[0] if name else ""

        length_raw = _clean(row.get("length", 0), 0)
        oneway_raw = _clean(row.get("oneway", False), False)

        features.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {
                "edge_id": f"{min(u,v)}-{max(u,v)}-{k}",
                "u": int(u),
                "v": int(v),
                "key": int(k),
                "name": str(name) if name else "",
                "highway": str(highway) if highway else "",
                "is_highway": is_highway,
                "length": float(length_raw) if length_raw else 0.0,
                "oneway": bool(oneway_raw),
            },
        })

    return {"type": "FeatureCollection", "features": features}


def get_graph_stats(G: nx.MultiDiGraph) -> dict:
    """Return basic stats about the road network (undirected — each street once)."""
    seen: set[tuple] = set()
    total_length_m = 0.0
    unique_count = 0
    for u, v, k, data in G.edges(keys=True, data=True):
        ek = (min(u, v), max(u, v), k)
        if ek in seen:
            continue
        seen.add(ek)
        unique_count += 1
        l = data.get("length", 0) or 0
        try:
            total_length_m += float(l)
        except (TypeError, ValueError):
            pass
    return {
        "node_count": G.number_of_nodes(),
        "edge_count": unique_count,
        "total_length_km": round(total_length_m / 1000, 2),
        "estimated_walk_hours": round(total_length_m / 1000 / 5, 1),
    }
