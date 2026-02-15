# gwtlib/branches.py
import subprocess
from typing import Optional, Tuple

from gwtlib.git_ops import run_git_command, run_git_quiet


def get_main_branch_name(git_dir):
    """Extract the main branch name from git worktree list."""
    try:
        result = run_git_command(["worktree", "list"], git_dir)
        lines = result.stdout.splitlines()
        if lines:
            parts = lines[0].split()
            if len(parts) >= 3:
                return parts[2].strip("[]")
    except Exception:
        pass
    return None


def branch_exists_locally(branch_name, git_dir):
    """Check if a branch exists locally via git rev-parse."""
    try:
        run_git_quiet(["rev-parse", "--verify", f"refs/heads/{branch_name}"], git_dir)
        return True
    except Exception:
        return False


def find_remote_branch(branch_name, git_dir):
    """Search for remote branches matching given name, preferring origin."""
    run_git_command(["remote", "update"], git_dir)
    result = run_git_command(
        ["for-each-ref", "--format=%(refname:short)", f"refs/remotes/*/{branch_name}"],
        git_dir,
    )
    refs = [r for r in result.stdout.strip().split("\n") if r]
    if len(refs) == 1:
        return refs[0]
    elif len(refs) > 1:
        for ref in refs:
            if ref.startswith("origin/"):
                return ref
        return refs[0]
    return None


def get_remote_tracking_branch(branch_name: str, git_dir: str) -> Optional[str]:
    """Get the remote tracking branch for a local branch, if any.

    Returns the remote ref (e.g., 'origin/feature-branch') or None if not tracking.
    """
    try:
        result = run_git_quiet(
            ["config", "--get", f"branch.{branch_name}.remote"], git_dir
        )
        remote = result.stdout.strip()
        if not remote:
            return None

        result = run_git_quiet(
            ["config", "--get", f"branch.{branch_name}.merge"], git_dir
        )
        merge_ref = result.stdout.strip()
        if not merge_ref:
            return None

        # merge_ref is like refs/heads/branch-name, extract just the branch name
        if merge_ref.startswith("refs/heads/"):
            remote_branch = merge_ref[len("refs/heads/") :]
        else:
            remote_branch = merge_ref

        return f"{remote}/{remote_branch}"
    except subprocess.CalledProcessError:
        return None


def remote_branch_exists(remote_ref: str, git_dir: str) -> bool:
    """Check if a remote branch exists (e.g., 'origin/feature-branch').

    Args:
        remote_ref: Full remote ref like 'origin/branch-name'
        git_dir: Path to git directory
    """
    try:
        run_git_quiet(["rev-parse", "--verify", f"refs/remotes/{remote_ref}"], git_dir)
        return True
    except subprocess.CalledProcessError:
        return False


def delete_remote_branch(branch_name: str, remote: str, git_dir: str) -> bool:
    """Delete a branch from a remote.

    Args:
        branch_name: The branch name (without remote prefix)
        remote: The remote name (e.g., 'origin')
        git_dir: Path to git directory

    Returns:
        True if deletion succeeded, False otherwise
    """
    try:
        run_git_command(["push", remote, "--delete", branch_name], git_dir)
        return True
    except subprocess.CalledProcessError:
        return False


def get_pr_state(branch_name: str) -> Optional[Tuple[str, bool]]:
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

        import json

        data = json.loads(result.stdout)
        state = data.get("state", "UNKNOWN")
        merged_at = data.get("mergedAt")
        is_merged = merged_at is not None

        return (state, is_merged)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        return None
