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
