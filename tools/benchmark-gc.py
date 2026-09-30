#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["tomli-w>=1.0.0", "tqdm>=4.0.0"]
# ///
"""Compare GC scans against a Git revision on a disposable directory tree.

Run with uv run tools/benchmark-gc.py, optionally selecting --baseline REV. Measurements exclude
process startup and Git checks; metadata is warm. No user worktrees are changed.
"""

import argparse
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gwtlib import cache, gc  # noqa: E402


def load_baseline(revision):
    commit = subprocess.check_output(
        ['git', 'rev-parse', revision], cwd=REPO, text=True
    ).strip()
    source = subprocess.check_output(
        ['git', 'show', f'{commit}:gwtlib/gc.py'], cwd=REPO, text=True
    )
    module = types.ModuleType('gc_benchmark_baseline')
    sys.modules[module.__name__] = module
    exec(compile(source, f'{commit}:gwtlib/gc.py', 'exec'), module.__dict__)
    return commit, module.get_worktree_mtime


def metadata_calls(operation):
    calls = 0
    original_stat, original_lstat = os.stat, os.lstat

    def counted_stat(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_stat(*args, **kwargs)

    def counted_lstat(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_lstat(*args, **kwargs)

    with (
        patch.object(os, 'stat', counted_stat),
        patch.object(os, 'lstat', counted_lstat),
    ):
        operation()
    return calls


def benchmark_case(root, paths, cutoff, baseline, rounds):
    git_dir = str(root.parent / 'repo.git')
    cache.get_gc_cache_path(git_dir).unlink(missing_ok=True)
    expected = baseline(str(root))

    def cached_scan():
        # Each call loads a fresh hint list from disk; no process-local cache.
        return gc.get_worktree_info_list(git_dir, recent_days=7)[0].mtime

    # Exercise the real planning scan and cache I/O, replacing only Git queries
    # and progress output. No fixture commits or user repositories are needed.
    with (
        patch.object(
            gc,
            'get_worktree_list',
            return_value=[{'path': str(root), 'branch': 'benchmark'}],
        ),
        patch.object(gc, 'is_worktree_dirty', lambda *a: False),
        patch.object(gc, '_is_branch_merged_to_main', lambda *a: True),
        patch.object(gc, 'HAS_TQDM', False),
    ):
        start = time.perf_counter_ns()
        learned = cached_scan()
        cold_ms = (time.perf_counter_ns() - start) / 1_000_000
        assert learned == (expected if expected <= cutoff else None)
        if expected <= cutoff:
            # Measure the bounded worst case for an old tree: ten stale hints.
            cache.save_gc_paths(
                git_dir, [str(path.relative_to(root)) for path in paths[:10]]
            )
        operations = {
            'original': lambda: baseline(str(root)),
            'threshold': lambda: gc._scan_worktree_mtime(str(root), cutoff),
            'cached': cached_scan,
        }
        for name, operation in operations.items():
            assert operation() == (
                expected if name == 'original' or expected <= cutoff else None
            )
        timings = {name: [] for name in operations}
        names = list(operations)
        for iteration in range(rounds):
            # Rotate order so one implementation is not always measured first.
            offset = iteration % len(names)
            for name in names[offset:] + names[:offset]:
                start = time.perf_counter_ns()
                operations[name]()
                timings[name].append((time.perf_counter_ns() - start) / 1_000_000)
        return (
            {
                name: (statistics.median(timings[name]), metadata_calls(operation))
                for name, operation in operations.items()
            },
            cold_ms,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--baseline', default='6f764aa4a31218793b8e7f4066ef67c668a832b3'
    )
    parser.add_argument('--files', type=int, default=20_000)
    parser.add_argument('--directories', type=int, default=200)
    parser.add_argument('--rounds', type=int, default=9)
    parser.add_argument(
        '--temp-dir',
        type=Path,
        default=Path(os.environ.get('TMPDIR', '/tmp')) / 'agents',
    )
    args = parser.parse_args()
    if not (0 < args.directories <= args.files) or args.rounds < 1:
        parser.error('require 0 < directories <= files and rounds >= 1')
    commit, baseline = load_baseline(args.baseline)
    now = time.time()
    old = now - 40 * 86400
    cutoff = now - 7 * 86400
    args.temp_dir.mkdir(parents=True, exist_ok=True)
    print(f'Baseline: {commit}')
    print(
        f'Python {platform.python_version()}, {platform.system()}, {args.rounds} rounds, warm metadata'
    )
    print(
        f'{args.files:,} files + one stale hint file / {args.directories} directories'
    )
    print(
        'Excludes interpreter startup and Git queries; cached timings include disk reads/writes. Counts include stat + lstat.'
    )
    print(
        'Cache cold is one learning run; cache warm reloads disk each round. All-stale warm cache has ten stale hints.'
    )
    print(
        '| Scenario | Original ms | Threshold ms | Cache warm ms | Cache cold ms | Metadata calls (original / threshold / cached) |'
    )
    print('|---|---:|---:|---:|---:|---|')
    with (
        tempfile.TemporaryDirectory(dir=args.temp_dir) as temporary,
        patch.dict(os.environ, {'XDG_CACHE_HOME': str(Path(temporary) / 'cache')}),
    ):
        root = Path(temporary) / 'worktree'
        root.mkdir()
        directories = [root / f'directory-{i}' for i in range(args.directories)]
        for directory in directories:
            directory.mkdir()
        for i in range(args.files):
            file = directories[i % args.directories] / f'file-{i}'
            file.touch()
            os.utime(file, (old, old))
        stale_hint = root / 'known_stale.py'
        stale_hint.touch()
        os.utime(stale_hint, (old, old))
        # Select files in actual traversal order, without assuming lexical order.
        paths = [
            Path(directory) / name
            for directory, _, files in os.walk(root)
            for name in files
            if name != stale_hint.name
        ]
        cases = [
            ('All stale', None),
            ('Recent near start', paths[0]),
            ('Recent halfway', paths[len(paths) // 2]),
            ('Recent last', paths[-1]),
        ]
        for label, recent in cases:
            if recent is not None:
                os.utime(recent, (now, now))
            results, cold_ms = benchmark_case(
                root, paths, cutoff, baseline, args.rounds
            )
            timings = ' | '.join(
                f'{milliseconds:.3f}' for milliseconds, _ in results.values()
            )
            counts = ' / '.join(str(calls) for _, calls in results.values())
            print(f'| {label} | {timings} | {cold_ms:.3f} | {counts} |', flush=True)
            if recent is not None:
                os.utime(recent, (old, old))


if __name__ == '__main__':
    main()
