# kg — knowledge graph for codebase context retrieval

A deterministic, file-level knowledge graph built from your repository.
Given a seed file path, it returns ranked related files with per-source
attribution. Designed to feed AI coding agents in large codebases so they
find the right context quickly without overlooking things.

- **Nodes:** files
- **Edges:** directed, weighted, tagged by source indexer
- **MVP edge sources:** Git co-change history, SCIP cross-references
- **Retrieval:** Reciprocal Rank Fusion across sources
- **Storage:** single SQLite file per repo, no daemon
- **Surfaces:** Python library, `kg` CLI, `kg-mcp` MCP server

---

## Table of contents

1. [Installation](#installation)
2. [SCIP bindings (one-time setup)](#scip-bindings-one-time-setup)
3. [Quick start](#quick-start)
4. [Concepts](#concepts)
5. [CLI reference](#cli-reference)
6. [Python library API](#python-library-api)
7. [MCP server](#mcp-server)
8. [Built-in indexers](#built-in-indexers)
9. [Retrieval: how Reciprocal Rank Fusion works](#retrieval-how-reciprocal-rank-fusion-works)
10. [Tuning recommendations](#tuning-recommendations)
11. [Adding a new indexer](#adding-a-new-indexer)
12. [Storage layout](#storage-layout)
13. [Determinism guarantees](#determinism-guarantees)
14. [Evaluation harness](#evaluation-harness)
15. [Troubleshooting](#troubleshooting)
16. [Limitations and roadmap](#limitations-and-roadmap)

---

## Installation

`kg` is a regular Python project with a `pyproject.toml`. Any environment
manager works (conda, venv, uv, poetry); the examples below use conda.

```bash
cd kg/
conda create -n kg python=3.11 -y
conda activate kg
pip install -e ".[dev]"        # core + pytest
# Optional extras:
pip install -e ".[mcp]"        # enables `kg-mcp` MCP server
pip install grpcio-tools       # needed to (re)generate SCIP protobuf bindings
```

After installation, two console scripts are on your `PATH` inside the env:

| script    | purpose                                  |
| --------- | ---------------------------------------- |
| `kg`      | build the graph, query it, inspect it    |
| `kg-mcp`  | run an MCP server exposing `related_files` |

Verify it worked:

```bash
kg --help
kg sources           # lists the registered indexers + their versions
```

---

## SCIP bindings (one-time setup)

The SCIP indexer needs Python bindings for Sourcegraph's `scip.proto`. These
are **not** shipped with the package because generating them requires
`protoc`, which is heavy and platform-specific. Generate them once with:

```bash
pip install grpcio-tools
./scripts/build_scip_proto.sh
```

This downloads `scip.proto` from the upstream Sourcegraph repo and emits
`src/kg/indexers/_scip_pb2.py`. The SCIP indexer auto-detects this file; if
it isn't generated, the indexer logs a warning and skips itself (the rest
of the build still succeeds).

You also need actual `.scip` index files inside your repo. Producing those
is per-language:

| language    | tool                                                                   |
| ----------- | ---------------------------------------------------------------------- |
| TypeScript  | [`scip-typescript`](https://github.com/sourcegraph/scip-typescript)    |
| Python      | [`scip-python`](https://github.com/sourcegraph/scip-python)            |
| Go          | [`scip-go`](https://github.com/sourcegraph/scip-go)                    |
| Java/Kotlin | [`scip-java`](https://github.com/sourcegraph/scip-java)                |

Run the appropriate tool inside the directory you want indexed; it writes
an `index.scip` next to your source. `kg` will discover any `*.scip` /
`index.scip` files under the repo root (excluding `node_modules`, `.git`,
`.kg`, `dist`, `build`, `venv`, `.venv`, `__pycache__`).

---

## Quick start

From inside the repo you want to analyse:

```bash
# 1. Build the graph.
kg build --repo /path/to/your/repo

# 2. Query.
kg query --repo /path/to/your/repo path/relative/to/repo/Foo.js -k 10

# 3. Inspect.
kg stats --repo /path/to/your/repo
```

Or from within `kg/` against the parent repo:

```bash
kg build --repo ..
kg query --repo .. path/to/file/index.js -k 10
```

Example output:

```
 0.0325  path/to/related/file_a.js   [git_cochange#2, scip#1]
 0.0325  path/to/related/file_b.js   [git_cochange#1, scip#2]
 0.0313  path/to/related/file_c.js   [git_cochange#5, scip#3]
 ...
```

Each line is `score  path  [why]`, where `why` lists the per-source rank
the file held in each source that contributed to its score.

---

## Concepts

**Indexer.** A pluggable component that produces `Edge` records by analysing
the repo. Each indexer has a `name` (e.g. `git_cochange`, `scip`) and a
`version`. The name is also the edge `source` tag.

**Edge.** A directed, weighted relationship between two files, tagged with
the source indexer's name. Multiple indexers may emit edges between the
same pair; they coexist in storage and are fused at query time.

**Symmetric vs. directed sources.** Some relations are symmetric (git
co-change: if A and B change together, the relation goes both ways).
Symmetric indexers emit both `(A → B)` and `(B → A)`. Asymmetric ones (SCIP
"file references → file defines") emit one direction.

**Source.** Synonym for "indexer name as it appears on an edge". Use the
term `source` when talking about queries (`--source`, `--source-weight`)
and `indexer` when talking about the producing component.

**Score.** A float assigned by retrieval to each candidate file, the result
of fusing per-source rank lists. Higher is more relevant. Scores are
**not** comparable across repos or even across different queries on the
same repo — only within one query's result set.

**Contribution.** Attribution for one result file from one source: the
rank the file held in that source's neighbor list, plus the raw stored
edge weight. Surfaced as the `[git_cochange#N, scip#M]` annotations.

---

## CLI reference

All commands accept `--help`. The top-level command is `kg`.

### `kg build`

Build (or completely rebuild) the graph.

| flag                                         | default                | description                                                          |
| -------------------------------------------- | ---------------------- | -------------------------------------------------------------------- |
| `--repo PATH`                                | `cwd`                  | Repository root.                                                     |
| `--db PATH`                                  | `<repo>/.kg/graph.db`  | Override the SQLite file location.                                   |
| `--source NAME` / `-s NAME`                  | all registered         | Repeat to select multiple. Other sources are left untouched.         |
| `--max-files N`                              | indexer default (100)  | `git_cochange` only: skip commits touching more than N files.        |
| `--cochange-scheme NAME`                     | `inv_n_minus_1`        | `git_cochange` only: per-pair weight formula. See [weight schemes](#git_cochange). |

The build is wrapped in a SQLite transaction — either the whole rebuild
commits or nothing changes. Building one source does **not** clear the
others; their edges remain.

Example:

```bash
kg build --repo .. -s git_cochange --max-files 50 --cochange-scheme inv_n
```

### `kg query`

Return ranked related files for a seed.

| flag                                          | default | description                                                                 |
| --------------------------------------------- | ------- | --------------------------------------------------------------------------- |
| `FILE` (positional)                           | —       | Seed path. Repo-relative or absolute. Relative paths are used as-is.        |
| `--repo PATH`                                 | `cwd`   | Repository root.                                                            |
| `--db PATH`                                   | default | Override DB location.                                                       |
| `--top-k N` / `-k N`                          | `20`    | Number of results to return.                                                |
| `--source NAME` / `-s NAME`                   | all     | Restrict fusion to specific sources. Repeatable.                            |
| `--source-weight NAME=VALUE` / `-w NAME=VALUE`| all `1.0` | Per-source RRF weight. Repeatable. Example: `-w git_cochange=2 -w scip=1`. |
| `--json`                                      | off     | Emit JSON instead of the tabular format.                                    |

Exit code is `1` (with a message on stderr) if no results were found —
useful for shell scripting. Successful queries always exit `0`.

JSON shape:

```json
[
  {
    "path": "path/to/related/file_a.js",
    "score": 0.0325,
    "contributions": [
      {"source": "git_cochange", "rank": 2, "weight": 1.7},
      {"source": "scip",         "rank": 1, "weight": 31}
    ]
  },
  ...
]
```

### `kg stats`

Show graph diagnostics: node count, edge count per source, schema version,
the commit the graph was built against, and a warning if `HEAD` has moved
since the build.

```json
{
  "nodes": 2832,
  "edges_per_source": {
    "git_cochange": 108194,
    "scip": 7162
  },
  "schema_version": "1",
  "repo_head": "057165c960d5778b6c5f1da673f270b3d147cf1b",
  "built_at": "1779908157"
}
```

### `kg sources`

List all registered indexers and their versions.

```
git_cochange  v3
scip          v1
```

### `kg eval`

Run the evaluation harness against a directory of fixture JSON files.

```bash
kg eval --fixtures kg/eval/fixtures
```

See [Evaluation harness](#evaluation-harness).

---

## Python library API

`kg` is also a library. Top-level imports:

```python
from kg import Graph, Result, Contribution, open_graph
from kg.graph import Edge, default_db_path, transaction
from kg import indexers
```

### Opening a graph

```python
from pathlib import Path
from kg.graph import open_graph, default_db_path

graph = open_graph(default_db_path(Path("/path/to/repo")))
```

`open_graph` creates parent directories, initialises schema if needed, and
returns a `Graph`. Idempotent on existing DBs.

### Building programmatically

```python
from kg import indexers
from kg.graph import open_graph, default_db_path, transaction
from pathlib import Path

repo = Path("/path/to/repo")
graph = open_graph(default_db_path(repo))

with transaction(graph):
    for name in ["git_cochange", "scip"]:
        idx = indexers.get(name)            # or indexers.get(name, **config)
        graph.clear_source(idx.name)
        graph.add_edges(idx.index(repo))
```

`transaction` rolls back on any exception. Inside the block you can call
`graph` methods freely; the commit happens on successful exit.

### Querying

```python
results = graph.query(
    "path/to/file/index.js",
    top_k=20,
    sources=None,                # None = use every source present
    per_source_limit=200,        # candidates fetched per source
    rrf_k=60,                    # RRF dampening constant
    source_weights={"git_cochange": 2.0, "scip": 1.0},
)
for r in results:
    print(r.score, r.path)
    for c in r.contributions:
        print(f"  {c.source}: rank {c.rank}, raw weight {c.weight}")
```

`results` is a list of `Result(path: str, score: float, contributions: tuple[Contribution, ...])`.
Returns `[]` if the seed isn't a known node.

### Inspecting

```python
graph.sources()                # ["git_cochange", "scip"]
graph.stats()                  # dict with counts, head, etc.
graph.get_meta("repo_head")    # any meta key
```

---

## MCP server

`kg-mcp` exposes the graph to MCP-capable clients (Claude Code, Claude
Desktop, Cursor, etc.) as a single tool named `related_files(path, top_k)`.

### Wiring into Claude Code

Add to `~/.config/claude-code/mcp.json` (or your project's MCP config):

```json
{
  "mcpServers": {
    "kg": {
      "command": "kg-mcp",
      "env": {
        "KG_REPO": "/absolute/path/to/your/repo"
      }
    }
  }
}
```

If `kg-mcp` isn't on Claude Code's `PATH`, either point `command` at the
absolute path (`/path/to/conda/envs/kg/bin/kg-mcp`) or invoke via the
interpreter:

```json
{
  "command": "/path/to/conda/envs/kg/bin/python",
  "args": ["-m", "kg.mcp_server"],
  "env": {"KG_REPO": "..."}
}
```

### Environment variables

| name      | default               | description                          |
| --------- | --------------------- | ------------------------------------ |
| `KG_REPO` | `cwd`                 | Repository root for path normalisation. |
| `KG_DB`   | `<KG_REPO>/.kg/graph.db` | Override DB location.            |

### The exposed tool

```
related_files(path: str, top_k: int = 20) -> list[dict]
```

Returns:

```json
[
  {
    "path": "...",
    "score": 0.032,
    "why": [
      {"source": "git_cochange", "rank": 2, "weight": 1.7},
      {"source": "scip",         "rank": 1, "weight": 31}
    ]
  }
]
```

The server is **stateless and read-only**: it opens the existing SQLite DB
on each call. You must run `kg build` separately; the MCP server never
mutates the graph.

---

## Built-in indexers

### `git_cochange`

Walks `git log --no-merges --name-only` and emits pairwise undirected edges
between all files changed in the same commit. Repeated co-changes
accumulate weight automatically.

**Config (constructor / CLI):**

| parameter              | CLI flag                | default            | meaning                                                 |
| ---------------------- | ----------------------- | ------------------ | ------------------------------------------------------- |
| `max_files_per_commit` | `--max-files`           | `100`              | Skip commits touching more than this many files.        |
| `weight_scheme`        | `--cochange-scheme`     | `inv_n_minus_1`    | Per-pair weight formula. Options below.                 |

**Weight schemes** (for a commit touching X files):

| scheme            | per-pair weight       | per-file outgoing per commit | behavior                              |
| ----------------- | --------------------- | ---------------------------- | ------------------------------------- |
| `inv_pair`        | `1 / C(X, 2)`         | `2 / X`                      | Heavy dampening; large commits matter very little. |
| `inv_sqrt_pair`   | `1 / √C(X, 2)`        | grows like `√X`              | Moderate dampening.                   |
| `inv_n_minus_1`   | `1 / (X − 1)`         | exactly `1.0`                | **Default.** Each file contributes total 1.0 outgoing per commit, independent of X. |
| `inv_n`           | `1 / X`               | `(X − 1) / X`                | Slight bias toward larger commits.    |

**When to deviate from defaults:**

- If your repo has many large but coherent commits (feature rollouts,
  whole-module refactors), keep `max_files` at 100 or higher and the
  default scheme — both already favor larger commits more than the
  original strict formula did.
- If your repo has mass-rewrite commits (initial imports, vendor dumps),
  keep `max_files` modest (50–200) to exclude them. The script
  `scripts/commit_size_hist.py` plots the distribution to help pick.
- Switch to `inv_pair` only if mid-sized commits in your history are
  predominantly noise.

### `scip`

Consumes `.scip` files (Sourcegraph's cross-language index format) and
emits directed reference edges: `file_containing_reference → file_containing_definition`.

**Config:**

| parameter      | default                                                       | meaning                                       |
| -------------- | ------------------------------------------------------------- | --------------------------------------------- |
| `patterns`     | `("*.scip", "index.scip")`                                    | Glob patterns to discover SCIP files.         |
| `ref_cap`      | `50.0`                                                        | Cap raw reference count to prevent ubiquitous utilities from dominating. |
| `exclude_dirs` | `{"node_modules", ".git", ".kg", "dist", "build", "venv", ".venv", "__pycache__"}` | Skip SCIP files under these directories. |

**Path alignment.** A SCIP file at `frontend/index.scip` indexes documents
with paths relative to `frontend/`. To align with git's repo-root-relative
paths, `kg` prefixes SCIP `relative_path` with the SCIP file's directory.
This matters: without it, SCIP edges and git edges live at different
nodes and never fuse.

**Producing `.scip` files.** Run the appropriate language tool
(`scip-typescript`, `scip-python`, etc.) inside the directory you want
indexed. See [SCIP bindings](#scip-bindings-one-time-setup).

---

## Retrieval: how Reciprocal Rank Fusion works

The query algorithm:

1. Look up the seed file's node id.
2. For each active source, fetch the top-N neighbors (default `per_source_limit=200`),
   ordered by `weight DESC, dst_id ASC`. The secondary sort breaks ties
   deterministically.
3. Fuse with Reciprocal Rank Fusion:

   ```
   score(d) = Σ_source  w_source / (rrf_k + rank_source(d))
   ```

   where `w_source` defaults to 1.0 and `rrf_k` defaults to 60.
4. Sort `(−score, path)` and return the top `top_k`.

**Why RRF?** Raw edge weights from different indexers live on different
scales (`git_cochange`: fractions of 1; `scip`: integer reference counts
up to 50). Adding them directly lets the bigger-magnitude source dominate
regardless of signal quality. RRF discards raw magnitudes and works only
on rank order — a file that's #1 in any source gets the same contribution
whether the underlying weight was 0.8 or 80.

**Tuning knobs:**

| knob                | typical range  | effect                                                        |
| ------------------- | -------------- | ------------------------------------------------------------- |
| `rrf_k`             | 30–100         | Lower = top ranks dominate more sharply. 60 is the canonical default. |
| `per_source_limit`  | 50–500         | Maximum candidates considered per source. Higher = more recall, more cost. |
| `source_weights`    | per source     | Bias fusion toward a source. `-w git_cochange=2 -w scip=1` doubles git's influence. |

---

## Tuning recommendations

These are starting points; use the eval harness to validate on your repo.

**Per-source weights.** The right balance depends on the repo:

- **SCIP-heavy repos** (well-typed TypeScript, Go, Java): SCIP is more
  precise — try `-w scip=2 -w git_cochange=1`.
- **Polyglot repos with weak SCIP coverage** (Python with dynamic imports,
  shell, configs): the default equal weighting works, or favor git.
- **Mature repos with long history**: git_cochange has more cumulative
  signal — default or slight bias toward git.
- **Young repos**: git history is thin; lean on SCIP.

**`max_files` cutoff.** Run `python scripts/commit_size_hist.py --repo .`
to plot the distribution. Look for the inflection between "legitimate
large commits" (feature rollouts, module-wide refactors) and "mass
operations" (initial imports, lint sweeps over the whole repo, vendor
dumps).

**Cochange weight scheme.** Default `inv_n_minus_1` is the sensible
middle. Switch to `inv_pair` if mid-sized commits in your repo are mostly
noise; switch to `inv_n` if you want larger coherent commits to count
even more.

---

## Adding a new indexer

Three steps:

1. **Create `src/kg/indexers/your_indexer.py`** implementing the protocol:

   ```python
   from pathlib import Path
   from typing import Iterator

   from kg.graph import Edge
   from kg.indexers import register


   class YourIndexer:
       name = "your_indexer"
       version = "1"

       def __init__(self, some_option: int = 42):
           self.some_option = some_option

       def index(self, repo: Path) -> Iterator[Edge]:
           # Walk repo, analyse, yield Edge(src, dst, source=self.name, weight=...).
           ...


   @register
   def _factory(**kw) -> YourIndexer:
       return YourIndexer(**kw)
   ```

2. **Import it in `src/kg/indexers/__init__.py`'s `_load_builtins`:**

   ```python
   def _load_builtins() -> None:
       from kg.indexers import git_cochange  # noqa: F401
       from kg.indexers import scip          # noqa: F401
       from kg.indexers import your_indexer  # noqa: F401
   ```

3. **(Optional) Expose CLI flags for its config** by editing
   `kg build` in `src/kg/cli.py` if you want first-class flags. Otherwise
   the indexer always uses its constructor defaults at the CLI.

Add tests under `tests/test_your_indexer.py`. The pattern from
`test_git_cochange.py` (synthetic mini-repo fixture in `conftest.py`)
generalises.

**Versioning convention.** Bump `version` whenever a change to the
indexer's code would produce different edges for the same input. This is
informational today; future incremental-update logic will use it for
cache invalidation.

---

## Storage layout

One SQLite file per repo, default `<repo>/.kg/graph.db`. Schema:

```sql
nodes(
  id    INTEGER PRIMARY KEY,
  path  TEXT NOT NULL UNIQUE,
  kind  TEXT NOT NULL DEFAULT 'file',
  meta  TEXT NOT NULL DEFAULT '{}'   -- JSON
);

edges(
  src    INTEGER NOT NULL,
  dst    INTEGER NOT NULL,
  source TEXT NOT NULL,
  weight REAL NOT NULL,
  meta   TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (src, dst, source)
);

CREATE INDEX edges_src_source ON edges(src, source, weight DESC);
CREATE INDEX edges_dst_source ON edges(dst, source, weight DESC);

meta(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

**Notable meta keys:**

| key                              | written by | meaning                              |
| -------------------------------- | ---------- | ------------------------------------ |
| `schema_version`                 | schema init | DB schema version.                  |
| `built_at`                       | schema init | Unix timestamp of first build.       |
| `repo_head`                      | `kg build`  | Git SHA of the build's HEAD.         |
| `indexer_version:<name>`         | `kg build`  | Stored copy of the indexer's `version`. |
| `indexer_config:<name>`          | `kg build`  | JSON of non-default config used.     |

The DB is safe to commit to git, share, or distribute as a build artifact.
It's reproducible from the same repo state and indexer versions.

---

## Determinism guarantees

For a given repo state and indexer set, the graph and all query outputs
are byte-identical across runs. This is enforced by:

- Sorted iteration in every indexer (`sorted(files)`, `sorted(counts.items())`).
- Sorted tie-breakers in SQL (`ORDER BY weight DESC, dst ASC`).
- Sorted tie-breaker in retrieval (`sort key (-score, path)`).
- No embeddings, no model calls, no time-dependent logic in the core path
  (only `built_at` is wall-clock, and it's not consulted by queries).
- `indexer.version` strings are stored so behavior changes are detectable.

Two clean builds against the same git commit produce identical edge tables
(modulo the `built_at` meta value). There's a test for this in
`tests/test_graph.py::test_determinism`.

---

## Evaluation harness

Tucked under `src/kg/eval/`. A fixture is a JSON file:

```json
{
  "repo": "../path/to/repo",
  "queries": [
    {"seed": "src/foo.py",  "expected": ["src/bar.py", "tests/test_foo.py"]},
    {"seed": "src/auth.py", "expected": ["src/users.py"]}
  ]
}
```

Run:

```bash
kg eval --fixtures path/to/fixtures
```

Outputs JSON with recall@5, recall@10, recall@20, and MRR per fixture.
Fixtures must be pre-built (`kg build` first); the harness only queries.

The recommended workflow when changing fusion weights, edge schemes, or
adding a new indexer:

1. Hand-curate a small fixture (5 repos × 5–10 queries is enough).
2. Snapshot baseline metrics.
3. Make the change, re-build, re-eval.
4. Decide based on numbers rather than vibes.

---

## Troubleshooting

**"`no results for <path>`"** — the seed isn't in the graph. Either:
(a) the path isn't repo-relative as the indexers stored it, (b) no
indexer produced any edges incident on that file, or (c) the graph wasn't
built. Check `kg stats` and confirm with a SQL probe:

```bash
sqlite3 .kg/graph.db "SELECT path FROM nodes WHERE path LIKE '%Foo%' LIMIT 10"
```

**SCIP indexer logs "protobuf bindings are missing"** — run
`scripts/build_scip_proto.sh` and rebuild. The graph builds fine without
it, just without SCIP edges.

**SCIP edges aren't fusing with git edges** — usually a path-alignment
issue. SCIP `relative_path` is project-relative; `kg` prefixes with the
`.scip` file's directory. If your SCIP file lives somewhere unexpected
(e.g. dumped at the repo root from a sub-project), paths won't align.
Move the SCIP file under the project directory it indexes, or extend
`ScipIndexer` to read `Index.metadata.project_root`.

**"`warning: graph built at <sha>, HEAD is <other-sha>`"** — informational.
Queries still work; results reflect the snapshot at build time. Rebuild
to refresh.

**Pylance / IDE doesn't resolve imports** — IDE is pointed at the wrong
interpreter. Select the conda env's Python explicitly
(`~/miniconda3/envs/kg/bin/python` or similar).

**Tests are slow / hang** — the `git_cochange` integration tests shell out
to `git`; make sure `git` is on `PATH` inside the test environment.

---

## Limitations and roadmap

Explicitly out of scope for v1, in rough priority order:

- **Symbol-level nodes.** Files are coarse; SCIP and tree-sitter natively
  operate on symbols. Adding symbol nodes (and aggregating up to files at
  query time) is the biggest fidelity win.
- **More query inputs.** Today only file paths. Adding symbol names,
  diffs, and free-text would broaden the surface considerably.
- **Tree-sitter indexer.** Language-agnostic static imports/references.
  Cheap to add via the plugin interface; would fill SCIP-shaped coverage gaps.
- **Multi-hop traversal.** Currently 1-hop only. BFS with score decay or
  PageRank-style propagation would help surface indirect dependencies.
- **Incremental updates.** Today `kg build` is a full rebuild per source.
  Walking only new git history and re-parsing only changed `.scip` files
  would make refresh near-instant. The `version` field is wired up for
  this future migration.
- **Time decay** on git co-change so older history weighs less. Currently
  uniform.
- **Very large monorepos.** SQLite + naive in-process queries are fine up
  to ~100k files. Beyond that, partitioned storage or a dedicated graph
  store may be necessary.
- **Cross-repo / external dependencies** as nodes.
