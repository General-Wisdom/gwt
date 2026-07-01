# gwtlib/github.py
"""GitHub CLI integrations."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Optional


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
