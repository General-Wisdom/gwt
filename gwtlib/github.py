# gwtlib/github.py
"""GitHub CLI integrations."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MergedPr:
    number: int
    head_oid: str  # PR source tip, retained after merge even if its branch moves


def get_merged_prs(
    branches: list[str], main_branch: str, cwd: str
) -> dict[str, MergedPr]:
    """Fetch the latest merged PR to main for each branch in bulk.

    Query only the requested branches, rather than walking repository PR history.
    Up to 100 branches share one request; larger sets use bounded batches.
    Lookup failures leave those branches on the ordinary GC policy.
    """
    branches = list(dict.fromkeys(branches))
    merged = {}
    for offset in range(0, len(branches), 100):
        batch = branches[offset : offset + 100]
        fields = ' '.join(
            f'b{i}: pullRequests(first: 1, states: MERGED, '
            f'headRefName: {json.dumps(branch)}, '
            f'baseRefName: {json.dumps(main_branch)}, '
            'orderBy: {field: CREATED_AT, direction: DESC}) '
            '{nodes {number state headRefName baseRefName headRefOid}}'
            for i, branch in enumerate(batch)
        )
        query = (
            'query($owner: String!, $name: String!) '
            '{repository(owner: $owner, name: $name) {' + fields + '}}'
        )
        try:
            result = subprocess.run(
                [
                    'gh',
                    'api',
                    'graphql',
                    '-F',
                    'owner={owner}',
                    '-F',
                    'name={repo}',
                    '-f',
                    f'query={query}',
                ],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            data = json.loads(result.stdout)
            if not isinstance(data, dict):
                raise ValueError('Invalid GitHub lookup response')
            if data.get('errors'):
                raise ValueError('GitHub returned GraphQL errors')
            repository = data['data']['repository']
            batch_merged = {}
            for i, branch in enumerate(batch):
                nodes = repository[f'b{i}']['nodes']
                if not isinstance(nodes, list) or len(nodes) > 1:
                    raise ValueError('Invalid PR lookup response')
                if not nodes:
                    continue
                pr = nodes[0]
                oid = pr['headRefOid']
                if (
                    pr['state'] != 'MERGED'
                    or pr['headRefName'] != branch
                    or pr['baseRefName'] != main_branch
                    or type(pr['number']) is not int
                    or pr['number'] <= 0
                    or not isinstance(oid, str)
                    or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid)
                ):
                    raise ValueError('Invalid merged PR metadata')
                batch_merged[branch] = MergedPr(pr['number'], oid)
            merged.update(batch_merged)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            print(
                'Warning: bulk GitHub PR lookup failed; using ordinary GC thresholds '
                'for branches without confirmed PR metadata.',
                file=sys.stderr,
            )
            break
    return merged


def get_pr_state(
    branch_name: str, cwd: Optional[str] = None, quiet: bool = False
) -> Optional[tuple[str, bool]]:
    """Check the PR state for a branch using GitHub CLI.

    Args:
        branch_name: The branch to check for PRs.
        cwd: Directory to run gh from (should be inside the git repo).
             If None, uses current directory.
        quiet: If True, suppress warnings on failure (for bulk lookups where the
             caller reports problems once instead of per-branch).

    Returns:
        Tuple of (state, is_merged) where state is 'OPEN', 'CLOSED', or 'MERGED',
        or None if no PR exists for this branch.

    Note: Requires gh CLI and must be run from within a git repo with a GitHub remote,
    or cwd must point to such a directory.
    """
    try:
        # Use gh pr view to get PR info for this branch
        result = subprocess.run(
            ["gh", "pr", "view", branch_name, "--json", "state,mergedAt"],
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
        )
        if result.returncode != 0:
            stderr = result.stderr.lower()
            # "no pull requests found" means no PR exists - not an error
            if "no pull requests found" in stderr:
                return None
            # Other failures (not in a repo, no gh CLI, network error, etc.)
            # Log warning so user knows GitHub lookup failed
            if not quiet and result.stderr.strip():
                print(
                    f"Warning: GitHub PR lookup failed: {result.stderr.strip()}",
                    file=sys.stderr,
                )
            return None

        data = json.loads(result.stdout)
        state = data.get("state", "UNKNOWN")
        merged_at = data.get("mergedAt")
        is_merged = merged_at is not None

        return (state, is_merged)
    except FileNotFoundError:
        # gh CLI not installed
        if not quiet:
            print("Warning: gh CLI not found, skipping PR lookup", file=sys.stderr)
        return None
    except json.JSONDecodeError:
        if not quiet:
            print("Warning: Failed to parse gh CLI output", file=sys.stderr)
        return None
