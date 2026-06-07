"""Plot the distribution of commit sizes (files changed per commit).

Run:
    python scripts/commit_size_hist.py --repo .. --out commit_sizes.png

Outputs:
  - PNG histogram (one bar per bucket; log y-scale).
  - Percentile table to stdout so you can pick a defensible max_files cutoff.
"""
from __future__ import annotations

import argparse
import subprocess
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np


def commit_sizes(repo: Path) -> list[int]:
    sep = "@@KG_COMMIT@@"
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--no-merges", "--name-only",
         f"--pretty=format:{sep}"],
        capture_output=True, text=True, check=True,
    ).stdout

    sizes: list[int] = []
    for chunk in out.split(sep):
        lines = [ln for ln in chunk.strip().splitlines() if ln]
        # Drop the empty separator boundaries; only count non-empty file lists.
        if lines:
            sizes.append(len(set(lines)))
    return sizes


def print_summary(sizes: list[int]) -> None:
    arr = np.array(sizes)
    pcts = [50, 75, 90, 95, 99, 99.5, 99.9]
    print(f"total commits: {len(arr)}")
    print(f"min / mean / max: {arr.min()} / {arr.mean():.1f} / {arr.max()}")
    print("percentiles:")
    for p in pcts:
        print(f"  p{p:>5}: {int(np.percentile(arr, p))} files")
    # Coverage at common cutoffs.
    print("coverage at candidate cutoffs (% of commits kept, % of edges kept):")
    pair_count = np.array([n * (n - 1) // 2 if n >= 2 else 0 for n in arr])
    total_pairs = pair_count.sum()
    for cutoff in [10, 25, 50, 100, 200, 500, 1000]:
        kept_commits = (arr <= cutoff).sum()
        kept_pairs = pair_count[arr <= cutoff].sum()
        print(
            f"  cutoff={cutoff:>5}: commits {kept_commits / len(arr):6.1%}"
            f"   pairs {kept_pairs / total_pairs if total_pairs else 0:6.1%}"
        )


def plot(sizes: list[int], out: Path) -> None:
    # Custom buckets emphasise the small/mid range where the decision lies.
    edges = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610,
             max(1000, max(sizes) + 1)]
    counts, _ = np.histogram(sizes, bins=edges)
    labels = [f"{a}–{b - 1}" if b - 1 > a else f"{a}" for a, b in zip(edges, edges[1:])]

    fig, ax = plt.subplots(figsize=(11, 5))
    bars = ax.bar(range(len(counts)), counts, color="steelblue")
    ax.set_xticks(range(len(counts)))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_yscale("log")
    ax.set_xlabel("files changed in commit")
    ax.set_ylabel("number of commits (log scale)")
    ax.set_title(f"Commit-size distribution (n={len(sizes)} non-merge commits)")
    for bar, c in zip(bars, counts):
        if c:
            ax.text(bar.get_x() + bar.get_width() / 2, c, str(int(c)),
                    ha="center", va="bottom", fontsize=8)

    # Mark current default cutoff.
    cutoff = 25
    cutoff_bin = next(i for i, e in enumerate(edges[1:]) if cutoff < e)
    ax.axvline(cutoff_bin - 0.5, color="red", linestyle="--", linewidth=1,
               label=f"current default max_files = {cutoff}")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    print(f"wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--out", type=Path, default=Path("commit_sizes.png"))
    args = ap.parse_args()

    sizes = commit_sizes(args.repo)
    print_summary(sizes)
    plot(sizes, args.out)


if __name__ == "__main__":
    main()
