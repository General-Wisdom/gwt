#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["tomli-w>=1.0.0", "tqdm>=4.0.0"]
# ///
"""Compare GC scan worker counts against a repository without changing it.

Uses one worktree/PR snapshot, fresh in-memory hints per run, and a fixed clock.
Includes pool startup and local Git checks; excludes the shared GitHub lookup.
Cache writes, cleaning, deletion, and Git index updates are disabled.
"""

import argparse
import os
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gwtlib import gc  # noqa: E402


def fingerprint(plan):
    return {
        (category, wt.path): (
            wt.branch,
            wt.mtime,
            wt.is_dirty,
            wt.is_merged,
            wt.head_oid,
            wt.merged_pr.number if wt.merged_pr else None,
        )
        for category in ['to_clean', 'to_delete', 'dirty', 'unmerged', 'skip']
        for wt in getattr(plan, category)
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', type=Path, help='Repository root or Git directory')
    parser.add_argument('--workers', type=int, nargs='+', default=[1, 2, 4, 8])
    parser.add_argument('--rounds', type=int, default=3)
    args = parser.parse_args()
    if args.rounds < 1 or any(workers < 1 for workers in args.workers):
        parser.error('rounds and worker counts must be positive')
    root = args.repo.resolve()
    git_dir = str(root / '.git' if (root / '.git').is_dir() else root)
    os.environ['GIT_OPTIONAL_LOCKS'] = '0'
    worktrees = gc.get_worktree_list(git_dir, include_main=False)
    main_branch = gc.get_main_branch_name(git_dir)
    start = time.perf_counter()
    prs = (
        gc.get_merged_prs(
            [wt['branch'] for wt in worktrees if wt.get('branch')], main_branch, git_dir
        )
        if main_branch
        else {}
    )
    print(f'Shared GitHub lookup: {time.perf_counter() - start:.3f}s', flush=True)
    hints = gc.load_gc_paths(git_dir)
    now = time.time()
    workers = list(dict.fromkeys(args.workers))
    samples = {count: [] for count in workers}
    reference = None
    with (
        patch.object(gc, 'get_worktree_list', return_value=worktrees),
        patch.object(gc, 'get_main_branch_name', return_value=main_branch),
        patch.object(gc, 'get_merged_prs', return_value=prs),
        patch.object(gc, 'load_gc_paths', side_effect=lambda *a: hints.copy()),
        patch.object(gc, 'save_gc_paths', lambda *a: None),
        patch.object(gc, 'HAS_TQDM', False),
        patch.object(gc.time, 'time', return_value=now),
    ):
        for iteration in range(args.rounds):
            offset = iteration % len(workers)
            for count in workers[offset:] + workers[:offset]:
                start = time.perf_counter()
                plan = gc.create_gc_plan(git_dir, workers=count)
                elapsed = time.perf_counter() - start
                samples[count].append(elapsed)
                result = fingerprint(plan)
                if reference is None:
                    reference = result
                changes = sum(
                    reference.get(key) != result.get(key)
                    for key in reference.keys() | result.keys()
                )
                print(
                    f'Round {iteration + 1}, workers={count}: {elapsed:.3f}s, '
                    f'{changes} changed plan entries',
                    flush=True,
                )
    print('\n| Workers | Median seconds |', flush=True)
    print('|---:|---:|')
    for count in workers:
        print(f'| {count} | {statistics.median(samples[count]):.3f} |')


if __name__ == '__main__':
    main()
