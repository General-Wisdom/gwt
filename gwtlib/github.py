# gwtlib/github.py
"""GitHub CLI integrations."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass
class PrInfo:
    """Result of a GitHub PR lookup for a branch.

    A successful lookup that found no PR has state=None and error=None.
    A failed lookup (gh missing, auth/network error) has error set.
    """

    state: Optional[str]  # 'OPEN', 'CLOSED', or 'MERGED'
    is_merged: bool
    head_ref_oid: Optional[str]  # PR head SHA (None if unknown)
    error: Optional[str]  # Lookup failure reason, None if the lookup succeeded


def get_pr_info(
    branch_name: str, cwd: Optional[str] = None, quiet: bool = False
) -> PrInfo:
    """Check the PR for a branch using GitHub CLI, reporting failures separately.

    Args:
        branch_name: The branch to check for PRs.
        cwd: Directory to run gh from (should be inside the git repo).
             If None, uses current directory.
        quiet: If True, suppress warnings on failure (for bulk lookups where the
             caller reports problems once instead of per-branch).

    Returns:
        PrInfo with state/merged info, or error set when the lookup itself
        failed (as opposed to finding no PR).
    """
    try:
        # Use gh pr view to get PR info for this branch
        result = subprocess.run(
            ["gh", "pr", "view", branch_name, "--json", "state,mergedAt,headRefOid"],
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
        )
        if result.returncode != 0:
            stderr = result.stderr.lower()
            # "no pull requests found" means no PR exists - not an error
            if "no pull requests found" in stderr:
                return PrInfo(None, False, None, None)
            # Other failures (not in a repo, no gh CLI, network error, etc.)
            # Log warning so user knows GitHub lookup failed
            if not quiet and result.stderr.strip():
                print(
                    f"Warning: GitHub PR lookup failed: {result.stderr.strip()}",
                    file=sys.stderr,
                )
            return PrInfo(None, False, None, result.stderr.strip() or "gh failed")

        data = json.loads(result.stdout)
        state = data.get("state", "UNKNOWN")
        merged_at = data.get("mergedAt")
        is_merged = merged_at is not None
        head_ref_oid = data.get("headRefOid")

        return PrInfo(state, is_merged, head_ref_oid, None)
    except FileNotFoundError:
        # gh CLI not installed
        if not quiet:
            print("Warning: gh CLI not found, skipping PR lookup", file=sys.stderr)
        return PrInfo(None, False, None, "gh CLI not found")
    except json.JSONDecodeError:
        if not quiet:
            print("Warning: Failed to parse gh CLI output", file=sys.stderr)
        return PrInfo(None, False, None, "failed to parse gh CLI output")


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
        or None if no PR exists for this branch (or the lookup failed).

    Note: Requires gh CLI and must be run from within a git repo with a GitHub remote,
    or cwd must point to such a directory.
    """
    info = get_pr_info(branch_name, cwd=cwd, quiet=quiet)
    if info.error or info.state is None:
        return None
    return (info.state, info.is_merged)
