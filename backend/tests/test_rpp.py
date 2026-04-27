"""Wave 5A unit tests for Frederickson's RPP heuristic.

Each test builds a tiny synthetic full_UG / cluster pair so the algorithm can
be exercised without the real road graph. The tests cover the size cap,
per-cluster timeout, anchor-circuit invariant, and required-edge coverage.
"""
import networkx as nx
import pytest

from services.route_optimizer import (
    MAX_RPP_ODD_NODES,
    optimize_rural_postman_route,
    _local_subgraph,
)


def _mk_feature(u: int, v: int, k: int, length: float, edge_id: str) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString",
                     "coordinates": [[float(u), 0.0], [float(v), 0.0]]},
        "properties": {
            "u": u, "v": v, "key": k, "length": length,
            "edge_id": edge_id, "name": "", "highway": "",
            "section_id": "S",
        },
    }


def _add_edge(G: nx.MultiGraph, u: int, v: int, k: int, length: float) -> None:
    G.add_edge(u, v, key=k, length=length, edge_id=f"e{u}-{v}-{k}",
               geometry={"type": "LineString",
                         "coordinates": [[float(u), 0.0], [float(v), 0.0]]},
               name="", highway="")
    for n in (u, v):
        G.nodes[n]["x"] = float(n)
        G.nodes[n]["y"] = 0.0


def _path_graph(n_nodes: int) -> nx.MultiGraph:
    """Linear chain 0-1-2-...-n_nodes-1, all edges length=100."""
    G = nx.MultiGraph()
    for i in range(n_nodes - 1):
        _add_edge(G, i, i + 1, 0, 100.0)
    return G


def test_rpp_walk_starts_and_ends_at_anchor():
    """The Eulerian circuit must originate and terminate at the anchor."""
    G = _path_graph(5)  # 0-1-2-3-4
    cluster = [_mk_feature(1, 2, 0, 100.0, "e1-2-0"),
               _mk_feature(2, 3, 0, 100.0, "e2-3-0")]
    feats, cost, telem = optimize_rural_postman_route(
        cluster, anchor_node=0, full_UG=G, cluster_id="walk-anchor",
    )
    assert feats is not None
    assert telem["used_rpp"] is True
    assert feats[0]["properties"]["u"] == 0, "first edge must leave anchor"
    assert feats[-1]["properties"]["v"] == 0, "last edge must return to anchor"
    assert cost > 0


def test_rpp_covers_every_required_edge():
    """Every cluster edge_id must appear in the returned features."""
    G = _path_graph(6)
    cluster = [_mk_feature(1, 2, 0, 100.0, "e1-2-0"),
               _mk_feature(2, 3, 0, 100.0, "e2-3-0"),
               _mk_feature(3, 4, 0, 100.0, "e3-4-0")]
    required_ids = {f["properties"]["edge_id"] for f in cluster}
    feats, _cost, telem = optimize_rural_postman_route(
        cluster, anchor_node=0, full_UG=G, cluster_id="walk-cover",
    )
    assert feats is not None
    seen = {f["properties"]["edge_id"] for f in feats}
    missing = required_ids - seen
    assert not missing, f"required edges missing from RPP output: {missing}"
    assert telem["used_rpp"] is True


def test_rpp_disconnected_cluster_uses_local_paths():
    """Two disjoint required edges must be connected via the local subgraph;
    the run must not depend on full-graph APSP."""
    # full_UG: 0-1-2-3-4-5  with required edges {1-2} and {4-5} separated by
    # node 3. Plus a far-away unconnected node cluster to ensure that any
    # full-graph shortest-path scan would visit useless nodes.
    G = _path_graph(6)
    for n in range(100, 200):
        G.add_node(n, x=float(n), y=10.0)
    cluster = [_mk_feature(1, 2, 0, 100.0, "e1-2-0"),
               _mk_feature(4, 5, 0, 100.0, "e4-5-0")]
    feats, _cost, telem = optimize_rural_postman_route(
        cluster, anchor_node=0, full_UG=G, cluster_id="walk-disjoint",
    )
    assert feats is not None
    seen = {f["properties"]["edge_id"] for f in feats}
    assert "e1-2-0" in seen and "e4-5-0" in seen
    assert telem["used_rpp"] is True
    # Local subgraph must exclude the unrelated 100..199 nodes.
    local = _local_subgraph(G, {1, 2, 4, 5, 0}, hops=2)
    assert all(n < 100 for n in local.nodes)


def test_rpp_falls_back_above_size_cap():
    """When R has > MAX_RPP_ODD_NODES odd vertices, RPP must bail out."""
    # Build N disjoint required edges so each endpoint is odd in R.
    N = MAX_RPP_ODD_NODES + 4  # 34 disjoint edges → 68 odd nodes
    G = nx.MultiGraph()
    cluster = []
    for i in range(N):
        u = 2 * i
        v = 2 * i + 1
        _add_edge(G, u, v, 0, 50.0)
        cluster.append(_mk_feature(u, v, 0, 50.0, f"e{u}-{v}-0"))
    # Anchor isolated; doesn't matter — cap is checked first.
    G.add_node(-1, x=-1.0, y=0.0)
    feats, _cost, telem = optimize_rural_postman_route(
        cluster, anchor_node=-1, full_UG=G, cluster_id="walk-cap",
    )
    assert feats is None
    assert telem["capped"] is True
    assert telem["used_rpp"] is False
    assert telem["odd_node_count"] == 2 * N


def test_rpp_falls_back_on_timeout(monkeypatch):
    """A near-zero timeout must trigger a graceful fall-back, not a crash."""
    G = _path_graph(8)
    cluster = [_mk_feature(1, 2, 0, 100.0, "e1-2-0"),
               _mk_feature(2, 3, 0, 100.0, "e2-3-0"),
               _mk_feature(5, 6, 0, 100.0, "e5-6-0")]
    # Deterministic clock: each subsequent monotonic() call is +1s, so the
    # first internal check past `start` immediately exceeds timeout_s=0.5.
    counter = {"n": 0}
    real_time = __import__("time")
    base = real_time.monotonic()

    def fake_monotonic():
        counter["n"] += 1
        return base + counter["n"]

    monkeypatch.setattr("services.route_optimizer.time.monotonic", fake_monotonic)
    feats, _cost, telem = optimize_rural_postman_route(
        cluster, anchor_node=0, full_UG=G, cluster_id="walk-timeout",
        timeout_s=0.5,
    )
    assert feats is None
    assert telem["timed_out"] is True
    assert telem["used_rpp"] is False
