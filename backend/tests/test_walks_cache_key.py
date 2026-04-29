"""Wave 5E: walks cache key embeds the section's edge-set hash so a
re-partition cannot serve stale walks for a recycled section_id."""
from main import _section_edges_hash, _WALKS_V5_RE, _prune_orphan_walks_v5, CACHE_DIR


def test_edges_hash_changes_with_edge_set():
    a = {"section_id": 79, "edge_ids": ["a-b-0", "b-c-0", "c-d-0"]}
    b = {"section_id": 79, "edge_ids": ["a-b-0", "b-c-0", "x-y-0"]}
    assert _section_edges_hash(a) != _section_edges_hash(b)


def test_edges_hash_stable_and_order_insensitive():
    a = {"section_id": 79, "edge_ids": ["a-b-0", "b-c-0", "c-d-0"]}
    b = {"section_id": 79, "edge_ids": ["c-d-0", "a-b-0", "b-c-0"]}
    h1 = _section_edges_hash(a)
    h2 = _section_edges_hash(a)
    assert h1 == h2
    assert h1 == _section_edges_hash(b)
    assert len(h1) == 8
    int(h1, 16)  # hex


def test_prune_orphan_walks_v5_removes_stale(tmp_path, monkeypatch):
    import main as m
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path)

    sec = {"section_id": 7, "edge_ids": ["e1-0", "e2-0"]}
    valid_hash = _section_edges_hash(sec)

    keep = tmp_path / f"walks_v5_7_{valid_hash}_1.0.pkl"
    stale_hash = tmp_path / "walks_v5_7_deadbeef_1.0.pkl"
    stale_id = tmp_path / f"walks_v5_99_{valid_hash}_1.0.pkl"
    other = tmp_path / "graph_v4.pkl"
    legacy = tmp_path / "walks_v4_7_1.0.pkl"
    for p in (keep, stale_hash, stale_id, other, legacy):
        p.write_bytes(b"x")

    removed = m._prune_orphan_walks_v5([sec])
    assert removed == 2
    assert keep.exists()
    assert not stale_hash.exists()
    assert not stale_id.exists()
    # Untouched non-v5 files
    assert other.exists()
    assert legacy.exists()


def test_walks_v5_regex_shape():
    m = _WALKS_V5_RE.match("walks_v5_79_a1b2c3d4_1.0.pkl")
    assert m is not None
    assert m.group(1) == "79"
    assert m.group(2) == "a1b2c3d4"
    assert _WALKS_V5_RE.match("walks_v4_79_1.0.pkl") is None
    assert _WALKS_V5_RE.match("walks_v5_79_SHORT_1.0.pkl") is None
