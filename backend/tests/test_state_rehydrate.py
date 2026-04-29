"""Wave 6C: backend rehydrates `_state` from on-disk caches after a restart
so /api/sections/{id}/walks calls don't 400 before the user re-touches
/api/sections."""
import pickle

import networkx as nx
import pytest
from fastapi import HTTPException
from shapely.geometry import Polygon

import main


def _fake_polygon() -> Polygon:
    return Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])


def _fake_graph() -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()
    G.add_node(1, x=0.0, y=0.0)
    G.add_node(2, x=1.0, y=0.0)
    G.add_edge(1, 2, key=0, length=10.0, edge_id="1-2-0")
    return G


def _fake_sections() -> list[dict]:
    return [
        {
            "section_id": 0,
            "edge_ids": ["1-2-0"],
            "anchor": {"lon": 0.0, "lat": 0.0},
        }
    ]


def _write_caches(cache_dir, *, boundary=True, graph=True, sections=True):
    if boundary:
        with open(cache_dir / "boundary_v1.pkl", "wb") as f:
            pickle.dump(_fake_polygon(), f)
    if graph:
        with open(cache_dir / "graph_v5.pkl", "wb") as f:
            pickle.dump(_fake_graph(), f)
    if sections:
        with open(cache_dir / "sections_v8.pkl", "wb") as f:
            pickle.dump(_fake_sections(), f)


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(main, "_state", {})
    return tmp_path


def test_rehydrate_when_all_three_caches_present(isolated_state, monkeypatch):
    _write_caches(isolated_state)

    def _boom(*args, **kwargs):
        raise AssertionError("rehydrate must not call build_sections")

    def _boom_fetch(*args, **kwargs):
        raise AssertionError("rehydrate must not call fetch_road_network")

    monkeypatch.setattr(main, "build_sections", _boom)
    monkeypatch.setattr(main, "fetch_road_network", _boom_fetch)

    section = main._get_section_or_404(0)
    assert section["section_id"] == 0
    assert main._state.get("boundary") is not None
    assert main._state.get("graph") is not None
    assert main._state.get("sections") is not None
    assert main._state.get("walks_cache") == {}


def test_rehydrate_skipped_when_boundary_missing(isolated_state):
    _write_caches(isolated_state, boundary=False)

    with pytest.raises(HTTPException) as exc:
        main._get_section_or_404(0)
    assert exc.value.status_code == 400
    # State must remain empty since rehydrate refused to partial-load.
    assert "boundary" not in main._state
    assert "graph" not in main._state
    assert "sections" not in main._state
