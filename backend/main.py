"""City Scour — FastAPI backend."""
import hashlib
import json
import os
import pickle
import re
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from shapely.geometry import shape

from services.boundary_parser import (
    parse_geojson, parse_kml, parse_shapefile, parse_shapefile_dir,
    polygon_to_geojson,
)
from services.road_network import fetch_road_network, graph_to_geojson, get_graph_stats
from services.section_planner import build_sections
from services.walk_planner import build_walks

app = FastAPI(title="City Scour API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

# In-memory state for the current session (single-city for now)
_state: dict = {}


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.pkl"


def _save_cache(key: str, data):
    with open(_cache_path(key), "wb") as f:
        pickle.dump(data, f)


def _load_cache(key: str):
    p = _cache_path(key)
    if p.exists():
        with open(p, "rb") as f:
            return pickle.load(f)
    return None


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/default-boundary")
def get_default_boundary():
    """Return the bundled Cupertino boundary as GeoJSON."""
    boundary_file = Path(__file__).parent.parent / "City_Boundary.geojson"
    if not boundary_file.exists():
        raise HTTPException(404, "Default boundary file not found")
    with open(boundary_file, "rb") as f:
        data = f.read()
    poly = parse_geojson(data)
    _state["boundary"] = poly
    return polygon_to_geojson(poly)


@app.post("/api/boundary")
async def upload_boundary(file: UploadFile = File(...)):
    """Upload a boundary file (GeoJSON, KML, Shapefile zip, or CSV)."""
    data = await file.read()
    name = file.filename.lower()

    try:
        if name.endswith(".geojson") or name.endswith(".json"):
            poly = parse_geojson(data)
        elif name.endswith(".kml"):
            poly = parse_kml(data)
        elif name.endswith(".zip"):
            poly = parse_shapefile(data)
        elif name.endswith(".csv"):
            from services.boundary_parser import parse_csv_boundary
            poly = parse_csv_boundary(data)
        else:
            raise HTTPException(400, f"Unsupported file type: {file.filename}")
    except Exception as e:
        raise HTTPException(422, f"Failed to parse boundary: {e}")

    _state["boundary"] = poly
    # Clear cached road/sections when boundary changes
    for key in ["road_geojson", "sections", "graph"]:
        _state.pop(key, None)

    return polygon_to_geojson(poly)


@app.get("/api/roads")
def get_roads(force_refresh: bool = False):
    """
    Fetch or return cached road network for the current boundary.
    Returns GeoJSON FeatureCollection of road edges.
    """
    poly = _state.get("boundary")
    if poly is None:
        raise HTTPException(400, "No boundary loaded. Call /api/default-boundary first.")

    if not force_refresh and "road_geojson" in _state:
        return _state["road_geojson"]

    cached_graph = _load_cache("graph_v5")
    if not force_refresh and cached_graph is not None:
        G = cached_graph
    else:
        try:
            G = fetch_road_network(poly)
        except Exception as e:
            raise HTTPException(500, f"Failed to fetch road network: {e}")
        _save_cache("graph_v5", G)

    _state["graph"] = G
    geojson = graph_to_geojson(G)
    stats = get_graph_stats(G)
    geojson["stats"] = stats
    _state["road_geojson"] = geojson
    return geojson


@app.get("/api/sections")
def get_sections(force_refresh: bool = False):
    """
    Build or return cached parking-anchored section plan.
    """
    poly = _state.get("boundary")
    G = _state.get("graph")

    if poly is None:
        raise HTTPException(400, "No boundary loaded.")
    if G is None:
        raise HTTPException(400, "Road network not loaded. Call /api/roads first.")

    cache_key = "sections_v7"
    if not force_refresh and "sections" in _state:
        return _state["sections"]

    cached = _load_cache(cache_key)
    if not force_refresh and cached is not None:
        _state["sections"] = cached
        _prune_orphan_walks_v5(cached)
        return cached

    try:
        sections = build_sections(G, poly)
    except Exception as e:
        raise HTTPException(500, f"Failed to build sections: {e}")

    _save_cache(cache_key, sections)
    _state["sections"] = sections
    # Invalidate any in-memory walks cache when sections change
    _state["walks_cache"] = {}
    _prune_orphan_walks_v5(sections)
    return sections


def _get_section_or_404(section_id: int) -> dict:
    sections = _state.get("sections")
    if sections is None:
        raise HTTPException(400, "Sections not built yet. Call /api/sections first.")
    matching = [s for s in sections if s["section_id"] == section_id]
    if not matching:
        raise HTTPException(404, f"Section {section_id} not found.")
    return matching[0]


def _section_edges_hash(section: dict) -> str:
    """Stable 8-hex-char digest of a section's edge set.

    Used so the walks cache auto-invalidates whenever a re-partition changes
    which edges belong to a section_id."""
    edge_ids = section.get("edge_ids", []) or []
    joined = ",".join(sorted(edge_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:8]


def _walks_for(section: dict, hours_per_walk: float) -> list[dict]:
    G = _state.get("graph")
    if G is None:
        raise HTTPException(400, "Road graph not available.")
    cache = _state.setdefault("walks_cache", {})
    edges_hash = _section_edges_hash(section)
    hpw = round(float(hours_per_walk), 2)
    key = (section["section_id"], edges_hash, hpw)
    if key in cache:
        return cache[key]
    file_key = f"walks_v5_{key[0]}_{edges_hash}_{hpw}"
    cached = _load_cache(file_key)
    if cached is not None:
        cache[key] = cached
        return cached
    try:
        walks = build_walks(section, G, hours_per_walk=hours_per_walk)
    except Exception as e:
        raise HTTPException(500, f"Walk planning failed: {e}")
    cache[key] = walks
    _save_cache(file_key, walks)
    return walks


_WALKS_V5_RE = re.compile(r"^walks_v5_(\d+)_([0-9a-f]{8})_.+\.pkl$")


def _prune_orphan_walks_v5(sections: list[dict]) -> int:
    """Delete walks_v5_*.pkl files whose (section_id, edges_hash) tuple no
    longer matches any current section. Returns the count removed."""
    valid: set[tuple[int, str]] = {
        (s["section_id"], _section_edges_hash(s)) for s in sections
    }
    removed = 0
    for p in CACHE_DIR.glob("walks_v5_*.pkl"):
        m = _WALKS_V5_RE.match(p.name)
        if not m:
            continue
        sid = int(m.group(1))
        h = m.group(2)
        if (sid, h) not in valid:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


@app.get("/api/sections/{section_id}/walks")
def get_section_walks(
    section_id: int,
    hours_per_walk: float = Query(default=1.0, ge=0.25, le=4.0),
):
    """Return all sub-walks for a section that together cover all its edges."""
    section = _get_section_or_404(section_id)
    walks = _walks_for(section, hours_per_walk)
    return {
        "section_id": section_id,
        "hours_per_walk": hours_per_walk,
        "walks": [_walk_summary(w) for w in walks],
    }


@app.get("/api/sections/{section_id}/walks/{walk_id}")
def get_walk(
    section_id: int,
    walk_id: str,
    hours_per_walk: float = Query(default=1.0, ge=0.25, le=4.0),
):
    """Return a single walk's full route + features."""
    section = _get_section_or_404(section_id)
    walks = _walks_for(section, hours_per_walk)
    for w in walks:
        if w["walk_id"] == walk_id:
            return {
                **_walk_summary(w),
                "route_features": w.get("route_features", []),
            }
    raise HTTPException(404, f"Walk {walk_id} not found in section {section_id}.")


def _walk_summary(w: dict) -> dict:
    return {
        "walk_id": w["walk_id"],
        "section_id": w["section_id"],
        "edge_ids": w["edge_ids"],
        "total_km": w["total_km"],
        "est_hours": w["est_hours"],
        "start": w["start"],
        "route": w["route"],
        "backtrack_edge_ids": w["backtrack_edge_ids"],
    }


@app.get("/api/stats")
def get_stats():
    """Return overview stats for the loaded city."""
    sections = _state.get("sections", [])
    G = _state.get("graph")
    if G is None:
        return {"loaded": False}
    stats = get_graph_stats(G)
    stats["section_count"] = len(sections)
    stats["loaded"] = True
    return stats
