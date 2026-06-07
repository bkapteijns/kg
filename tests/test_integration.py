"""End-to-end: build against the synthetic mini-repo, query, verify ordering."""
from pathlib import Path

from kg import indexers
from kg.graph import open_graph, transaction


def test_full_pipeline_on_mini_repo(mini_repo: Path, tmp_path: Path) -> None:
    graph = open_graph(tmp_path / "g.db")
    with transaction(graph):
        for name in ["git_cochange"]:  # scip has no .scip files in the mini repo
            idx = indexers.get(name)
            graph.add_edges(idx.index(mini_repo))

    results = graph.query("a.py", top_k=5)
    paths = [r.path for r in results]
    assert paths[0] == "b.py", "strongest co-change pair should be top"
    assert "c.py" not in paths, "c never co-changed with a"

    # Build a second time on a fresh db and assert query output is byte-identical.
    graph2 = open_graph(tmp_path / "g2.db")
    with transaction(graph2):
        graph2.add_edges(indexers.get("git_cochange").index(mini_repo))
    results2 = graph2.query("a.py", top_k=5)
    assert [(r.path, round(r.score, 6)) for r in results] == \
           [(r.path, round(r.score, 6)) for r in results2]
