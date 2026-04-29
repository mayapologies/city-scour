# City Scour

Plan a multi-day walking tour of every public road in a city, one parking-anchored section at a time.

<!-- TODO: add screenshot -->

The app shows a sidebar of color-coded sections on the left and a Leaflet map on the right. Selecting a section dims the others, zooms to its bounding box, and drops a parking pin at the section's anchor (a free public lot, or a street-parking fallback). Expanding a section reveals the walks inside it: short Chinese-Postman loops, each starting and ending at the parking anchor, that together cover every road in the section. Walked edges turn green; the overall progress bar updates as you go.

## What it does

- Accepts a city boundary as GeoJSON, KML, CSV, or a zipped Esri shapefile (a Cupertino, CA boundary is bundled at the repo root as the default).
- Fetches the public road network for that boundary from OpenStreetMap via `osmnx` and caches the resulting NetworkX graph on disk.
- Partitions the graph into **sections**, each anchored on a free public parking lot (`amenity=parking`, not private, not paid). Pockets without a nearby eligible lot get a "street-parking" fallback section.
- For each section, builds a list of **walks** (~1 hour / ~5 km each, hard cap 2 hours / ~10 km), each a closed loop starting and ending at the section's parking anchor. Walks together cover every road in the section.
- Click an edge on the map to cycle its state (unvisited → walked → driven → unvisited); "Mark walked" / "Mark driven" buttons update a whole walk or section at once.
- Progress persists in `localStorage`, keyed by stable OSM edge IDs, so re-partitioning sections or recomputing walks does not lose what you have already walked.
- Export any walk as a GPX 1.1 file, or a whole section as a ZIP of per-walk GPX files, ready for offline GPS apps like OsmAnd, Open GPX Tracker, Footpath, or Avenza Maps.

## Taking walks to your phone

Each walk row in the sidebar has a `📥 GPX` button that downloads a single GPX 1.1 file (e.g. `cupertino-ca-section-170-walk-3.gpx`); each section card has a `📥 Download all walks (.zip)` button that bundles every walk in that section into one zip (`cupertino-ca-section-170-walks.zip`). Each track starts with a parking waypoint at the section's anchor so you can find your car. Import the file into any GPX-aware app on iOS or Android — on iOS, Files → "Open in" works for OsmAnd; Open GPX Tracker has its own in-app import flow. Note the GPX is a planned route, not a recording — there is no auto-tracking yet.

## Quick start

Backend (Python 3.11):

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Frontend (Node + Vite):

```bash
cd frontend
npm install
npm run dev -- --port 5173
```

Then open <http://localhost:5173>. The Cupertino boundary loads automatically. The first time you load a boundary the OSM fetch + section build takes ~2 minutes; subsequent loads come from the on-disk pickle cache in a few seconds.

The workspace already registers both as named scripts (`USE_RPP=0 ./.venv/bin/uvicorn main:app --reload --port 8000` for the backend, `npm run dev -- --port 5173` for the frontend), so you can start them from the workspace UI instead of a terminal.

## Project layout

```
backend/            FastAPI service: boundary parsing, OSM fetch, sectioning, walk planning
frontend/           React + Leaflet single-page app
City_Boundary*      Default Cupertino, CA boundary (.geojson, .kml, .csv, and a shapefile dir)
```

## Architecture

The backend is a FastAPI app (`backend/main.py`) that wraps a small pipeline under `backend/services/`: `boundary_parser` reads GeoJSON/KML/CSV/shapefile, `road_network` fetches the OSM graph via `osmnx` and stores it as a NetworkX `MultiDiGraph`, `section_planner` partitions the graph into parking-anchored sections, and `walk_planner` builds per-section walks using a Chinese Postman circuit (with a size-capped Rural Postman variant gated behind `USE_RPP`). Graphs, sections, and walks are pickled to `backend/.cache/` so repeated requests are instant. The frontend is a React + Leaflet SPA built with Vite; it talks to the backend through a `/api` dev-server proxy and stores edge / walk progress in `localStorage`.

## Tests

The default lane is fast (≈1.6 s, 23 tests):

```bash
cd backend
python3 -m pytest
```

The full lane includes the slow walk-coverage invariant checks (≈91 s, 24 tests):

```bash
cd backend
python3 -m pytest -m ""
```

Tests load the road graph from `backend/.cache/graph_v5.pkl`; if it is missing, the graph fixture in `backend/tests/conftest.py` skips those tests. Run the backend against the bundled Cupertino boundary once to populate the cache.

## Live numbers (Cupertino)

For reference, the bundled Cupertino, CA boundary currently produces 14k nodes / 20k edges / 862 km of road / 183 sections.

## Status and roadmap

Project planning, wave history, and known limitations live in the workspace spec note rather than in this repo.
