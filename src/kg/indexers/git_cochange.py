"""Git co-change indexer.

For every non-merge commit that touched ≤ MAX_FILES_PER_COMMIT files, emit
pairwise directed edges between all changed files. The per-pair weight is
determined by `weight_scheme`; see WEIGHT_SCHEMES below. Edges are emitted in
BOTH directions so the symmetric relation is preserved under the directed
schema, and repeated co-changes accumulate (handled by graph.add_edges via
ON CONFLICT).
"""
from __future__ import annotations

import math
import subprocess
from itertools import combinations
from pathlib import Path
from typing import Callable, Iterator

from kg.graph import Edge
from kg.indexers import register

NAME = "git_cochange"
VERSION = "1"
DEFAULT_MAX_FILES = 100

# Each scheme maps n (number of files in commit) -> per-pair weight.
# All assume n >= 2 (callers filter n < 2 before invoking).
WEIGHT_SCHEMES: dict[str, Callable[[int], float]] = {
    "inv_pair":        lambda n: 2.0 / (n * (n - 1)),   # 1/C(n,2): heavy dampening
    "inv_sqrt_pair":   lambda n: 1.0 / math.sqrt(n * (n - 1) / 2.0),
    "inv_n_minus_1":   lambda n: 1.0 / (n - 1),         # per-file outgoing = 1.0
    "inv_n":           lambda n: 1.0 / n,
}
DEFAULT_WEIGHT_SCHEME = "inv_n_minus_1"


class GitCochangeIndexer:
    name = NAME
    version = VERSION

    def __init__(
        self,
        max_files_per_commit: int = DEFAULT_MAX_FILES,
        weight_scheme: str = DEFAULT_WEIGHT_SCHEME,
    ):
        if weight_scheme not in WEIGHT_SCHEMES:
            raise ValueError(
                f"unknown weight_scheme {weight_scheme!r}; "
                f"available: {sorted(WEIGHT_SCHEMES)}"
            )
        self.max_files = max_files_per_commit
        self.weight_scheme = weight_scheme
        self._weight_fn = WEIGHT_SCHEMES[weight_scheme]

    def index(self, repo: Path) -> Iterator[Edge]:
        # `--name-only` + a unique commit-separator lets us parse without
        # confusing files named like SHAs. `--no-merges` excludes merge
        # commits whose file lists conflate independent changes.
        sep = "@@KG_COMMIT@@"
        out = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                "--no-merges",
                "--name-only",
                f"--pretty=format:{sep}%H",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

        for chunk in out.split(sep):
            chunk = chunk.strip()
            if not chunk:
                continue
            lines = chunk.splitlines()
            # lines[0] is the SHA; rest are file paths.
            files = sorted({ln for ln in lines[1:] if ln})
            n = len(files)
            if n < 2 or n > self.max_files:
                continue
            pair_weight = self._weight_fn(n)
            for a, b in combinations(files, 2):
                yield Edge(src=a, dst=b, source=self.name, weight=pair_weight)
                yield Edge(src=b, dst=a, source=self.name, weight=pair_weight)


@register
def _factory(**kw) -> GitCochangeIndexer:
    return GitCochangeIndexer(**kw)
