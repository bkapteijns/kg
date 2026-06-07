"""kg CLI: build / query / update / stats / eval."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import typer

from kg import indexers
from kg.graph import default_db_path, open_graph, transaction, default_html_path

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

_CONFIG_OWNER: dict[str, str] = {
    "max_files_per_commit": "git_cochange",
    "weight_scheme": "git_cochange",
}


def _repo_head(repo: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def _parse_kv_floats(items: list[str] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in items or []:
        if "=" not in item:
            raise typer.BadParameter(f"expected NAME=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = float(v)
    return out


@app.command()
def build(
    repo: Path = typer.Option(Path.cwd(), "--repo", help="Repository root."),
    db: Path | None = typer.Option(None, "--db", help="Override DB path."),
    source: list[str] = typer.Option(
        None, "--source", "-s", help="Indexer(s) to run. Default: all built-ins."
    ),
    max_files: int | None = typer.Option(
        None, "--max-files",
        help="git_cochange: skip commits touching > N files.",
    ),
    cochange_scheme: str | None = typer.Option(
        None, "--cochange-scheme",
        help="git_cochange weight scheme: inv_pair|inv_sqrt_pair|inv_n_minus_1|inv_n.",
    ),
) -> None:
    """Build (or rebuild) the graph from scratch."""
    db_path = db or default_db_path(repo)
    names = source or indexers.available()
    typer.echo(f"building graph at {db_path} with sources: {names}")

    option_values = {
        "max_files_per_commit": max_files,
        "weight_scheme": cochange_scheme,
    }
    configs: dict[str, dict] = {}
    for key, value in option_values.items():
        if value is not None:
            configs.setdefault(_CONFIG_OWNER[key], {})[key] = value

    graph = open_graph(db_path)
    with transaction(graph):
        for name in names:
            cfg = configs.get(name, {})
            idx = indexers.get(name, **cfg)
            graph.clear_source(idx.name)
            try:
                n = graph.add_edges(idx.index(repo))
            except Exception as exc:
                typer.echo(f"  {idx.name}: failed ({exc}); skipping", err=True)
                continue
            typer.echo(f"  {idx.name}: {n} edge writes")
            graph.set_meta(f"indexer_version:{idx.name}", idx.version)
            if cfg:
                graph.set_meta(f"indexer_config:{idx.name}", json.dumps(cfg, sort_keys=True))
        head = _repo_head(repo)
        if head:
            graph.set_meta("repo_head", head)

    typer.echo("done.")


@app.command()
def query(
    file: Path = typer.Argument(..., help="Seed file path (relative to repo root)."),
    repo: Path = typer.Option(Path.cwd(), "--repo"),
    db: Path | None = typer.Option(None, "--db"),
    top_k: int = typer.Option(20, "--top-k", "-k"),
    source: list[str] = typer.Option(None, "--source", "-s"),
    source_weight: list[str] = typer.Option(
        None, "--source-weight", "-w",
        help="Per-source RRF weight, e.g. --source-weight git_cochange=2 -w scip=1.",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Return ranked related files for a seed."""
    db_path = db or default_db_path(repo)
    graph = open_graph(db_path)
    if file.is_absolute():
        try:
            rel = str(file.resolve().relative_to(repo.resolve()))
        except ValueError:
            rel = str(file)
    else:
        rel = str(file)

    weights = _parse_kv_floats(source_weight) or None
    results = graph.query(rel, top_k=top_k, sources=source, source_weights=weights)

    if json_out:
        payload = [
            {
                "path": r.path,
                "score": r.score,
                "contributions": [
                    {"source": c.source, "rank": c.rank, "weight": c.weight}
                    for c in r.contributions
                ],
            }
            for r in results
        ]
        typer.echo(json.dumps(payload, indent=2))
        return

    if not results:
        typer.echo(f"no results for {rel}", err=True)
        raise typer.Exit(1)

    for r in results:
        why = ", ".join(f"{c.source}#{c.rank}" for c in r.contributions)
        typer.echo(f"{r.score:7.4f}  {r.path}   [{why}]")


@app.command()
def stats(
    repo: Path = typer.Option(Path.cwd(), "--repo"),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """Show graph diagnostics."""
    db_path = db or default_db_path(repo)
    graph = open_graph(db_path)
    s = graph.stats()
    typer.echo(json.dumps(s, indent=2))

    head_now = _repo_head(repo)
    if head_now and s.get("repo_head") and head_now != s["repo_head"]:
        typer.echo(
            f"warning: graph built at {s['repo_head'][:8]}, HEAD is {head_now[:8]}",
            err=True,
        )


@app.command()
def sources() -> None:
    """List registered indexers."""
    for name in indexers.available():
        idx = indexers.get(name)
        typer.echo(f"{idx.name}\tv{idx.version}")


@app.command()
def viz(
    repo: Path = typer.Option(Path.cwd(), "--repo"),
    db: Path | None = typer.Option(None, "--db"),
    out: Path | None = typer.Option(None, "--out", "-o", help="Output HTML path."),
    source: list[str] = typer.Option(
        None, "--source", "-s", help="Restrict to these sources. Default: all."
    ),
    seed: Path | None = typer.Option(
        None, "--seed", help="Center on this file's ego graph (recommended for big graphs)."
    ),
    hops: int = typer.Option(1, "--hops", help="How many edges out from the seed to include."),
    max_nodes: int = typer.Option(
        2000, "--max-nodes",
        help="Refuse to render an unseeded graph larger than this (force layout chokes).",
    ),
    open_browser: bool = typer.Option(False, "--open", help="Open the result in a browser."),
) -> None:
    """Write a self-contained interactive HTML visualization of the graph.

    For large graphs pass --seed FILE to render just that file's neighborhood;
    the full force-directed layout is unreadable past a few thousand nodes."""
    from kg.viz import render_html

    db_path = db or default_db_path(repo)
    out_path = out or default_html_path(repo)
    graph = open_graph(db_path)

    rel = None
    if seed is not None:
        if seed.is_absolute():
            try:
                rel = str(seed.resolve().relative_to(repo.resolve()))
            except ValueError:
                rel = str(seed)
        else:
            rel = str(seed)
        if graph.node_id(rel) is None:
            typer.echo(f"seed {rel!r} not found in graph; check the path prefix", err=True)
            raise typer.Exit(1)
    else:
        n_nodes = graph.stats()["nodes"]
        if n_nodes > max_nodes:
            typer.echo(
                f"graph has {n_nodes} nodes (> --max-nodes {max_nodes}); "
                f"pass --seed FILE to render a neighborhood, or raise --max-nodes.",
                err=True,
            )
            raise typer.Exit(1)

    html = render_html(graph, sources=source or None, seed=rel, hops=hops)
    out_path.write_text(html)
    typer.echo(f"wrote {out_path}")

    if open_browser:
        import webbrowser

        webbrowser.open(out_path.resolve().as_uri())


@app.command()
def eval(
    fixtures: Path = typer.Option(..., "--fixtures", help="Directory of fixture .json files."),
) -> None:
    """Run the evaluation harness against labeled fixtures."""
    from kg.eval.runner import run_directory

    results = run_directory(fixtures)
    if not results:
        typer.echo(f"no fixtures found under {fixtures}", err=True)
        raise typer.Exit(1)

    payload = [r.__dict__ for r in results]
    typer.echo(json.dumps(payload, indent=2))


if __name__ == "__main__":
    app()
