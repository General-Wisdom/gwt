import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from gwtlib import cache, gc

DAY = 86400
NOW = 100 * DAY
CUTOFF = NOW - 7 * DAY


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setattr(gc, 'get_main_branch_name', lambda *a: None)


def _file(path, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('content')
    os.utime(path, (mtime, mtime))
    return path


def test_exact_scan_ignores_git_and_directory_activity(tmp_path):
    _file(tmp_path / 'src' / 'old.py', NOW - 40 * DAY)
    _file(tmp_path / 'src' / 'newer.py', NOW - 10 * DAY)
    _file(tmp_path / '.git' / 'index', NOW)
    _file(tmp_path / 'src' / '.git' / 'index', NOW)
    os.utime(tmp_path, (NOW, NOW))
    assert gc.get_worktree_mtime(str(tmp_path)) == NOW - 10 * DAY


def test_recent_hint_avoids_walk(tmp_path, monkeypatch):
    _file(tmp_path / 'src' / 'active.py', NOW)
    walk = Mock(side_effect=AssertionError('recent hint should avoid walking'))
    monkeypatch.setattr(gc.os, 'walk', walk)
    assert gc._scan_worktree_mtime(str(tmp_path), CUTOFF, ['src/active.py']) is None
    walk.assert_not_called()


@pytest.mark.parametrize('mtime', [CUTOFF - 1, CUTOFF, CUTOFF + 1])
def test_threshold_boundary(tmp_path, mtime):
    _file(tmp_path / 'source.py', mtime)
    expected = None if mtime > CUTOFF else mtime
    assert gc._scan_worktree_mtime(str(tmp_path), CUTOFF) == expected
    assert gc._scan_worktree_mtime(str(tmp_path), CUTOFF, ['source.py']) == expected


def test_walk_stops_at_recent_file(tmp_path, monkeypatch):
    recent = _file(tmp_path / 'recent.py', NOW)
    monkeypatch.setattr(
        gc.os, 'walk', lambda root: iter([(root, [], [recent.name, 'must-not-stat'])])
    )
    getmtime = Mock(wraps=os.path.getmtime)
    monkeypatch.setattr(gc.os.path, 'getmtime', getmtime)
    learned = Mock()
    assert gc._scan_worktree_mtime(str(tmp_path), CUTOFF, on_recent=learned) is None
    assert getmtime.call_args_list == [((str(recent),), {})]
    learned.assert_called_once_with('recent.py')


def test_missing_and_old_hints_fall_back_to_exact_scan(tmp_path):
    _file(tmp_path / 'old.py', NOW - 40 * DAY)
    _file(tmp_path / 'src' / 'newest.py', NOW - 10 * DAY)
    assert (
        gc._scan_worktree_mtime(str(tmp_path), CUTOFF, ['missing.py', 'old.py'])
        == NOW - 10 * DAY
    )


@pytest.mark.parametrize(
    'hint',
    [
        '.',
        'src',
        '.git',
        '.git/index',
        'link/new.py',
        '../external/new.py',
        'missing.py',
        'src/\0invalid.py',
        42,
    ],
)
def test_hints_cannot_use_excluded_activity(tmp_path, hint):
    root = tmp_path / 'worktree'
    root.mkdir()
    _file(root / 'src' / 'old.py', NOW - 40 * DAY)
    _file(root / '.git' / 'index', NOW)
    external = _file(tmp_path / 'external' / 'new.py', NOW)
    (root / 'link').symlink_to(external.parent, target_is_directory=True)
    assert gc._scan_worktree_mtime(str(root), CUTOFF, [hint]) == NOW - 40 * DAY
    assert gc._scan_worktree_mtime(str(root), CUTOFF, [str(external)]) == NOW - 40 * DAY


def test_file_symlink_matches_existing_scan_semantics(tmp_path):
    root = tmp_path / 'worktree'
    root.mkdir()
    external = _file(tmp_path / 'external.py', NOW)
    (root / 'source.py').symlink_to(external)
    assert gc.get_worktree_mtime(str(root)) == NOW
    assert gc._scan_worktree_mtime(str(root), CUTOFF, ['source.py']) is None


def test_empty_tree_fallbacks(tmp_path):
    git_file = _file(tmp_path / '.git', NOW - 10 * DAY)
    assert gc.get_worktree_mtime(str(tmp_path)) == NOW - 10 * DAY
    git_file.unlink()
    os.utime(tmp_path, (NOW - 20 * DAY, NOW - 20 * DAY))
    assert gc.get_worktree_mtime(str(tmp_path)) == NOW - 20 * DAY
    assert gc.get_worktree_mtime(str(tmp_path / 'missing')) == 0


@pytest.fixture
def planning_environment(tmp_path, monkeypatch):
    ages = {
        'recent': 1,
        'boundary': 7,
        'clean': 10,
        'delete': 28,
        'dirty': 40,
        'unmerged': 45,
    }
    worktrees = []
    for branch, age in ages.items():
        root = tmp_path / branch
        _file(root / 'src' / 'source.py', NOW - age * DAY)
        worktrees.append({'path': str(root), 'branch': branch})
    monkeypatch.setattr(gc, 'get_worktree_list', lambda *a, **kw: worktrees)
    monkeypatch.setattr(gc.time, 'time', lambda: NOW)
    monkeypatch.setattr(gc, 'HAS_TQDM', False)
    dirty = Mock(side_effect=lambda path: path.endswith('/dirty'))
    merged = Mock(side_effect=lambda branch, git_dir: branch != 'unmerged')
    monkeypatch.setattr(gc, 'is_worktree_dirty', dirty)
    monkeypatch.setattr(gc, '_is_branch_merged_to_main', merged)
    return tmp_path, dirty, merged


def test_gc_preserves_action_ages_and_skips_recent_git_checks(planning_environment):
    root, dirty, merged = planning_environment
    plan = gc.create_gc_plan('repo.git')
    assert [(wt.branch, wt.age_days) for wt in plan.to_clean] == [
        ('unmerged', 45),
        ('dirty', 40),
        ('clean', 10),
        ('boundary', 7),
    ]
    assert [(wt.branch, wt.age_days) for wt in plan.to_delete] == [('delete', 28)]
    assert [wt.branch for wt in plan.dirty] == ['dirty']
    assert [wt.branch for wt in plan.unmerged] == ['unmerged']
    assert [wt.branch for wt in plan.skip] == ['recent']
    recent = plan.skip[0]
    assert (recent.mtime, recent.age_days, recent.is_dirty, recent.is_merged) == (
        None,
        None,
        None,
        None,
    )
    assert str(root / 'recent') not in [call.args[0] for call in dirty.call_args_list]
    assert 'recent' not in [call.args[0] for call in merged.call_args_list]


def test_exact_info_api_still_checks_recent_worktrees(planning_environment):
    _, dirty, merged = planning_environment
    info = gc.get_worktree_info_list('repo.git')
    assert info[-1].branch == 'recent'
    assert info[-1].age_days == 1
    assert info[-1].mtime == NOW - DAY
    assert dirty.call_count == merged.call_count == 6
    assert not cache.get_gc_cache_path('repo.git').exists()


def test_reversed_thresholds_do_not_skip_delete_candidates(planning_environment):
    plan = gc.create_gc_plan('repo.git', clean_days=28, delete_days=7)
    assert [wt.branch for wt in plan.to_delete] == ['delete', 'clean', 'boundary']
    assert [wt.branch for wt in plan.skip] == ['recent']


def test_corrupt_cache_falls_back_to_walk(planning_environment):
    cache_path = cache.get_gc_cache_path('repo.git')
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text('invalid json')
    plan = gc.create_gc_plan('repo.git')
    assert [wt.branch for wt in plan.skip] == ['recent']
    assert [wt.branch for wt in plan.to_delete] == ['delete']


def test_plan_output_keeps_exact_action_ages_and_recent_count(
    planning_environment, monkeypatch, capsys
):
    monkeypatch.setattr(gc, '_path_matches_branch', lambda *a: True)
    plan = gc.create_gc_plan('repo.git')
    gc.print_plan(plan, 'repo.git', clean_days=7, delete_days=28)
    output = capsys.readouterr().err
    assert 'boundary  (7d)' in output
    assert 'clean  (10d)' in output
    assert 'delete  (28d)' in output
    assert 'Keeping 1 recent worktree(s)' in output
    assert 'recent  (' not in output
    assert 'None' not in output


def test_recent_empty_worktree_skips_git_checks(tmp_path, monkeypatch):
    _file(tmp_path / '.git', NOW)
    monkeypatch.setattr(
        gc,
        'get_worktree_list',
        lambda *a, **kw: [{'path': str(tmp_path), 'branch': 'empty'}],
    )
    monkeypatch.setattr(gc.time, 'time', lambda: NOW)
    monkeypatch.setattr(gc, 'HAS_TQDM', False)
    dirty = Mock(side_effect=AssertionError('recent empty tree needs no status'))
    monkeypatch.setattr(gc, 'is_worktree_dirty', dirty)
    info = gc.get_worktree_info_list('repo.git', recent_days=7)
    assert info[0].age_days is None
    dirty.assert_not_called()
    assert not cache.get_gc_cache_path('repo.git').exists()


def _recent_worktrees(monkeypatch, roots):
    monkeypatch.setattr(
        gc,
        'get_worktree_list',
        lambda *a, **kw: [{'path': str(root), 'branch': root.name} for root in roots],
    )
    monkeypatch.setattr(gc.time, 'time', lambda: NOW)
    monkeypatch.setattr(gc, 'HAS_TQDM', False)
    monkeypatch.setattr(gc, 'is_worktree_dirty', lambda *a: False)
    monkeypatch.setattr(gc, '_is_branch_merged_to_main', lambda *a: True)


def test_learned_paths_are_shared_between_worktrees(tmp_path, monkeypatch):
    roots = [tmp_path / 'first', tmp_path / 'second']
    for root in roots:
        _file(root / 'src' / 'active.py', NOW)
    _recent_worktrees(monkeypatch, roots)
    walk = Mock(wraps=os.walk)
    save = Mock(wraps=gc.save_gc_paths)
    monkeypatch.setattr(gc.os, 'walk', walk)
    monkeypatch.setattr(gc, 'save_gc_paths', save)
    info = gc.get_worktree_info_list('repo.git', recent_days=7)
    assert all(wt.age_days is None for wt in info)
    assert [call.args[0] for call in walk.call_args_list] == [str(roots[0])]
    assert cache.load_gc_paths('repo.git') == ['src/active.py']
    save.assert_called_once_with('repo.git', ['src/active.py'])


def test_stale_or_missing_hint_in_one_tree_remains_useful_in_another(
    tmp_path, monkeypatch
):
    roots = [tmp_path / 'stale', tmp_path / 'missing-hint', tmp_path / 'active']
    _file(roots[0] / 'src' / 'source.py', NOW - 40 * DAY)
    _file(roots[1] / 'other.py', NOW - 30 * DAY)
    _file(roots[2] / 'src' / 'source.py', NOW)
    cache.save_gc_paths('repo.git', ['src/source.py'])
    _recent_worktrees(monkeypatch, roots)
    save = Mock(side_effect=AssertionError('unchanged cache should not be written'))
    walk = Mock(wraps=os.walk)
    monkeypatch.setattr(gc, 'save_gc_paths', save)
    monkeypatch.setattr(gc.os, 'walk', walk)
    info = gc.get_worktree_info_list('repo.git', recent_days=7)
    assert [wt.age_days for wt in info] == [40, 30, None]
    assert [call.args[0] for call in walk.call_args_list] == [
        str(root) for root in roots[:2]
    ]
    save.assert_not_called()
    assert cache.load_gc_paths('repo.git') == ['src/source.py']


def test_gc_learns_up_to_ten_distinct_recent_paths(tmp_path, monkeypatch):
    roots = [tmp_path / f'worktree-{i}' for i in range(12)]
    for i, root in enumerate(roots):
        _file(root / 'src' / f'file-{i}.py', NOW)
    _recent_worktrees(monkeypatch, roots)
    gc.get_worktree_info_list('repo.git', recent_days=7)
    assert cache.load_gc_paths('repo.git') == [
        f'src/file-{i}.py' for i in range(11, 1, -1)
    ]


def test_cache_survives_new_process_without_walking_again(tmp_path):
    root = tmp_path / 'worktree'
    _file(root / 'src' / 'active.py', NOW)
    script = '''
import sys
from gwtlib import gc
gc.get_worktree_list = lambda *a, **kw: [{'path': sys.argv[1], 'branch': 'feature'}]
gc.time.time = lambda: 8640000
gc.HAS_TQDM = False
gc.is_worktree_dirty = lambda *a: False
gc._is_branch_merged_to_main = lambda *a: True
if sys.argv[2] == 'cached':
    def unexpected_walk(*args, **kwargs):
        raise AssertionError('new process should use the learned cache')
    gc.os.walk = unexpected_walk
info = gc.get_worktree_info_list(sys.argv[3], recent_days=7)
assert info[0].age_days is None
'''
    for mode in ['learn', 'cached']:
        result = subprocess.run(
            [sys.executable, '-c', script, str(root), mode, str(tmp_path / 'repo.git')],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
