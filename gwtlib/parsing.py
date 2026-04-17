# gwtlib/parsing.py
import os
import subprocess
import sys
from typing import Optional

from gwtlib.git_ops import run_git_in_worktree, run_git_quiet
from gwtlib.paths import get_worktree_base


def get_main_branch_name(git_dir: str) -> Optional[str]:
    """Extract the main branch name from git worktree list.

    Parses the first line of `git worktree list` output to find the main branch.
    The main worktree is always listed first and shows the branch name in brackets.

    Args:
        git_dir: Path to the git directory.

    Returns:
        The main branch name (e.g., 'main' or 'master'), or None if it cannot be determined.
    """
    try:
        result = run_git_quiet(["worktree", "list"], git_dir)
        lines = result.stdout.splitlines()
        if lines:
            parts = lines[0].split()
            if len(parts) >= 3:
                branch = parts[2].strip("[]")
                # Return None if main worktree is in detached HEAD state
                if branch.startswith("(") or "detached" in branch.lower():
                    return None
                return branch
    except subprocess.CalledProcessError:
        pass
    return None


def parse_worktree_porcelain(git_dir, include_main=True):
    """
    Parse `git worktree list --porcelain`. Return a list of dict entries:
    {
      "path": str,
      "head": str,           # short SHA (7-10 chars) if available, else ""
      "branch": str or None, # branch name for normal, "(detached)" for detached, or None
      "is_main": bool,       # True for first block if it is the main worktree
      "locked": bool,
      "prunable": bool,
      "detached": bool,
    }
    Returns None if porcelain is unavailable or parsing fails (caller should fallback).
    """
    try:
        res = run_git_quiet(["worktree", "list", "--porcelain"], git_dir)
    except subprocess.CalledProcessError:
        return None

    lines = res.stdout.splitlines()
    if not lines:
        return []

    entries = []
    block = {}
    main_marked = False

    def push_block():
        nonlocal main_marked
        if not block:
            return
        # Normalize defaults
        block.setdefault("head", "")
        block.setdefault("branch", None)
        block.setdefault("locked", False)
        block.setdefault("prunable", False)
        block["detached"] = block.get("branch") == "(detached)"
        if not main_marked:
            block["is_main"] = True
            main_marked = True
        else:
            block["is_main"] = False
        # Shorten head
        if block["head"]:
            block["head"] = block["head"][:10]
        entries.append(block.copy())

    for ln in lines:
        if not ln.strip():
            continue
        if ln.startswith("worktree "):
            # Start of a new block
            if "path" in block:
                push_block()
                block = {}
            block["path"] = ln.split(" ", 1)[1].strip()
        elif ln.startswith("HEAD "):
            block["head"] = ln.split(" ", 1)[1].strip()
        elif ln.startswith("branch "):
            ref = ln.split(" ", 1)[1].strip()
            if ref == "(detached)":
                block["branch"] = "(detached)"
            elif ref.startswith("refs/heads/"):
                block["branch"] = ref[len("refs/heads/") :]
            else:
                block["branch"] = ref  # fallback
        elif ln.startswith("locked"):
            block["locked"] = True
        elif ln.startswith("prunable"):
            block["prunable"] = True
        # ignore other keys

    if "path" in block:
        push_block()

    # Optionally drop main if not included
    if not include_main and entries:
        entries = [e for i, e in enumerate(entries) if i != 0]
    return entries


def parse_worktree_legacy(git_dir, include_main=True):
    """
    Use `git worktree list` and parse the legacy format.
    Returns entries similar to parse_worktree_porcelain (without locked/prunable).
    """
    try:
        res = run_git_quiet(["worktree", "list"], git_dir)
    except subprocess.CalledProcessError:
        return []

    entries = []
    lines = res.stdout.splitlines()
    for i, line in enumerate(lines):
        parts = line.split()
        if len(parts) >= 1:
            path = parts[0]
            branch = None
            head = ""  # We'll try to fill short SHA per worktree below
            if len(parts) >= 3:
                branch_info = parts[2].strip("[]")
                if branch_info == "(detached)" or branch_info.startswith("(HEAD"):
                    branch = "(detached)"
                else:
                    branch = branch_info

            if i == 0 and not include_main:
                continue

            # Attempt to get short SHA for each path (best-effort)
            try:
                sha_res = run_git_in_worktree(["rev-parse", "--short=10", "HEAD"], path)
                head = sha_res.stdout.strip()
            except subprocess.CalledProcessError:
                head = ""

            entries.append(
                {
                    "path": path,
                    "head": head,
                    "branch": branch,
                    "is_main": (i == 0),
                    "locked": False,
                    "prunable": False,
                    "detached": (branch == "(detached)"),
                }
            )
    return entries


def get_git_worktrees(git_dir, include_main=False):
    """Get worktrees as reported by git worktree list command.

    Returns a dict mapping branch names to their paths.

    Args:
        git_dir: Path to git directory
        include_main: If True, include the main worktree in results
    """
    # Use existing parsers to avoid duplicating parsing logic
    entries = parse_worktree_porcelain(git_dir, include_main=include_main)
    if entries is None:
        entries = parse_worktree_legacy(git_dir, include_main=include_main)

    git_worktrees = {}
    for entry in entries:
        branch = entry.get("branch")
        path = entry.get("path")
        # Skip detached HEAD worktrees
        if branch and path and not entry.get("detached"):
            git_worktrees[branch] = path

    return git_worktrees


def get_directory_worktrees(git_dir):
    """Get worktrees by examining the worktree directory structure.

    Returns a dict mapping branch names to their paths.
    """
    dir_worktrees = {}
    worktree_base = get_worktree_base(git_dir)

    # Check if the worktree directory exists
    if not os.path.isdir(worktree_base):
        return dir_worktrees

    # Look for subdirectories in the worktree base directory
    try:
        for entry in os.listdir(worktree_base):
            path = os.path.join(worktree_base, entry)
            if os.path.isdir(path):
                # Check if it looks like a git worktree
                if os.path.isdir(os.path.join(path, ".git")) or os.path.isfile(
                    os.path.join(path, ".git")
                ):
                    dir_worktrees[entry] = path

        return dir_worktrees
    except OSError as e:
        print(f"Error examining worktree directory: {e}", file=sys.stderr)
        return dir_worktrees


def get_worktree_list(git_dir, include_main=False, warnings=None):
    """Get a list of all worktrees for the repository, comparing git command output
    with directory examination.

    Args:
        git_dir: Path to git directory
        include_main: If True, include the main worktree in results
        warnings: If provided, append warning strings instead of printing them

    Returns a list of dicts with 'path' and 'branch' keys.
    """

    def warn(msg):
        if warnings is not None:
            warnings.append(msg)
        else:
            print(msg, file=sys.stderr)

    # Get worktrees from both sources (already excludes main working tree)
    git_worktrees = get_git_worktrees(git_dir, include_main=include_main)
    dir_worktrees = get_directory_worktrees(git_dir)

    # Combine results, reporting any discrepancies
    worktrees = []
    all_branches = set(git_worktrees.keys()) | set(dir_worktrees.keys())

    for branch in all_branches:
        git_path = git_worktrees.get(branch)
        dir_path = dir_worktrees.get(branch)

        if git_path and dir_path:
            # Branch exists in both sources
            if git_path != dir_path:
                warn(f"Warning: Mismatch for branch '{branch}':")
                warn(f"  Git reports: {git_path}")
                warn(f"  Directory shows: {dir_path}")

            # Use the git path as the source of truth
            worktrees.append({"path": git_path, "branch": branch})
        elif git_path:
            # Branch exists in git but not in directory - only warn if it's
            # supposed to be in the .gwt directory
            worktree_base = get_worktree_base(git_dir)
            if git_path.startswith(worktree_base):
                warn(
                    f"Warning: Branch '{branch}' found by git but not in worktree directory"
                )
            worktrees.append({"path": git_path, "branch": branch})
        elif dir_path:
            # Branch exists in directory but not reported by git
            warn(
                f"Warning: Directory '{branch}' exists in worktree path but not recognized by git"
            )
            # Don't add to the list as it's not a valid worktree according to git

    return worktrees
