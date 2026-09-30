import sys
from unittest.mock import Mock

import pytest
import tomli_w

from gwtlib import cli, gc

GIT_DIR = '/example/repo.git'


def _assert_thresholds(plan, clean_days, delete_days):
    plan.assert_called_once()
    assert plan.call_args.args == (GIT_DIR,)
    assert plan.call_args.kwargs['clean_days'] == clean_days
    assert plan.call_args.kwargs['delete_days'] == delete_days


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    config_home = tmp_path / 'xdg'
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config_home))
    path = config_home / 'gwt' / 'config.toml'
    path.parent.mkdir(parents=True)

    def write(settings):
        path.write_text(tomli_w.dumps(settings))

    return write


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
    ],
)
def test_cli_resolves_each_threshold_independently(
    write_config, plan_mock, monkeypatch, settings, options, expected
):
    write_config(settings)
    monkeypatch.setattr(sys, 'argv', ['gwt', 'gc', '--plan', *options])
    cli.main()
    _assert_thresholds(plan_mock, *expected)


def test_programmatic_gc_call_uses_config_when_arguments_are_omitted(
    write_config, plan_mock
):
    write_config({'gc': {'clean_days': 10, 'delete_days': 35}})
    gc.gc_worktrees(GIT_DIR, plan_only=True)
    _assert_thresholds(plan_mock, 10, 35)
