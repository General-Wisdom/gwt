import json
import os
from unittest.mock import Mock

import pytest

from gwtlib import cache


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'xdg'))


def test_cache_location_and_repository_isolation(tmp_path):
    first = str(tmp_path / 'first.git')
    second = str(tmp_path / 'second.git')
    path = cache.get_gc_cache_path(first)
    assert path.parent == tmp_path / 'xdg' / 'gwt' / 'gc'
    assert not path.parent.exists()
    cache.save_gc_paths(first, ['src/active.py'])
    assert cache.load_gc_paths(first) == ['src/active.py']
    assert cache.load_gc_paths(second) == []
    assert cache.get_gc_cache_path(first) != cache.get_gc_cache_path(second)


@pytest.mark.parametrize('xdg_value', [None, '', 'relative/cache'])
def test_cache_location_falls_back_to_user_cache(tmp_path, monkeypatch, xdg_value):
    if xdg_value is None:
        monkeypatch.delenv('XDG_CACHE_HOME')
    else:
        monkeypatch.setenv('XDG_CACHE_HOME', xdg_value)
    monkeypatch.setattr(cache.Path, 'home', lambda: tmp_path)
    assert (
        cache.get_gc_cache_path('repo.git').parent == tmp_path / '.cache' / 'gwt' / 'gc'
    )


def test_canonical_repository_aliases_share_cache(tmp_path):
    repo = tmp_path / 'repo.git'
    repo.mkdir()
    alias = tmp_path / 'alias.git'
    alias.symlink_to(repo, target_is_directory=True)
    cache.save_gc_paths(str(repo), ['source.py'])
    assert cache.get_gc_cache_path(str(repo)) == cache.get_gc_cache_path(str(alias))
    assert cache.load_gc_paths(str(alias)) == ['source.py']


def test_cache_validates_deduplicates_and_caps_loaded_paths():
    path = cache.get_gc_cache_path('repo.git')
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                'version': 1,
                'repo': os.path.realpath('repo.git'),
                'paths': [
                    None,
                    42,
                    '',
                    '.',
                    '.git/index',
                    '../external.py',
                    '/absolute.py',
                    '\0bad',
                    'src/./source.py',
                    'src/source.py',
                    *[f'file-{i}' for i in range(15)],
                ],
            }
        )
    )
    assert cache.load_gc_paths('repo.git') == [
        'src/source.py',
        *[f'file-{i}' for i in range(9)],
    ]


@pytest.mark.parametrize(
    'data',
    [
        'invalid json',
        '[]',
        '{"version": 2}',
        '{"version": 1, "paths": []}',
        '{"version": 1, "paths": "file.py"}',
    ],
)
def test_unusable_cache_warns_and_returns_no_hints(data, capsys):
    path = cache.get_gc_cache_path('repo.git')
    path.parent.mkdir(parents=True)
    path.write_text(data)
    assert cache.load_gc_paths('repo.git') == []
    assert 'Warning: could not load GC path cache' in capsys.readouterr().err


def test_missing_cache_is_normal(capsys):
    assert cache.load_gc_paths('repo.git') == []
    assert capsys.readouterr().err == ''


def test_mru_promotes_deduplicates_and_evicts():
    paths = []
    for i in range(12):
        cache.remember_gc_path(paths, f'src/file-{i}.py')
    assert paths == [f'src/file-{i}.py' for i in range(11, 1, -1)]
    cache.remember_gc_path(paths, 'src/./file-5.py')
    assert paths[0] == 'src/file-5.py'
    assert len(paths) == len(set(paths)) == 10
    cache.remember_gc_path(paths, '.git/index')
    assert len(paths) == 10
    assert '.git/index' not in paths


def test_failed_atomic_write_preserves_old_cache_and_cleans_temporary_files(
    monkeypatch, capsys
):
    cache.save_gc_paths('repo.git', ['original.py'])
    path = cache.get_gc_cache_path('repo.git')
    original = path.read_bytes()
    replace = Mock(side_effect=OSError('simulated replacement failure'))
    monkeypatch.setattr(cache.os, 'replace', replace)
    cache.save_gc_paths('repo.git', ['new.py'])
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]
    assert 'simulated replacement failure' in capsys.readouterr().err


def test_unavailable_storage_does_not_raise(tmp_path, monkeypatch, capsys):
    unavailable = tmp_path / 'not-a-directory'
    unavailable.write_text('file')
    monkeypatch.setenv('XDG_CACHE_HOME', str(unavailable))
    assert cache.load_gc_paths('repo.git') == []
    cache.save_gc_paths('repo.git', ['new.py'])
    output = capsys.readouterr().err
    assert 'could not load GC path cache' in output
    assert 'could not save GC path cache' in output
