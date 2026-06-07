"""MCP server exposing `related_files` over stdio.

Configure in Claude Code (or any MCP client) as a stdio server pointing at
the `kg-mcp` console script with `KG_REPO` and optionally `KG_DB` in env.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from kg.graph import default_db_path, open_graph


def _resolve_paths() -> tuple[Path, Path]:
    repo = Path(os.environ.get("KG_REPO", Path.cwd())).resolve()
    db = Path(os.environ["KG_DB"]).resolve() if "KG_DB" in os.environ else default_db_path(repo)
    return repo, db


def _related_files(path: str, top_k: int = 20) -> list[dict[str, Any]]:
    repo, db = _resolve_paths()
    graph = open_graph(db)
    # Normalize the input to a repo-relative path when possible.
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            rel = str(candidate.resolve().relative_to(repo))
        except ValueError:
            rel = path
    else:
        rel = path

    results = graph.query(rel, top_k=top_k)
    return [
        {
            "path": r.path,
            "score": r.score,
            "why": [
                {"source": c.source, "rank": c.rank, "weight": c.weight}
                for c in r.contributions
            ],
        }
        for r in results
    ]


def main() -> None:
    """Entry point for the `kg-mcp` console script."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:
        raise SystemExit(
            "mcp package not installed. Install with: pip install 'kg[mcp]'"
        ) from e

    server = FastMCP("kg")

    @server.tool()
    def related_files(path: str, top_k: int = 20) -> list[dict[str, Any]]:
        """Return ranked related files for a seed path, with per-source attribution."""
        return _related_files(path, top_k=top_k)

    server.run()


if __name__ == "__main__":
    main()
