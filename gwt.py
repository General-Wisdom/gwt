#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "tomli>=2.0.0; python_version < '3.11'",
#   "tomli-w>=1.0.0",
#   "tqdm>=4.0.0",
# ]
# ///

# Thin compatibility shim: delegates to gwtlib and re-exports public API

# Explicit re-exports required by tests (import gwt; gwt.func())
from gwtlib.api import (
    ColorMode,
    auto_detect_git_dir,
    branch_exists_locally,
    create_worktree_for_branch,
    get_main_worktree_path,
    get_remote_tracking_branch,
    get_worktree_base,
    get_worktree_list,
    is_path_current_worktree,
    parse_worktree_legacy,
    parse_worktree_porcelain,
    rel_display_path,
    remote_branch_exists,
)
from gwtlib.cli import main  # CLI entrypoint

__all__ = [
    "main",
    # Public API used by tests
    "get_worktree_base",
    "get_main_worktree_path",
    "is_path_current_worktree",
    "rel_display_path",
    "create_worktree_for_branch",
    "branch_exists_locally",
    "get_remote_tracking_branch",
    "remote_branch_exists",
    "parse_worktree_porcelain",
    "parse_worktree_legacy",
    "get_worktree_list",
    "auto_detect_git_dir",
    "ColorMode",
]

if __name__ == "__main__":
    main()
