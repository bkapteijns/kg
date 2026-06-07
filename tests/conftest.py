import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(repo), "PATH": _path_env()},
    )


def _path_env() -> str:
    import os
    return os.environ.get("PATH", "")


def _commit(repo: Path, files: dict[str, str], msg: str) -> None:
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        _git(repo, "add", rel)
    _git(repo, "commit", "-m", msg)


@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    # Commit 1: a.py and b.py change together (strong co-change pair).
    _commit(repo, {"a.py": "x = 1\n", "b.py": "y = 1\n"}, "init a/b")
    _commit(repo, {"a.py": "x = 2\n", "b.py": "y = 2\n"}, "bump a/b")
    _commit(repo, {"a.py": "x = 3\n", "b.py": "y = 3\n"}, "bump a/b again")

    # Commit: c.py changes alone (no co-change).
    _commit(repo, {"c.py": "z = 1\n"}, "init c")

    # Commit: a.py and d.py change together once (weaker pair).
    _commit(repo, {"a.py": "x = 4\n", "d.py": "w = 1\n"}, "a/d once")

    return repo
