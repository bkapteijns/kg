from pathlib import Path

from kg.graph import Edge, open_graph, transaction


def test_add_and_query_basic(tmp_path: Path) -> None:
    graph = open_graph(tmp_path / "g.db")
    with transaction(graph):
        graph.add_edges([
            Edge("a.py", "b.py", "git_cochange", 1.0),
            Edge("b.py", "a.py", "git_cochange", 1.0),
            Edge("a.py", "c.py", "git_cochange", 0.5),
            Edge("c.py", "a.py", "git_cochange", 0.5),
            Edge("a.py", "b.py", "scip", 5.0),
            Edge("b.py", "a.py", "scip", 5.0),
        ])

    results = graph.query("a.py")
    paths = [r.path for r in results]
    assert paths[0] == "b.py"
    assert "c.py" in paths
    # b.py should appear via both sources.
    b_contribs = {c.source for c in results[0].contributions}
    assert b_contribs == {"git_cochange", "scip"}


def test_query_is_bidirectional(tmp_path: Path) -> None:
    # Only B->A exists (B references A's definition). Querying the definition A
    # must still surface its dependent B via incoming-edge traversal.
    graph = open_graph(tmp_path / "g.db")
    with transaction(graph):
        graph.add_edges([Edge("b.py", "a.py", "scip", 5.0)])

    assert [r.path for r in graph.query("a.py")] == ["b.py"]
    assert [r.path for r in graph.query("b.py")] == ["a.py"]


def test_mutual_edge_collapses_to_one_undirected_edge(tmp_path: Path) -> None:
    # Both directions of a pair canonicalize to a single undirected edge whose
    # weight is the accumulation of the two, and the neighbor appears once.
    graph = open_graph(tmp_path / "g.db")
    with transaction(graph):
        graph.add_edges([
            Edge("a.py", "b.py", "scip", 3.0),
            Edge("b.py", "a.py", "scip", 7.0),
        ])

    assert graph.stats()["edges_per_source"]["scip"] == 1
    results = graph.query("a.py")
    assert len(results) == 1
    (contrib,) = results[0].contributions
    assert contrib.weight == 10.0


def test_query_unknown_seed_returns_empty(tmp_path: Path) -> None:
    graph = open_graph(tmp_path / "g.db")
    assert graph.query("nonexistent.py") == []


def test_self_edges_dropped(tmp_path: Path) -> None:
    graph = open_graph(tmp_path / "g.db")
    with transaction(graph):
        graph.add_edges([Edge("a.py", "a.py", "git_cochange", 1.0)])
    assert graph.query("a.py") == []


def test_determinism(tmp_path: Path) -> None:
    def build(p: Path):
        g = open_graph(p)
        with transaction(g):
            g.add_edges([
                Edge("a", "b", "s", 1.0),
                Edge("a", "c", "s", 1.0),  # tie with a->b
            ])
        return [(r.path, round(r.score, 6)) for r in g.query("a")]

    assert build(tmp_path / "g1.db") == build(tmp_path / "g2.db")
