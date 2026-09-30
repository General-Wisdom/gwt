from unittest.mock import Mock

import pytest

from gwtlib import gc


@pytest.mark.parametrize(
    'age, merged, dirty, options, expected',
    [
        (27.5, True, False, {}, ['to_clean']),
        (28, True, False, {}, ['to_delete']),
        (0.5, True, False, {'merged_days': 1}, ['skip']),
        (1, True, False, {'merged_days': 1}, ['to_delete']),
        (0, True, False, {'merged_days': 0}, ['to_delete']),
        (28, True, False, {'merged_days': 40}, ['to_delete']),
        (0, True, False, {'delete_days': 0, 'merged_days': 1}, ['to_delete']),
        (2, True, True, {'merged_days': 1}, ['dirty']),
        (7, True, True, {'merged_days': 1}, ['to_clean', 'dirty']),
        (28, True, True, {'clean_days': 35}, ['to_clean', 'dirty']),
        (1, False, False, {'merged_days': 1}, ['skip']),
        (28, False, False, {'merged_days': 1}, ['to_clean', 'unmerged']),
    ],
)
def test_merged_age_uses_existing_eligibility(
    monkeypatch, age, merged, dirty, options, expected
):
    wt = gc.WorktreeInfo('worktree', 'feature', 0, age, dirty, merged)
    scan = Mock(return_value=[wt])
    monkeypatch.setattr(gc, 'get_worktree_info_list', scan)
    plan = gc.create_gc_plan('repo.git', **options)
    scan.assert_called_once_with('repo.git', include_main=False)
    for category in ('to_clean', 'to_delete', 'dirty', 'unmerged', 'skip'):
        assert getattr(plan, category) == ([wt] if category in expected else [])


@pytest.mark.parametrize('merged_days, expected', [(28, 28), (1, 1), (40, 28)])
def test_plan_displays_effective_merged_age(capsys, merged_days, expected):
    wt = gc.WorktreeInfo('worktree', 'feature', 0, 28, False, True)
    gc.print_plan(
        gc.GcPlan([], [wt], [], [], []),
        'repo.git',
        7,
        28,
        merged_days=merged_days,
    )
    assert f'{expected}d for merged branches' in capsys.readouterr().err
