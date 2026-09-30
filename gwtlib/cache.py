"""Disposable hints for finding recent worktree activity."""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

MAX_GC_PATHS = 10


def normalize_gc_path(path: object) -> str | None:
    """Accept only relative paths that can belong to the normal GC traversal."""
    if not isinstance(path, str) or not path or '\0' in path or os.path.isabs(path):
        return None
    normalized = os.path.normpath(path)
    if normalized == '.' or any(
        part in ('..', '.git') for part in normalized.split(os.sep)
    ):
        return None
    return normalized


def _bounded_paths(paths: list) -> list[str]:
    result = []
    for path in paths:
        normalized = normalize_gc_path(path)
        if normalized is not None and normalized not in result:
            result.append(normalized)
            if len(result) == MAX_GC_PATHS:
                break
    return result


def get_gc_cache_path(git_dir: str) -> Path:
    """Locate one cache per canonical repository, without creating directories."""
    xdg_cache_home = os.environ.get('XDG_CACHE_HOME')
    if xdg_cache_home and os.path.isabs(xdg_cache_home):
        cache_dir = Path(xdg_cache_home) / 'gwt'
    else:
        cache_dir = Path.home() / '.cache' / 'gwt'
    repo = os.path.realpath(git_dir)
    key = hashlib.sha256(os.fsencode(repo)).hexdigest()
    return cache_dir / 'gc' / f'{key}.json'


def load_gc_paths(git_dir: str) -> list[str]:
    """Load validated hints; missing or unusable caches require normal scanning."""
    cache_path = get_gc_cache_path(git_dir)
    try:
        with cache_path.open(encoding='utf-8') as stream:
            data = json.load(stream)
        if (
            not isinstance(data, dict)
            or data.get('version') != 1
            or data.get('repo') != os.path.realpath(git_dir)
            or not isinstance(data.get('paths'), list)
        ):
            raise ValueError('unrecognized cache format or repository')
        return _bounded_paths(data['paths'])
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        print(
            f'Warning: could not load GC path cache {cache_path}: {error}',
            file=sys.stderr,
        )
        return []


def remember_gc_path(paths: list[str], path: str) -> None:
    """Keep up to ten paths in order of most recent successful use."""
    normalized = normalize_gc_path(path)
    if normalized is None:
        return
    if normalized in paths:
        paths.remove(normalized)
    paths.insert(0, normalized)
    del paths[MAX_GC_PATHS:]


def save_gc_paths(git_dir: str, paths: list[str]) -> None:
    """Atomically replace optional cache data; failure never blocks GC."""
    cache_path = get_gc_cache_path(git_dir)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Stage on the same filesystem so replacement is atomic. Concurrent
        # writers can lose hints, but cannot change GC decisions.
        with tempfile.TemporaryDirectory(
            dir=cache_path.parent, prefix='.gc-'
        ) as temporary:
            staged = Path(temporary) / 'paths.json'
            with staged.open('w', encoding='utf-8') as stream:
                json.dump(
                    {
                        'version': 1,
                        'repo': os.path.realpath(git_dir),
                        'paths': _bounded_paths(paths),
                    },
                    stream,
                    indent=4,
                )
                stream.write('\n')
            os.replace(staged, cache_path)
    except OSError as error:
        print(
            f'Warning: could not save GC path cache {cache_path}: {error}',
            file=sys.stderr,
        )
