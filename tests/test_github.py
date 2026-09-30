import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gwtlib import github

OID = 'a' * 40


def _pr(branch='feature', **changes):
    return {
        'number': 42,
        'state': 'MERGED',
        'headRefName': branch,
        'baseRefName': 'main',
        'headRefOid': OID,
        **changes,
    }


def _response(nodes):
    repository = {f'b{i}': {'nodes': value} for i, value in enumerate(nodes)}
    return SimpleNamespace(stdout=json.dumps({'data': {'repository': repository}}))


def test_bulk_lookup_fetches_only_requested_branches_and_destination(monkeypatch):
    run = Mock(return_value=_response([[_pr('feature/one')], []]))
    monkeypatch.setattr(github.subprocess, 'run', run)
    result = github.get_merged_prs(
        ['feature/one', 'other', 'feature/one'], 'main', 'repo'
    )
    assert result == {'feature/one': github.MergedPr(42, OID)}
    run.assert_called_once()
    args, kwargs = run.call_args
    query = args[0][-1]
    assert 'headRefName: "feature/one"' in query
    assert 'headRefName: "other"' in query
    assert query.count('pullRequests(') == 2
    assert 'baseRefName: "main"' in query
    assert 'states: MERGED' in query
    assert 'first: 1' in query
    assert kwargs['cwd'] == 'repo'
    assert kwargs['timeout'] == 30


def test_bulk_lookup_bounds_large_requests(monkeypatch):
    branches = [f'branch-{i}' for i in range(101)]
    run = Mock(side_effect=[_response([[]] * 100), _response([[_pr(branches[-1])]])])
    monkeypatch.setattr(github.subprocess, 'run', run)
    assert github.get_merged_prs(branches, 'main', 'repo') == {
        branches[-1]: github.MergedPr(42, OID)
    }
    assert run.call_count == 2


def test_empty_branch_list_needs_no_lookup(monkeypatch):
    run = Mock()
    monkeypatch.setattr(github.subprocess, 'run', run)
    assert github.get_merged_prs([], 'main', 'repo') == {}
    run.assert_not_called()


@pytest.mark.parametrize(
    'changes',
    [
        {'state': 'CLOSED'},
        {'headRefName': 'different'},
        {'baseRefName': 'release'},
        {'headRefOid': '--all'},
        {'headRefOid': None},
        {'number': True},
        {'number': -1},
    ],
)
def test_invalid_metadata_cannot_authorize_early_removal(monkeypatch, capsys, changes):
    run = Mock(return_value=_response([[_pr()], [_pr('other', **changes)]]))
    monkeypatch.setattr(github.subprocess, 'run', run)
    assert github.get_merged_prs(['feature', 'other'], 'main', 'repo') == {}
    assert 'ordinary GC thresholds' in capsys.readouterr().err


@pytest.mark.parametrize(
    'response',
    [
        '{',
        'null',
        '[]',
        '{"data": {"repository": null}}',
        '{"errors": [{"message": "denied"}], "data": null}',
        '{"data": {"repository": {"b0": {"nodes": null}}}}',
    ],
)
def test_invalid_response_uses_normal_policy(monkeypatch, capsys, response):
    monkeypatch.setattr(
        github.subprocess, 'run', Mock(return_value=SimpleNamespace(stdout=response))
    )
    assert github.get_merged_prs(['feature'], 'main', 'repo') == {}
    assert 'lookup failed' in capsys.readouterr().err


@pytest.mark.parametrize(
    'error',
    [
        FileNotFoundError(),
        subprocess.CalledProcessError(1, ['gh']),
        subprocess.TimeoutExpired(['gh'], 30),
    ],
)
def test_lookup_failure_warns_once_without_retry(monkeypatch, capsys, error):
    run = Mock(side_effect=error)
    monkeypatch.setattr(github.subprocess, 'run', run)
    assert github.get_merged_prs(['feature', 'other'], 'main', 'repo') == {}
    run.assert_called_once()
    assert capsys.readouterr().err.count('Warning:') == 1


def test_failed_later_batch_preserves_confirmed_first_batch(monkeypatch):
    run = Mock(
        side_effect=[
            _response([[_pr('branch-0')]] + [[]] * 99),
            FileNotFoundError(),
        ]
    )
    monkeypatch.setattr(github.subprocess, 'run', run)
    assert github.get_merged_prs(
        [f'branch-{i}' for i in range(101)], 'main', 'repo'
    ) == {'branch-0': github.MergedPr(42, OID)}
