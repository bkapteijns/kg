"""SCIP indexer.

Consumes one or more SCIP index files (typically produced by `scip-python`,
`scip-typescript`, `scip-go`, etc.) and emits file-level reference edges:

    file_containing_reference  ──►  file_containing_definition

Weighted by reference count, capped to dampen ubiquitous utility files.

The SCIP protobuf bindings are generated separately into `_scip_pb2.py` to
keep the package free of build-time dependencies on `protoc`. Run
`scripts/build_scip_proto.sh` to (re)generate them. Until they exist this
indexer logs a warning and yields nothing — the rest of the build proceeds.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Iterator

from kg.graph import Edge
from kg.indexers import register

log = logging.getLogger(__name__)

NAME = "scip"
VERSION = "1"
DEFAULT_PATTERNS = ("*.scip", "index.scip")
DEFAULT_REF_CAP = 50.0
DEFAULT_EXCLUDE_DIRS = frozenset(
    {"node_modules", ".git", ".kg", "dist", "build", "venv", ".venv", "__pycache__"}
)


class ScipIndexer:
    name = NAME
    version = VERSION

    def __init__(
        self,
        patterns: tuple[str, ...] = DEFAULT_PATTERNS,
        ref_cap: float = DEFAULT_REF_CAP,
        exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    ):
        self.patterns = patterns
        self.ref_cap = ref_cap
        self.exclude_dirs = exclude_dirs

    def _discover(self, repo: Path) -> list[Path]:
        seen: set[Path] = set()
        for pat in self.patterns:
            for p in repo.rglob(pat):
                if not p.is_file():
                    continue
                if any(part in self.exclude_dirs for part in p.relative_to(repo).parts):
                    continue
                seen.add(p)
        return sorted(seen)

    def index(self, repo: Path) -> Iterator[Edge]:
        files = self._discover(repo)
        if not files:
            return

        try:
            from kg.indexers import _scip_pb2  # type: ignore[attr-defined]
        except ImportError:
            log.warning(
                "found %d .scip file(s) but protobuf bindings are missing; "
                "skipping SCIP indexer. Run scripts/build_scip_proto.sh to enable.",
                len(files),
            )
            return

        repo_resolved = repo.resolve()
        for scip_path in files:
            # SCIP document.relative_path is relative to the indexed project,
            # which usually lives at scip_path.parent (or a parent of it).
            # Prefixing with that directory's path relative to the repo root
            # aligns SCIP paths with git paths.
            prefix = str(scip_path.parent.resolve().relative_to(repo_resolved))
            if prefix == ".":
                prefix = ""
            yield from self._parse_one(scip_path, _scip_pb2, prefix)

    def _parse_one(self, scip_path: Path, pb, prefix: str) -> Iterator[Edge]:
        index = pb.Index()
        with scip_path.open("rb") as fh:
            index.ParseFromString(fh.read())

        # Map symbol -> file_of_definition (first definition wins; SCIP
        # documents emit definitions before references within a document).
        symbol_def_file: dict[str, str] = {}
        # Collect (ref_file, symbol) pairs to resolve after the first pass.
        ref_pairs: list[tuple[str, str]] = []

        # SCIP roles: 1 == Definition (lowest bit). See scip.proto.
        DEFINITION_ROLE = 1

        def _with_prefix(p: str) -> str:
            return f"{prefix}/{p}" if prefix else p

        for doc in index.documents:
            doc_path = _with_prefix(doc.relative_path)
            for occ in doc.occurrences:
                sym = occ.symbol
                if not sym or sym.startswith("local "):
                    continue
                is_def = bool(occ.symbol_roles & DEFINITION_ROLE)
                if is_def:
                    symbol_def_file.setdefault(sym, doc_path)
                else:
                    ref_pairs.append((doc_path, sym))

        # Aggregate reference counts per (ref_file -> def_file).
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for ref_file, sym in ref_pairs:
            def_file = symbol_def_file.get(sym)
            if def_file is None or def_file == ref_file:
                continue
            counts[(ref_file, def_file)] += 1

        for (ref_file, def_file), n in sorted(counts.items()):
            yield Edge(
                src=ref_file,
                dst=def_file,
                source=self.name,
                weight=min(float(n), self.ref_cap),
                meta={"references": n, "scip_index": str(scip_path.name)},
            )


@register
def _factory(**kw) -> ScipIndexer:
    return ScipIndexer(**kw)
