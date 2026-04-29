"""Wave 5B A/B bench — RPP (USE_RPP=1) vs baseline closed-CPP (USE_RPP=0).

Picks the first 10 sections by section_id sort order, runs build_walks twice
per section with the env flag toggled, and prints per-section / aggregate
deltas plus a decision recommendation.
"""
import logging
import os
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(message)s")

from services.boundary_parser import parse_geojson  # noqa: E402
from services.section_planner import build_sections  # noqa: E402
import services.walk_planner as walk_planner  # noqa: E402


def _run(section, graph, *, use_rpp: bool):
    """Run build_walks with USE_RPP toggled, capture timing and RPP telemetry."""
    prev = os.environ.get("USE_RPP")
    os.environ["USE_RPP"] = "1" if use_rpp else "0"

    captured: list[dict] = []
    orig = walk_planner._cluster_to_walk

    def spy(*args, **kwargs):
        telem_out = kwargs.get("rpp_telemetry_out")
        if telem_out is None and use_rpp:
            telem_out = []
            kwargs["rpp_telemetry_out"] = telem_out
        result = orig(*args, **kwargs)
        if telem_out is not None:
            captured.extend(telem_out)
        return result

    walk_planner._cluster_to_walk = spy
    try:
        t0 = time.monotonic()
        walks = walk_planner.build_walks(section, graph, hours_per_walk=1.0)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
    finally:
        walk_planner._cluster_to_walk = orig
        if prev is None:
            os.environ.pop("USE_RPP", None)
        else:
            os.environ["USE_RPP"] = prev

    total_km = round(sum(w["total_km"] for w in walks), 3)
    return {
        "n_walks": len(walks),
        "total_km": total_km,
        "elapsed_ms": round(elapsed_ms, 1),
        "telemetry": captured,
    }


def _telem_counts(telem: list[dict]) -> dict:
    return {
        "n": len(telem),
        "used": sum(1 for t in telem if t["used_rpp"]),
        "capped": sum(1 for t in telem if t["capped"]),
        "timed_out": sum(1 for t in telem if t["timed_out"]),
    }


def main():
    boundary = parse_geojson((ROOT.parent / "City_Boundary.geojson").read_bytes())
    with open(ROOT / ".cache" / "graph_v5.pkl", "rb") as f:
        graph = pickle.load(f)
    sections = build_sections(graph, boundary)
    sections_sorted = sorted(sections, key=lambda s: s["section_id"])
    pick = sections_sorted[:10]
    print(f"# Wave 5B A/B bench — {len(pick)} sections "
          f"(first 10 by section_id sort order)")
    print(f"# section_ids = {[s['section_id'] for s in pick]}")
    print()
    print("section_id,n_walks_base,n_walks_rpp,km_base,km_rpp,delta_km,"
          "delta_pct,ms_base,ms_rpp,rpp_n,rpp_used,rpp_capped,rpp_timed_out")

    sum_base_km = 0.0
    sum_rpp_km = 0.0
    sum_base_ms = 0.0
    sum_rpp_ms = 0.0
    agg = {"n": 0, "used": 0, "capped": 0, "timed_out": 0}
    n_regress = 0
    n_improve = 0

    for s in pick:
        sid = s["section_id"]
        base = _run(s, graph, use_rpp=False)
        rpp = _run(s, graph, use_rpp=True)
        delta = round(rpp["total_km"] - base["total_km"], 3)
        pct = (100.0 * delta / base["total_km"]) if base["total_km"] > 0 else 0.0
        c = _telem_counts(rpp["telemetry"])
        print(
            f"{sid},{base['n_walks']},{rpp['n_walks']},"
            f"{base['total_km']:.3f},{rpp['total_km']:.3f},"
            f"{delta:+.3f},{pct:+.2f}%,"
            f"{base['elapsed_ms']:.1f},{rpp['elapsed_ms']:.1f},"
            f"{c['n']},{c['used']},{c['capped']},{c['timed_out']}"
        )
        sum_base_km += base["total_km"]
        sum_rpp_km += rpp["total_km"]
        sum_base_ms += base["elapsed_ms"]
        sum_rpp_ms += rpp["elapsed_ms"]
        for k in agg:
            agg[k] += c[k]
        if delta < -0.001:
            n_improve += 1
        elif delta > 0.001:
            n_regress += 1

    grand_delta = round(sum_rpp_km - sum_base_km, 3)
    grand_pct = (100.0 * grand_delta / sum_base_km) if sum_base_km > 0 else 0.0
    print(
        f"TOTAL,,,{sum_base_km:.3f},{sum_rpp_km:.3f},"
        f"{grand_delta:+.3f},{grand_pct:+.2f}%,"
        f"{sum_base_ms:.1f},{sum_rpp_ms:.1f},"
        f"{agg['n']},{agg['used']},{agg['capped']},{agg['timed_out']}"
    )
    print()
    print(f"# improved sections: {n_improve}")
    print(f"# regressed sections: {n_regress}")
    print(f"# unchanged sections: {len(pick) - n_improve - n_regress}")
    pct_used = (100.0 * agg["used"] / agg["n"]) if agg["n"] else 0.0
    print(f"# RPP usage rate: {agg['used']}/{agg['n']} clusters "
          f"({pct_used:.1f}%)")


if __name__ == "__main__":
    main()
