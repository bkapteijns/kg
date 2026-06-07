"""SQLite-backed graph storage + retrieval engine.

Schema is intentionally small: nodes are files, edges are undirected (a, b,
source) with a weight. Relatedness is symmetric, so endpoints are canonicalized
by node id at insert time (src < dst) and each unordered pair is stored exactly
once per source — querying a file surfaces both what it depends on and what
depends on it. Multiple sources coexist by tagging each edge with its `source`
name. Retrieval gathers per-source ranked neighbors of a seed and fuses them
with Reciprocal Rank Fusion.

All iteration is sorted for determinism.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  id    INTEGER PRIMARY KEY,
  path  TEXT NOT NULL UNIQUE,
  kind  TEXT NOT NULL DEFAULT 'file',
  meta  TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS edges (
  -- Undirected: src < dst is enforced at insert time, so (a,b) and (b,a)
  -- collapse to one row. The two columns are just the lower/higher endpoint.
  src    INTEGER NOT NULL,
  dst    INTEGER NOT NULL,
  source TEXT NOT NULL,
  weight REAL NOT NULL,
  meta   TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (src, dst, source)
);
CREATE INDEX IF NOT EXISTS edges_src_source ON edges(src, source, weight DESC);
CREATE INDEX IF NOT EXISTS edges_dst_source ON edges(dst, source, weight DESC);
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    source: str
    weight: float
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Contribution:
    source: str
    rank: int
    weight: float


@dataclass(frozen=True)
class Result:
    path: str
    score: float
    contributions: tuple[Contribution, ...]


class Graph:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- schema / meta ----------

    def init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.set_meta("schema_version", SCHEMA_VERSION)
        if self.get_meta("built_at") is None:
            self.set_meta("built_at", str(int(time.time())))
        self.conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    # ---------- nodes ----------

    def upsert_node(self, path: str, kind: str = "file", meta: dict | None = None) -> int:
        meta_json = json.dumps(meta or {}, sort_keys=True)
        cur = self.conn.execute(
            "INSERT INTO nodes(path, kind, meta) VALUES(?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET kind=excluded.kind, meta=excluded.meta "
            "RETURNING id",
            (path, kind, meta_json),
        )
        return cur.fetchone()[0]

    def node_id(self, path: str) -> int | None:
        row = self.conn.execute("SELECT id FROM nodes WHERE path=?", (path,)).fetchone()
        return row[0] if row else None

    def node_path(self, node_id: int) -> str | None:
        row = self.conn.execute("SELECT path FROM nodes WHERE id=?", (node_id,)).fetchone()
        return row[0] if row else None

    # ---------- edges ----------

    def add_edges(self, edges: Iterable[Edge]) -> int:
        """Bulk-insert edges. Endpoints are canonicalized (src < dst) so the
        graph is undirected; the two directions of a pair — and any other
        duplicate (src,dst,source) — accumulate weight."""
        n = 0
        for e in edges:
            src_id = self.upsert_node(e.src)
            dst_id = self.upsert_node(e.dst)
            if src_id == dst_id:
                continue
            if src_id > dst_id:
                src_id, dst_id = dst_id, src_id
            meta_json = json.dumps(e.meta, sort_keys=True)
            self.conn.execute(
                "INSERT INTO edges(src, dst, source, weight, meta) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(src, dst, source) DO UPDATE SET "
                "  weight = edges.weight + excluded.weight",
                (src_id, dst_id, e.source, e.weight, meta_json),
            )
            n += 1
        return n

    def clear_source(self, source: str) -> None:
        self.conn.execute("DELETE FROM edges WHERE source=?", (source,))

    def sources(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT source FROM edges ORDER BY source"
        ).fetchall()
        return [r[0] for r in rows]

    def stats(self) -> dict:
        n_nodes = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        per_source = self.conn.execute(
            "SELECT source, COUNT(*) FROM edges GROUP BY source ORDER BY source"
        ).fetchall()
        return {
            "nodes": n_nodes,
            "edges_per_source": {s: c for s, c in per_source},
            "schema_version": self.get_meta("schema_version"),
            "repo_head": self.get_meta("repo_head"),
            "built_at": self.get_meta("built_at"),
        }

    # ---------- visualization ----------

    def _hop(self, frontier: set[int], active: list[str]) -> set[int]:
        """One BFS step: neighbors of `frontier` over edges in `active` sources."""
        if not frontier:
            return set()
        node_ph = ",".join("?" for _ in frontier)
        src_ph = ",".join("?" for _ in active)
        params = [*frontier, *active, *frontier, *active]
        rows = self.conn.execute(
            f"SELECT dst AS other FROM edges WHERE src IN ({node_ph}) AND source IN ({src_ph}) "
            f"UNION SELECT src AS other FROM edges WHERE dst IN ({node_ph}) AND source IN ({src_ph})",
            params,
        ).fetchall()
        return {r[0] for r in rows}

    def viz_data(
        self,
        sources: list[str] | None = None,
        seed: str | None = None,
        hops: int = 1,
    ) -> dict:
        """Return a JSON-serializable {nodes, edges} snapshot of the graph.

        Nodes carry their path and degree; edges carry endpoint ids, source, and
        weight. Restrict to `sources` if given. If `seed` is given, restrict to
        the ego graph: the seed plus everything within `hops` edges of it (the
        seed node is flagged `seed: True`). All iteration is sorted for
        determinism, matching the rest of the module."""
        active = sorted(sources) if sources is not None else self.sources()
        if not active:
            return {"nodes": [], "edges": [], "seed": None}

        keep: set[int] | None = None
        seed_id: int | None = None
        if seed is not None:
            seed_id = self.node_id(seed)
            if seed_id is None:
                return {"nodes": [], "edges": [], "seed": None}
            keep = {seed_id}
            frontier = {seed_id}
            for _ in range(max(0, hops)):
                frontier = self._hop(frontier, active) - keep
                if not frontier:
                    break
                keep |= frontier

        src_ph = ",".join("?" for _ in active)
        edge_rows = self.conn.execute(
            f"SELECT src, dst, source, weight FROM edges "
            f"WHERE source IN ({src_ph}) ORDER BY source, src, dst",
            active,
        ).fetchall()
        if keep is not None:
            edge_rows = [
                row for row in edge_rows if row[0] in keep and row[1] in keep
            ]

        degree: dict[int, int] = {}
        for src, dst, _source, _weight in edge_rows:
            degree[src] = degree.get(src, 0) + 1
            degree[dst] = degree.get(dst, 0) + 1

        node_rows = self.conn.execute(
            "SELECT id, path FROM nodes ORDER BY path"
        ).fetchall()
        nodes = [
            {
                "id": nid,
                "path": path,
                "degree": degree.get(nid, 0),
                "seed": nid == seed_id,
            }
            for nid, path in node_rows
            if keep is None or nid in keep
        ]
        edges = [
            {"src": src, "dst": dst, "source": source, "weight": weight}
            for src, dst, source, weight in edge_rows
        ]
        return {"nodes": nodes, "edges": edges, "seed": seed}

    # ---------- retrieval ----------

    def _neighbors(self, seed_id: int, source: str, limit: int) -> list[tuple[int, float]]:
        """Return (neighbor_id, weight) for the seed's neighbors in `source`.

        Edges are undirected (canonicalized to src < dst at insert), so the seed
        may sit in either column: we gather pairs where it is the lower endpoint
        (neighbor is dst) and where it is the higher endpoint (neighbor is src).
        Each unordered pair is stored once, so the two arms can't surface the
        same neighbor twice; the GROUP BY MAX is a cheap guard against any
        non-canonical legacy rows. Sorted by (weight DESC, neighbor_id ASC) for
        deterministic tie-breaking. The edges_src_source and edges_dst_source
        indexes cover the two arms."""
        rows = self.conn.execute(
            "SELECT other, MAX(weight) AS w FROM ("
            "  SELECT dst AS other, weight FROM edges WHERE src=? AND source=? "
            "  UNION ALL "
            "  SELECT src AS other, weight FROM edges WHERE dst=? AND source=? "
            ") GROUP BY other "
            "ORDER BY w DESC, other ASC LIMIT ?",
            (seed_id, source, seed_id, source, limit),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def query(
        self,
        seed: str,
        top_k: int = 20,
        sources: list[str] | None = None,
        per_source_limit: int = 200,
        rrf_k: int = 60,
        source_weights: dict[str, float] | None = None,
    ) -> list[Result]:
        """Return ranked related files for `seed`.

        Per-source neighbors are fused with Reciprocal Rank Fusion:
            score(d) = Σ_s w_s / (rrf_k + rank_s(d))
        Ties broken by path lex order. Empty list if seed unknown.
        """
        seed_id = self.node_id(seed)
        if seed_id is None:
            return []

        active = sorted(sources) if sources is not None else self.sources()
        if not active:
            return []
        weights = source_weights or {}

        scores: dict[int, float] = {}
        contributions: dict[int, list[Contribution]] = {}

        for source in active:
            w = weights.get(source, 1.0)
            for rank, (dst_id, edge_weight) in enumerate(
                self._neighbors(seed_id, source, per_source_limit), start=1
            ):
                scores[dst_id] = scores.get(dst_id, 0.0) + w / (rrf_k + rank)
                contributions.setdefault(dst_id, []).append(
                    Contribution(source=source, rank=rank, weight=edge_weight)
                )

        # Resolve to paths and sort deterministically.
        results: list[Result] = []
        for dst_id, score in scores.items():
            path = self.node_path(dst_id)
            if path is None:
                continue
            contribs = tuple(
                sorted(contributions[dst_id], key=lambda c: (c.source, c.rank))
            )
            results.append(Result(path=path, score=score, contributions=contribs))

        results.sort(key=lambda r: (-r.score, r.path))
        return results[:top_k]


# ---------- top-level helpers ----------


def open_graph(db_path: Path) -> Graph:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    g = Graph(conn)
    g.init_schema()
    return g


@contextmanager
def transaction(graph: Graph) -> Iterator[Graph]:
    try:
        yield graph
        graph.conn.commit()
    except Exception:
        graph.conn.rollback()
        raise


def default_db_path(repo: Path) -> Path:
    return repo / ".kg" / "graph.db"


def default_html_path(repo: Path) -> Path:
    return repo / ".kg" / "graph.html"
