# gwtlib/git_ops.py
import subprocess
import sys


def is_worktree_dirty(worktree_path: str, include_untracked: bool = True) -> bool:
    """Check if worktree has uncommitted changes.

    Args:
        worktree_path: Path to the worktree directory.
        include_untracked: If True, include untracked files in the check.
                          If False, only check tracked files (uses -uno flag).

    Returns:
        True if the worktree has uncommitted changes, False otherwise.
    """
    try:
        cmd = ["git", "-C", worktree_path, "status", "--porcelain"]
        if not include_untracked:
            cmd.append("-uno")
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"Warning: git status failed for {worktree_path}: {e}", file=sys.stderr)
        return True  # Fail closed: assume dirty if we can't check


def run_git_command(cmd_args, git_dir, capture=True):
    """Execute git commands with specified git directory."""
    cmd = ["git", f"--git-dir={git_dir}"] + cmd_args
    if capture:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result
    else:
        return subprocess.run(cmd, check=True)


def run_git_quiet(cmd_args, git_dir):
    """Like run_git_command but never prints; returns CompletedProcess."""
    return subprocess.run(
        ["git", f"--git-dir={git_dir}"] + cmd_args,
        check=True,
        capture_output=True,
        text=True,
    )


def run_git_in_worktree(cmd_args, worktree_path, capture=True):
    """For commands that must run with -C path (e.g., git -C path status)."""
    if capture:
        return subprocess.run(
            ["git", "-C", worktree_path] + cmd_args,
            check=True,
            capture_output=True,
            text=True,
        )
    return subprocess.run(["git", "-C", worktree_path] + cmd_args, check=True)


def run_git_simple(cmd_args, cwd=None, capture=True):
    """For commands like auto-detect that don't need --git-dir."""
    if capture:
        return subprocess.run(
            ["git"] + cmd_args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    return subprocess.run(["git"] + cmd_args, cwd=cwd, check=True)
