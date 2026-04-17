# gwtlib/paths.py
import os
from pathlib import Path
from typing import Optional, cast


def get_worktree_base(git_dir: str) -> str:
    """Get the base directory for worktrees, creating it if needed."""
    git_dir_path = Path(git_dir).resolve()
    if git_dir_path.name == ".git" and git_dir_path.is_dir():
        repo_path = git_dir_path.parent
        worktree_base = str(repo_path) + ".gwt"
    elif git_dir.endswith(".git"):
        worktree_base = git_dir[:-4] + ".gwt"
    else:
        worktree_base = git_dir.rstrip("/") + ".gwt"
    if not os.path.exists(worktree_base):
        os.makedirs(worktree_base, exist_ok=True)
        readme_path = os.path.join(worktree_base, "README.md")
        with open(readme_path, "w") as f:
            f.write(
                """# Git Worktree Directory

This directory contains git worktrees managed by the gwt tool.
Each subdirectory is a separate worktree for a branch.

For more information, see: https://github.com/username/gwt
"""
            )
    return worktree_base


def get_main_worktree_path(git_dir: str) -> Optional[str]:
    """Get the path to the main worktree for non-bare repositories."""
    git_dir_path = Path(git_dir).resolve()
    if git_dir_path.name == ".git" and git_dir_path.is_dir():
        return str(git_dir_path.parent)
    else:
        return None


def is_path_current_worktree(path: str) -> bool:
    """Check if the current directory is inside the given worktree path."""
    try:
        cur = os.path.abspath(os.getcwd())
        p = os.path.abspath(path)
        return cur == p or cur.startswith(p + os.sep)
    except Exception:
        return False


def rel_display_path(path: str, git_dir: str, force_absolute: bool) -> str:
    """Return relative path for display (relative for .gwt worktrees, absolute for main)."""
    if force_absolute:
        return os.path.abspath(path)
    main = get_main_worktree_path(git_dir)
    base = get_worktree_base(git_dir)
    if main and os.path.abspath(path) == os.path.abspath(main):
        return os.path.abspath(path)
    if base and os.path.abspath(path).startswith(os.path.abspath(base) + os.sep):
        return cast(str, os.path.relpath(path, os.path.dirname(base)))
    return os.path.abspath(path)
