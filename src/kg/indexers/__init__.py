"""Indexer plugin interface and registry.

An indexer produces an iterable of `Edge` records for a repository. Each
indexer owns a `source` name (== indexer name) and a `version` string that
participates in cache invalidation.

Register a new indexer by importing this module and decorating the class
with `@register`. Factories may accept **kwargs to support per-indexer
configuration passed through from the CLI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from kg.graph import Edge


class Indexer(Protocol):
    name: str
    version: str

    def index(self, repo: Path) -> Iterator[Edge]: ...


IndexerFactory = Callable[..., Indexer]

_registry: dict[str, IndexerFactory] = {}


def register(factory: IndexerFactory) -> IndexerFactory:
    # Probe the factory with no args to learn its name. Factories must support
    # zero-arg invocation for default config.
    instance = factory()
    _registry[instance.name] = factory
    return factory


def get(name: str, **config: Any) -> Indexer:
    if name not in _registry:
        raise KeyError(f"unknown indexer: {name!r}. Known: {sorted(_registry)}")
    return _registry[name](**config)


def available() -> list[str]:
    return sorted(_registry)


# Import built-in indexers so they self-register.
def _load_builtins() -> None:
    from kg.indexers import git_cochange  # noqa: F401
    from kg.indexers import scip  # noqa: F401


_load_builtins()
