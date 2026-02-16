# gwtlib/gc.py
"""Garbage collection for stale worktrees."""

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, List, Optional

try:
    from tqdm import tqdm

    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    tqdm = None  # type: ignore

from gwtlib.config import get_repo_config
from gwtlib.git_ops import is_worktree_dirty, run_git_command, run_git_quiet
from gwtlib.parsing import get_main_branch_name, get_worktree_list
from gwtlib.paths import rel_display_path
from gwtlib.ui import prompt_yes_no


def _is_branch_merged_to_main(branch_name: str, git_dir: str) -> bool:
    """Check if a branch has been merged to main.

    Returns True if all commits in the branch are also in main.
    """
    main_branch = get_main_branch_name(git_dir)
    if not main_branch:
        # Can't determine main branch, assume not merged to be safe
        return False

    try:
        # Check if there are any commits in branch that aren't in main
        result = run_git_quiet(
            ["log", f"{main_branch}..{branch_name}", "--oneline"],
            git_dir,
        )
        # If no output, branch is fully merged
        return not result.stdout.strip()
    except subprocess.CalledProcessError:
        # Error checking, assume not merged to be safe
        return False


# Default thresholds
CLEAN_THRESHOLD_DAYS = 7
DELETE_THRESHOLD_DAYS = 28


@dataclass
class WorktreeInfo:
    """Information about a worktree for garbage collection."""

    path: str
    branch: str
    mtime: float  # Most recent modification time (Unix timestamp)
    age_days: float  # Age in days since last modification
    is_dirty: bool
    is_merged: bool  # True if branch is merged to main
    is_main: bool = False


def get_worktree_mtime(worktree_path: str) -> float:
    """Get the most recent modification time of any file in a worktree.

    Walks the directory tree and finds the most recently modified file,
    excluding .git directory.

    Returns:
        Unix timestamp of most recent modification.
    """
    most_recent = 0.0
    worktree_path = os.path.abspath(worktree_path)

    for root, dirs, files in os.walk(worktree_path):
        # Skip .git directory
        if ".git" in dirs:
            dirs.remove(".git")

        # Check directory modification time
        try:
            dir_mtime = os.path.getmtime(root)
            most_recent = max(most_recent, dir_mtime)
        except OSError:
            pass

        # Check file modification times
        for filename in files:
            try:
                filepath = os.path.join(root, filename)
                file_mtime = os.path.getmtime(filepath)
                most_recent = max(most_recent, file_mtime)
            except OSError:
                pass

    return most_recent


def get_worktree_info_list(
    git_dir: str, include_main: bool = False
) -> List[WorktreeInfo]:
    """Get information about all worktrees including modification times.

    Args:
        git_dir: Path to the git directory.
        include_main: Whether to include the main worktree.

    Returns:
        List of WorktreeInfo objects sorted by age (oldest first).
    """
    worktrees = get_worktree_list(git_dir, include_main=include_main)
    current_time = time.time()
    info_list = []

    # Show progress
    if HAS_TQDM and tqdm is not None:
        iterator = tqdm(  # type: ignore[misc]
            worktrees,
            desc="Scanning",
            file=sys.stderr,
            unit="worktree",
        )
    else:
        iterator = worktrees

    for wt in iterator:
        path = wt["path"]
        branch = wt.get("branch", "")

        # Skip if path doesn't exist
        if not os.path.isdir(path):
            continue

        mtime = get_worktree_mtime(path)
        age_seconds = current_time - mtime
        age_days = age_seconds / (24 * 60 * 60)

        info = WorktreeInfo(
            path=path,
            branch=branch,
            mtime=mtime,
            age_days=age_days,
            is_dirty=is_worktree_dirty(path),
            is_merged=_is_branch_merged_to_main(branch, git_dir),
            is_main=wt.get("is_main", False),
        )
        info_list.append(info)

    # Sort by age (oldest first)
    info_list.sort(key=lambda x: -x.age_days)
    return info_list


@dataclass
class GcPlan:
    """Plan for garbage collection."""

    to_clean: List[WorktreeInfo]  # Worktrees > clean_days old, to run clean command
    to_delete: List[WorktreeInfo]  # Worktrees > delete_days old, clean, and merged
    dirty: List[WorktreeInfo]  # Worktrees > delete_days old but dirty
    unmerged: List[WorktreeInfo]  # Worktrees > delete_days old, clean, but not merged
    skip: List[WorktreeInfo]  # Other worktrees (< clean_days old)


def create_gc_plan(
    git_dir: str,
    clean_days: int = CLEAN_THRESHOLD_DAYS,
    delete_days: int = DELETE_THRESHOLD_DAYS,
) -> GcPlan:
    """Create a garbage collection plan.

    Args:
        git_dir: Path to the git directory.
        clean_days: Threshold for cleaning (default 7 days).
        delete_days: Threshold for deletion (default 28 days).

    Returns:
        GcPlan with categorized worktrees.
    """
    worktrees = get_worktree_info_list(git_dir, include_main=False)

    to_clean = []
    to_delete = []
    dirty = []
    unmerged = []
    skip = []

    for wt in worktrees:
        if wt.age_days >= delete_days:
            # Old enough for deletion
            if wt.is_dirty:
                dirty.append(wt)
                to_clean.append(wt)  # Still clean dirty worktrees
            elif not wt.is_merged:
                unmerged.append(wt)
                to_clean.append(wt)  # Still clean unmerged worktrees
            else:
                to_delete.append(wt)
        elif wt.age_days >= clean_days:
            # Old enough for cleaning but not deletion
            to_clean.append(wt)
        else:
            # Too recent, skip
            skip.append(wt)

    return GcPlan(
        to_clean=to_clean,
        to_delete=to_delete,
        dirty=dirty,
        unmerged=unmerged,
        skip=skip,
    )


def format_age(days: float) -> str:
    """Format age in days."""
    if days < 1:
        return "<1d"
    return f"{int(days)}d"


def _path_matches_branch(path: str, branch: str, git_dir: str) -> bool:
    """Check if the worktree path is the expected .gwt/{branch} location."""
    from gwtlib.paths import get_worktree_base

    expected = os.path.join(get_worktree_base(git_dir), branch)
    return os.path.abspath(path) == os.path.abspath(expected)


def _format_worktree_line(wt: WorktreeInfo, git_dir: str, suffix: str = "") -> str:
    """Format a single worktree as one line."""
    age = format_age(wt.age_days)
    # Only show path if it doesn't match expected location
    if _path_matches_branch(wt.path, wt.branch, git_dir):
        return f"  {wt.branch}  ({age}){suffix}"
    else:
        path_display = rel_display_path(wt.path, git_dir, force_absolute=False)
        return f"  {wt.branch}  ({age}){suffix}  [{path_display}]"


def print_plan(plan: GcPlan, git_dir: str, clean_days: int, delete_days: int) -> None:
    """Print the garbage collection plan to stderr."""
    total = (
        len(plan.to_clean)
        + len(plan.to_delete)
        + len(plan.dirty)
        + len(plan.unmerged)
        + len(plan.skip)
    )

    if total == 0:
        print("No worktrees found.", file=sys.stderr)
        return

    # Check if nothing to do
    if (
        not plan.to_clean
        and not plan.to_delete
        and not plan.dirty
        and not plan.unmerged
    ):
        print(f"All {len(plan.skip)} worktree(s) are recent.", file=sys.stderr)
        return

    # Print worktrees to clean (run clean command)
    if plan.to_clean:
        print(
            f"\nWill clean {len(plan.to_clean)} worktrees over {clean_days} days old:",
            file=sys.stderr,
        )
        for wt in plan.to_clean:
            suffix = " [dirty]" if wt.is_dirty else ""
            print(_format_worktree_line(wt, git_dir, suffix), file=sys.stderr)

    # Print worktrees to delete
    if plan.to_delete:
        print(
            f"\nWill delete {len(plan.to_delete)} worktrees over {delete_days} days old:",
            file=sys.stderr,
        )
        for wt in plan.to_delete:
            print(_format_worktree_line(wt, git_dir), file=sys.stderr)

    # Print dirty worktrees that can't be deleted
    if plan.dirty:
        print(
            f"\nOld but dirty, inspect manually ({len(plan.dirty)}):", file=sys.stderr
        )
        for wt in plan.dirty:
            print(_format_worktree_line(wt, git_dir), file=sys.stderr)

    # Print unmerged worktrees that can't be auto-deleted
    if plan.unmerged:
        print(
            f"\nOld but unmerged, inspect manually ({len(plan.unmerged)}):",
            file=sys.stderr,
        )
        for wt in plan.unmerged:
            print(_format_worktree_line(wt, git_dir), file=sys.stderr)

    # Summary
    print("", file=sys.stderr)
    if plan.skip:
        print(f"Keeping {len(plan.skip)} recent worktree(s)", file=sys.stderr)


def run_clean_command(
    worktree_path: str, git_dir: str, clean_cmd: str | None = None
) -> bool:
    """Run a clean command in a worktree.

    Args:
        worktree_path: Path to the worktree.
        git_dir: Path to the git directory.
        clean_cmd: Custom clean command, or None for default.

    Returns:
        True if command succeeded, False otherwise.
    """
    # Get clean command from config or use default
    if clean_cmd is None:
        repo_config = get_repo_config(git_dir)
        clean_cmd = str(repo_config.get("clean_command", "just clean"))

    current_dir = os.getcwd()
    try:
        os.chdir(worktree_path)
        print(f"  Running: {clean_cmd}", file=sys.stderr)
        result = subprocess.run(
            clean_cmd,  # type: ignore[arg-type]
            shell=True,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            if result.stderr:
                print(f"  Warning: {result.stderr.strip()}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"  Error running clean command: {e}", file=sys.stderr)
        return False
    finally:
        os.chdir(current_dir)


def execute_gc_plan(
    plan: GcPlan,
    git_dir: str,
    clean_cmd: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    """Execute the garbage collection plan.

    Args:
        plan: The GcPlan to execute.
        git_dir: Path to the git directory.
        clean_cmd: Custom clean command for cleaning.
        dry_run: If True, only print what would be done.
    """
    # Run clean commands
    if plan.to_clean:
        print("\nCleaning worktrees...", file=sys.stderr)
        for wt in plan.to_clean:
            print(f"\n{wt.branch}:", file=sys.stderr)
            if dry_run:
                print(f"  Would run clean command", file=sys.stderr)
            else:
                run_clean_command(wt.path, git_dir, clean_cmd)

    # Delete old worktrees
    if plan.to_delete:
        print("\nRemoving worktrees...", file=sys.stderr)
        for wt in plan.to_delete:
            print(f"\n{wt.branch}:", file=sys.stderr)
            if dry_run:
                print(f"  Would remove worktree", file=sys.stderr)
            else:
                try:
                    # Use git worktree remove directly since we know it's clean
                    run_git_command(
                        ["worktree", "remove", wt.path],
                        git_dir,
                        capture=False,
                    )
                    print(f"  Removed worktree", file=sys.stderr)

                    # Also delete the local branch
                    try:
                        run_git_command(
                            ["branch", "-d", wt.branch],
                            git_dir,
                            capture=False,
                        )
                        print(f"  Deleted branch '{wt.branch}'", file=sys.stderr)
                    except subprocess.CalledProcessError:
                        # Branch might not be fully merged, try force delete
                        try:
                            run_git_command(
                                ["branch", "-D", wt.branch],
                                git_dir,
                                capture=False,
                            )
                            print(
                                f"  Force-deleted branch '{wt.branch}'", file=sys.stderr
                            )
                        except subprocess.CalledProcessError as e:
                            print(f"  Could not delete branch: {e}", file=sys.stderr)
                except subprocess.CalledProcessError as e:
                    print(f"  Error removing worktree: {e}", file=sys.stderr)

    print("\nDone.", file=sys.stderr)


def gc_worktrees(
    git_dir: str,
    clean_days: int = CLEAN_THRESHOLD_DAYS,
    delete_days: int = DELETE_THRESHOLD_DAYS,
    clean_cmd: Optional[str] = None,
    yes: bool = False,
    plan_only: bool = False,
) -> None:
    """Run garbage collection on worktrees.

    Args:
        git_dir: Path to the git directory.
        clean_days: Threshold in days for cleaning (default 7).
        delete_days: Threshold in days for deletion (default 28).
        clean_cmd: Custom clean command.
        yes: Skip confirmation prompt.
        plan_only: Only print plan, don't execute.
    """
    # Create the plan
    plan = create_gc_plan(git_dir, clean_days=clean_days, delete_days=delete_days)

    # Print the plan
    print_plan(plan, git_dir, clean_days=clean_days, delete_days=delete_days)

    # Check if there's anything to do
    if not plan.to_clean and not plan.to_delete:
        return

    # Plan only mode - just exit after printing
    if plan_only:
        return

    # Prompt for confirmation
    if not yes:
        print("", file=sys.stderr)
        if not prompt_yes_no("Proceed?"):
            print("Aborted.", file=sys.stderr)
            return

    # Execute the plan
    execute_gc_plan(plan, git_dir, clean_cmd=clean_cmd, dry_run=False)
