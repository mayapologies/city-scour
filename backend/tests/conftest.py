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
    cache = ROOT / ".cache" / "graph_v5.pkl"
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


@pytest.fixture(scope="session")
def _sections_by_km(sections):
    return sorted(sections, key=lambda s: s["total_km"])


@pytest.fixture(scope="session")
def smallest_section(_sections_by_km):
    """Smallest section by total_km. Cheap walk-building target for default
    lane: keeps the per-test build_walks cost ≪ 1s while still exercising the
    full pipeline (peel → CPP/RPP → spur → walk_id)."""
    return _sections_by_km[0]


@pytest.fixture(scope="session")
def median_section(_sections_by_km):
    """Median section by total_km. Used by tests that need richer walks
    (multiple cluster edges, real spur paths) but should still finish in <2s."""
    return _sections_by_km[len(_sections_by_km) // 2]


@pytest.fixture(scope="session")
def smallest_private_section(sections):
    """Smallest is_private=True section. Used by `test_walk_marks_private` to
    exercise the private-flag propagation on a single section instead of all
    ~98 private sections (Wave 5I speed-up)."""
    private = sorted(
        (s for s in sections if s.get("is_private")),
        key=lambda s: s["total_km"],
    )
    if not private:
        pytest.skip("no private sections in graph; cannot exercise is_private flag")
    return private[0]
