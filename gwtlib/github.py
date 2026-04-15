# gwtlib/github.py
"""GitHub CLI integrations."""

from __future__ import annotations

import json
import subprocess
from typing import Optional


def get_pr_state(branch_name: str) -> Optional[tuple[str, bool]]:
    """Check the PR state for a branch using GitHub CLI.

    Returns:
        Tuple of (state, is_merged) where state is 'OPEN', 'CLOSED', or 'MERGED',
        or None if no PR exists or gh CLI is not available.

    Note: This uses the gh CLI and works in any directory with a GitHub remote.
    """
    try:
        # Use gh pr view to get PR info for this branch
        result = subprocess.run(
            ["gh", "pr", "view", branch_name, "--json", "state,mergedAt"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        state = data.get("state", "UNKNOWN")
        merged_at = data.get("mergedAt")
        is_merged = merged_at is not None

        return (state, is_merged)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        return None
