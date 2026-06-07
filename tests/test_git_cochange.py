from pathlib import Path

from kg.graph import open_graph, transaction
from kg.indexers.git_cochange import GitCochangeIndexer


def test_cochange_pairs_have_correct_relative_weights(mini_repo: Path, tmp_path: Path) -> None:
    indexer = GitCochangeIndexer()
    graph = open_graph(tmp_path / "g.db")
    with transaction(graph):
        graph.add_edges(indexer.index(mini_repo))

    results = graph.query("a.py", sources=["git_cochange"])
    paths = [r.path for r in results]

    # a and b changed together 3x; a and d changed together 1x; c never with a.
    assert paths[0] == "b.py"
    assert "d.py" in paths
    assert "c.py" not in paths

    # b should outrank d via RRF on a single source (b is rank 1, d is rank 2).
    b = next(r for r in results if r.path == "b.py")
    d = next(r for r in results if r.path == "d.py")
    assert b.score > d.score


def test_max_files_filter_skips_large_commits(mini_repo: Path, tmp_path: Path) -> None:
    # Setting max_files=1 means every commit (with >=2 files) is skipped.
    indexer = GitCochangeIndexer(max_files_per_commit=1)
    graph = open_graph(tmp_path / "g.db")
    with transaction(graph):
        graph.add_edges(indexer.index(mini_repo))
    assert graph.query("a.py", sources=["git_cochange"]) == []


def test_weight_scheme_inv_n_default(mini_repo: Path, tmp_path: Path) -> None:
    # Under inv_n_minus_1, a 2-file commit yields pair weight 1.0;
    # three commits of (a,b) together => weight ~3.0 on (a→b).
    indexer = GitCochangeIndexer()
    graph = open_graph(tmp_path / "g.db")
    with transaction(graph):
        graph.add_edges(indexer.index(mini_repo))
    row = graph.conn.execute(
        "SELECT weight FROM edges e JOIN nodes s ON e.src=s.id JOIN nodes d ON e.dst=d.id "
        "WHERE s.path='a.py' AND d.path='b.py' AND source='git_cochange'"
    ).fetchone()
    assert row is not None
    assert abs(row[0] - 3.0) < 1e-9
