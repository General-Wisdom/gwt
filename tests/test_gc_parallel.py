import os
from concurrent.futures import Future
from unittest.mock import Mock

import pytest

from gwtlib import cache, gc
from gwtlib.github import MergedPr

DAY = 86400
NOW = 100 * DAY
CUTOFF = NOW - 7 * DAY


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setattr(gc, 'get_main_branch_name', lambda *a: 'main')
    monkeypatch.setattr(gc, 'get_merged_prs', lambda *a, **kw: {})
    monkeypatch.setattr(gc, 'DEFAULT_GC_WORKERS', 1)


def _file(path, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('content')
    os.utime(path, (mtime, mtime))
    return path


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


def _merged_branches(monkeypatch, branches):
    prs = {branch: MergedPr(i + 1, 'a' * 40) for i, branch in enumerate(branches)}
    lookup = Mock(return_value=prs)
    monkeypatch.setattr(gc, 'get_merged_prs', lookup)
    monkeypatch.setattr(gc, '_get_covered_branch_head', lambda *a: 'b' * 40)
    return lookup


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


def test_covered_pr_still_short_circuits_under_one_day(
    planning_environment, monkeypatch
):
    root, dirty, _ = planning_environment
    _file(root / 'recent' / 'src' / 'source.py', NOW - DAY / 2)
    _merged_branches(monkeypatch, ['recent'])
    plan = gc.create_gc_plan('repo.git')
    assert [wt.branch for wt in plan.skip] == ['recent']
    assert plan.skip[0].age_days is None
    assert str(root / 'recent') not in [call.args[0] for call in dirty.call_args_list]


def test_spawned_scans_match_serial_results_and_include_build_activity(
    tmp_path, monkeypatch
):
    roots = [tmp_path / name for name in ['old-first', 'active-build', 'old-second']]
    for root in roots:
        _file(root / 'source.py', NOW - 40 * DAY)
    _file(roots[1] / 'node_modules' / 'build-output', NOW)
    _recent_worktrees(monkeypatch, roots)
    serial = gc.get_worktree_info_list('repo.git', recent_days=7, workers=1)
    parallel = gc.get_worktree_info_list('repo.git', recent_days=7, workers=2)
    assert parallel == serial
    assert [wt.branch for wt in parallel] == ['old-first', 'old-second', 'active-build']
    assert parallel[-1].age_days is None
    assert cache.load_gc_paths('repo.git') == ['node_modules/build-output']


class InlinePool:
    """Complete submitted jobs inline while exercising the parent coordinator."""

    def __init__(self, **kwargs):
        self.options = kwargs
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def submit(self, operation, *args):
        self.requests.append(args)
        future = Future()
        try:
            future.set_result(operation(*args))
        except Exception as error:
            future.set_exception(error)
        return future


def test_parallel_coordinator_bounds_jobs_and_shares_new_hints(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ['learn', 'stale', 'reuse']]
    for index, root in enumerate(roots):
        _file(root / 'src' / 'active.py', NOW if index != 1 else NOW - 40 * DAY)
    _recent_worktrees(monkeypatch, roots)
    pool = InlinePool(max_workers=2)
    monkeypatch.setattr(gc, 'ProcessPoolExecutor', lambda **kwargs: pool)
    windows = []

    def complete_first(pending, **kwargs):
        windows.append(len(pending))
        return {next(iter(pending))}, set()

    monkeypatch.setattr(gc, 'wait', complete_first)
    save = Mock(wraps=gc.save_gc_paths)
    monkeypatch.setattr(gc, 'save_gc_paths', save)
    info = gc.get_worktree_info_list('repo.git', recent_days=7, workers=2)
    assert len(info) == 3
    assert pool.requests[0][2] == pool.requests[1][2] == ()
    assert pool.requests[2][2] == ('src/active.py',)
    assert max(windows) == 2
    save.assert_called_once_with('repo.git', ['src/active.py'])


def test_parallel_completion_order_does_not_change_equal_age_order(
    tmp_path, monkeypatch
):
    roots = [tmp_path / name for name in ['first', 'second', 'third']]
    for root in roots:
        _file(root / 'source.py', NOW - 40 * DAY)
    _recent_worktrees(monkeypatch, roots)
    monkeypatch.setattr(gc, 'ProcessPoolExecutor', InlinePool)
    monkeypatch.setattr(
        gc, 'wait', lambda pending, **kwargs: ({list(pending)[-1]}, set())
    )
    info = gc.get_worktree_info_list('repo.git', recent_days=7, workers=2)
    assert [wt.branch for wt in info] == ['first', 'second', 'third']


def test_worker_failure_propagates_without_saving_partial_cache(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ['first', 'second']]
    for root in roots:
        _file(root / 'source.py', NOW)
    _recent_worktrees(monkeypatch, roots)
    monkeypatch.setattr(gc, 'ProcessPoolExecutor', InlinePool)
    monkeypatch.setattr(
        gc, '_scan_worktree_task', Mock(side_effect=RuntimeError('failed scan'))
    )
    save = Mock()
    monkeypatch.setattr(gc, 'save_gc_paths', save)
    with pytest.raises(RuntimeError, match='failed scan'):
        gc.get_worktree_info_list('repo.git', recent_days=7, workers=2)
    save.assert_not_called()


@pytest.mark.parametrize('task_count', [0, 1])
def test_empty_and_single_scans_need_no_process_pool(tmp_path, monkeypatch, task_count):
    _file(tmp_path / 'source.py', NOW - 40 * DAY)
    executor = Mock(side_effect=AssertionError('no pool needed'))
    monkeypatch.setattr(gc, 'ProcessPoolExecutor', executor)
    tasks = [(str(tmp_path), CUTOFF)] * task_count
    assert list(gc._scan_worktree_batch(tasks, [], 8)) == (
        [(0, NOW - 40 * DAY)] if task_count else []
    )
    executor.assert_not_called()


@pytest.mark.parametrize('workers', [0, -1])
def test_invalid_worker_count_fails_before_repo_lookup(monkeypatch, workers):
    lookup = Mock(side_effect=AssertionError('invalid input must fail first'))
    monkeypatch.setattr(gc, 'get_worktree_list', lookup)
    with pytest.raises(ValueError, match='at least 1'):
        gc.get_worktree_info_list('repo.git', workers=workers)
    lookup.assert_not_called()
