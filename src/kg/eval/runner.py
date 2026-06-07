"""Evaluation harness.

A fixture is a JSON file:
{
  "repo": "relative/path/to/repo",
  "queries": [
    {"seed": "src/foo.py", "expected": ["src/bar.py", "tests/test_foo.py"]},
    ...
  ]
}

For each fixture we build the graph (assumed pre-built or built by the
caller) and compute recall@k and mean reciprocal rank across all queries.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from kg.graph import default_db_path, open_graph


@dataclass
class EvalResult:
    fixture: str
    n_queries: int
    recall_at_5: float
    recall_at_10: float
    recall_at_20: float
    mrr: float


def _recall_at(ranked: list[str], expected: set[str], k: int) -> float:
    if not expected:
        return 0.0
    hits = sum(1 for p in ranked[:k] if p in expected)
    return hits / len(expected)


def _reciprocal_rank(ranked: list[str], expected: set[str]) -> float:
    for i, p in enumerate(ranked, start=1):
        if p in expected:
            return 1.0 / i
    return 0.0


def run_fixture(fixture_path: Path, top_k: int = 20) -> EvalResult:
    spec = json.loads(fixture_path.read_text())
    repo = (fixture_path.parent / spec["repo"]).resolve()
    graph = open_graph(default_db_path(repo))

    r5: list[float] = []
    r10: list[float] = []
    r20: list[float] = []
    rrs: list[float] = []
    for q in spec["queries"]:
        results = graph.query(q["seed"], top_k=top_k)
        ranked = [r.path for r in results]
        expected = set(q["expected"])
        r5.append(_recall_at(ranked, expected, 5))
        r10.append(_recall_at(ranked, expected, 10))
        r20.append(_recall_at(ranked, expected, 20))
        rrs.append(_reciprocal_rank(ranked, expected))

    n = max(len(spec["queries"]), 1)
    return EvalResult(
        fixture=str(fixture_path),
        n_queries=len(spec["queries"]),
        recall_at_5=sum(r5) / n,
        recall_at_10=sum(r10) / n,
        recall_at_20=sum(r20) / n,
        mrr=sum(rrs) / n,
    )


def run_directory(fixtures_dir: Path) -> list[EvalResult]:
    return [run_fixture(p) for p in sorted(fixtures_dir.glob("*.json"))]
