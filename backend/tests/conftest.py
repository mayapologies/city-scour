import sys
import pickle
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore")

from services.boundary_parser import parse_geojson  # noqa: E402
from services.section_planner import build_sections  # noqa: E402


@pytest.fixture(scope="session")
def boundary():
    return parse_geojson((ROOT.parent / "City_Boundary.geojson").read_bytes())


@pytest.fixture(scope="session")
def graph():
    cache = ROOT / ".cache" / "graph_v4.pkl"
    if not cache.exists():
        pytest.skip("graph cache not present; run backend once to populate")
    with open(cache, "rb") as f:
        return pickle.load(f)


@pytest.fixture(scope="session")
def sections(graph, boundary):
    return build_sections(graph, boundary)


@pytest.fixture(scope="session")
def all_walks_1h(graph):
    """Lazy session-scoped cache of build_walks(sec, graph, hours_per_walk=1.0).

    Returns a callable `get(section)` that computes walks on first access and
    memoizes by section_id, so repeated requests across wave2 tests share a
    single Chinese-Postman pass per section."""
    from services.walk_planner import build_walks
    cache: dict[str, list] = {}

    def get(sec):
        sid = sec["section_id"]
        if sid not in cache:
            cache[sid] = build_walks(sec, graph, hours_per_walk=1.0)
        return cache[sid]

    return get
