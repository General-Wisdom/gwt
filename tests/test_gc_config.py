import sys
from unittest.mock import Mock

import pytest

from gwtlib import cli, gc

GIT_DIR = '/example/repo.git'


def _assert_thresholds(plan, clean_days, delete_days, merged_days=28):
    plan.assert_called_once()
    assert plan.call_args.args == (GIT_DIR,)
    assert plan.call_args.kwargs['clean_days'] == clean_days
    assert plan.call_args.kwargs['delete_days'] == delete_days
    assert plan.call_args.kwargs['merged_days'] == merged_days


@pytest.fixture
def set_config(monkeypatch):
    def set_settings(settings):
        monkeypatch.setattr(gc, 'load_config', lambda: settings)

    return set_settings


@pytest.fixture
def plan_mock(monkeypatch):
    plan = Mock(return_value=gc.GcPlan([], [], [], [], []))
    monkeypatch.setattr(gc, 'create_gc_plan', plan)
    monkeypatch.setattr(
        cli, 'get_git_dir_with_source', lambda **kw: (GIT_DIR, 'test', {})
    )
    return plan


@pytest.mark.parametrize(
    'settings, options, expected',
    [
        ({}, [], (7, 28)),
        ({'gc': {'clean_days': 10, 'delete_days': 35}}, [], (10, 35)),
        (
            {
                'gc': {'clean_days': 10, 'delete_days': 35},
                'repos': {GIT_DIR: {'gc': {'delete_days': 14}}},
            },
            [],
            (10, 14),
        ),
        ({'repos': {GIT_DIR: {'gc': {'clean_days': 0}}}}, [], (0, 28)),
        (
            {
                'gc': {'clean_days': 10, 'delete_days': 35},
                'repos': {GIT_DIR: {'gc': {'clean_days': 3, 'delete_days': 14}}},
            },
            ['--clean-days', '7', '--delete-days', '0'],
            (7, 0),
        ),
        (
            {'gc': {'clean_days': 10, 'delete_days': 35}},
            ['--delete-days', '14'],
            (10, 14),
        ),
        (
            {
                'gc': {'clean_days': 10},
                'repos': {'/another/repo.git': {'gc': {'clean_days': 1}}},
            },
            [],
            (10, 28),
        ),
        (
            {'gc': {'clean_days': 10}},
            ['--clean-days', '0'],
            (0, 28),
        ),
        (
            {
                'gc': {'clean_days': 10},
                'repos': {GIT_DIR: {'gc': {'clean_days': 2}}},
            },
            [],
            (2, 28),
        ),
        ({'gc': {'merged_days': 1}}, [], (7, 28, 1)),
        (
            {
                'gc': {'clean_days': 10, 'merged_days': 3},
                'repos': {GIT_DIR: {'gc': {'merged_days': 2}}},
            },
            [],
            (10, 28, 2),
        ),
        ({'repos': {GIT_DIR: {'gc': {'merged_days': 0}}}}, [], (7, 28, 0)),
        (
            {
                'gc': {'clean_days': 10, 'delete_days': 35, 'merged_days': 3},
                'repos': {GIT_DIR: {'gc': {'delete_days': 14, 'merged_days': 2}}},
            },
            ['--merged-days', '0'],
            (10, 14, 0),
        ),
        (
            {
                'gc': {'merged_days': 3},
                'repos': {'/another/repo.git': {'gc': {'merged_days': 0}}},
            },
            [],
            (7, 28, 3),
        ),
    ],
)
def test_cli_resolves_each_threshold_independently(
    set_config, plan_mock, monkeypatch, settings, options, expected
):
    set_config(settings)
    monkeypatch.setattr(sys, 'argv', ['gwt', 'gc', '--plan', *options])
    cli.main()
    _assert_thresholds(plan_mock, *expected)


def test_programmatic_gc_call_uses_config_when_arguments_are_omitted(
    set_config, plan_mock, monkeypatch
):
    set_config({'gc': {'clean_days': 10, 'delete_days': 35, 'merged_days': 3}})
    display = Mock()
    monkeypatch.setattr(gc, 'print_plan', display)
    gc.gc_worktrees(GIT_DIR, plan_only=True)
    _assert_thresholds(plan_mock, 10, 35, 3)
    display.assert_called_once_with(
        plan_mock.return_value,
        GIT_DIR,
        clean_days=10,
        delete_days=35,
        merged_days=3,
    )
