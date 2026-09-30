import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gwtlib import gc
from gwtlib.github import MergedPr

DAY = 86400
NOW = 100 * DAY
CUTOFF = NOW - 7 * DAY


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setattr(gc, 'get_main_branch_name', lambda *a: 'main')
    monkeypatch.setattr(gc, 'get_merged_prs', lambda *a, **kw: {})


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


def test_covered_pr_uses_one_day_and_preserves_normal_policy(
    planning_environment, monkeypatch, capsys
):
    _, _, _ = planning_environment
    lookup = _merged_branches(monkeypatch, ['recent', 'clean'])
    plan = gc.create_gc_plan('repo.git', merged_pr_days=1)
    assert [wt.branch for wt in plan.to_delete] == ['delete', 'clean', 'recent']
    assert plan.to_delete[-1].age_days == 1
    assert [wt.branch for wt in plan.to_clean] == ['unmerged', 'dirty', 'boundary']
    assert not plan.skip
    lookup.assert_called_once()
    gc.print_plan(plan, 'repo.git', 7, 28, merged_pr_days=1)
    output = capsys.readouterr().err
    assert 'merged PR #1' in output
    assert '28d normally, 1d for covered merged PRs' in output


def test_covered_pr_under_one_day_is_kept(planning_environment, monkeypatch):
    root, dirty, _ = planning_environment
    _file(root / 'recent' / 'src' / 'source.py', NOW - DAY / 2)
    _merged_branches(monkeypatch, ['recent'])
    plan = gc.create_gc_plan('repo.git', merged_pr_days=1)
    assert [wt.branch for wt in plan.skip] == ['recent']
    assert 'recent' not in [wt.branch for wt in plan.to_delete]


@pytest.mark.parametrize('merged_pr_days', [None, 0, 1, 3, 40])
def test_covered_pr_uses_configured_age_and_normal_age_cap(
    planning_environment, monkeypatch, capsys, merged_pr_days
):
    root, _, _ = planning_environment
    options = {} if merged_pr_days is None else {'merged_pr_days': merged_pr_days}
    threshold = 28 if merged_pr_days is None else min(28, merged_pr_days)
    _merged_branches(monkeypatch, ['recent'])
    _file(root / 'recent' / 'src' / 'source.py', NOW - threshold * DAY)
    plan = gc.create_gc_plan('repo.git', **options)
    assert 'recent' in [wt.branch for wt in plan.to_delete]
    gc.print_plan(plan, 'repo.git', 7, 28, **options)
    assert f'{threshold}d for covered merged PRs' in capsys.readouterr().err

    _file(root / 'recent' / 'src' / 'source.py', NOW - (threshold - 0.5) * DAY)
    plan = gc.create_gc_plan('repo.git', **options)
    assert 'recent' not in [wt.branch for wt in plan.to_delete]


def test_new_commits_restore_normal_threshold(planning_environment, monkeypatch):
    root, _, _ = planning_environment
    _file(root / 'recent' / 'src' / 'source.py', NOW - 2 * DAY)
    _merged_branches(monkeypatch, ['recent'])
    monkeypatch.setattr(gc, '_get_covered_branch_head', lambda *a: None)
    plan = gc.create_gc_plan('repo.git', merged_pr_days=1)
    assert [wt.branch for wt in plan.skip] == ['recent']
    assert plan.skip[0].merged_pr is None


def test_dirty_merged_pr_does_not_lower_cleaning_threshold(
    planning_environment, monkeypatch
):
    root, _, _ = planning_environment
    _file(root / 'dirty' / 'src' / 'source.py', NOW - 2 * DAY)
    _merged_branches(monkeypatch, ['dirty'])
    plan = gc.create_gc_plan('repo.git', merged_pr_days=1)
    assert [wt.branch for wt in plan.dirty] == ['dirty']
    assert 'dirty' not in [wt.branch for wt in plan.to_clean]
    assert 'dirty' not in [wt.branch for wt in plan.to_delete]


def test_explicit_shorter_deletion_threshold_still_applies(
    planning_environment, monkeypatch
):
    root, _, _ = planning_environment
    _file(root / 'recent' / 'src' / 'source.py', NOW - DAY / 2)
    _merged_branches(monkeypatch, ['recent'])
    plan = gc.create_gc_plan('repo.git', delete_days=0)
    assert 'recent' in [wt.branch for wt in plan.to_delete]


def test_unknown_main_leaves_normal_policy(planning_environment, monkeypatch):
    monkeypatch.setattr(gc, 'get_main_branch_name', lambda *a: None)
    lookup = Mock(side_effect=AssertionError('cannot look up PR destination'))
    monkeypatch.setattr(gc, 'get_merged_prs', lookup)
    plan = gc.create_gc_plan('repo.git')
    assert [wt.branch for wt in plan.skip] == ['recent']
    lookup.assert_not_called()


@pytest.mark.parametrize(
    'main_rc, pr_rc, expected',
    [
        (0, 128, 'b' * 40),  # Main alone proves coverage; old PR object is unnecessary.
        (1, 0, 'b' * 40),  # PR history covers squash/rebase-merged source commits.
        (1, 1, None),  # Local tip adds work outside both histories.
        (1, 128, None),  # Missing PR object cannot prove coverage.
    ],
)
def test_commit_coverage_accepts_pr_history_or_main(
    monkeypatch, main_rc, pr_rc, expected
):
    run = Mock(return_value=SimpleNamespace(stdout='b' * 40 + '\n'))
    monkeypatch.setattr(gc, 'run_git_quiet', run)
    ancestors = Mock(side_effect=[main_rc, pr_rc])
    monkeypatch.setattr(gc, 'run_git_rc', ancestors)
    assert (
        gc._get_covered_branch_head(
            'feature', MergedPr(1, 'a' * 40), 'main', 'repo.git'
        )
        == expected
    )
    assert ancestors.call_args_list[0].args == (
        ['merge-base', '--is-ancestor', 'b' * 40, 'refs/heads/main'],
        'repo.git',
    )
    if main_rc == 0:
        ancestors.assert_called_once()
    else:
        assert ancestors.call_args_list[1].args == (
            ['merge-base', '--is-ancestor', 'b' * 40, 'a' * 40],
            'repo.git',
        )


def test_missing_local_branch_cannot_prove_coverage(monkeypatch):
    error = subprocess.CalledProcessError(128, ['git'])
    run = Mock(side_effect=error)
    monkeypatch.setattr(gc, 'run_git_quiet', run)
    assert (
        gc._get_covered_branch_head(
            'feature', MergedPr(1, 'a' * 40), 'main', 'repo.git'
        )
        is None
    )


@pytest.mark.parametrize('current_head', [None, 'c' * 40, 'b' * 40])
def test_execution_rechecks_merged_pr_coverage_and_local_tip(monkeypatch, current_head):
    wt = gc.WorktreeInfo(
        'worktree',
        'feature',
        NOW - 2 * DAY,
        2,
        False,
        True,
        merged_pr=MergedPr(1, 'a' * 40),
        head_oid='b' * 40,
    )
    monkeypatch.setattr(gc, 'is_worktree_dirty', lambda *a: False)
    monkeypatch.setattr(
        gc,
        'run_git_in_worktree',
        lambda *a: SimpleNamespace(stdout='refs/heads/feature\n'),
    )
    monkeypatch.setattr(gc, '_get_covered_branch_head', lambda *a: current_head)
    run = Mock()
    monkeypatch.setattr(gc, 'run_git_command', run)
    gc.execute_gc_plan(gc.GcPlan([], [wt], [], [], []), 'repo.git')
    if current_head == wt.head_oid:
        assert run.call_args_list[0].args[0] == ['worktree', 'remove', 'worktree']
        assert run.call_args_list[1].args[0] == ['branch', '-d', 'feature']
    else:
        run.assert_not_called()


def test_execution_retains_worktree_that_switched_branches(monkeypatch):
    wt = gc.WorktreeInfo(
        'worktree',
        'feature',
        NOW - 2 * DAY,
        2,
        False,
        True,
        merged_pr=MergedPr(1, 'a' * 40),
        head_oid='b' * 40,
    )
    monkeypatch.setattr(gc, 'is_worktree_dirty', lambda *a: False)
    monkeypatch.setattr(
        gc,
        'run_git_in_worktree',
        lambda *a: SimpleNamespace(stdout='refs/heads/new-work\n'),
    )
    run = Mock()
    monkeypatch.setattr(gc, 'run_git_command', run)
    gc.execute_gc_plan(gc.GcPlan([], [wt], [], [], []), 'repo.git')
    run.assert_not_called()
