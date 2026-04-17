# gwtlib/api.py
from gwtlib.branches import (
    branch_exists_locally,
    get_remote_tracking_branch,
    remote_branch_exists,
)
from gwtlib.display import ColorMode
from gwtlib.parsing import (
    get_main_branch_name,
    get_worktree_list,
    parse_worktree_legacy,
    parse_worktree_porcelain,
)
from gwtlib.paths import (
    get_main_worktree_path,
    get_worktree_base,
    is_path_current_worktree,
    rel_display_path,
)
from gwtlib.resolution import auto_detect_git_dir
from gwtlib.worktrees import create_worktree_for_branch

__all__ = [
    # paths
    "get_worktree_base",
    "get_main_worktree_path",
    "is_path_current_worktree",
    "rel_display_path",
    # worktrees
    "create_worktree_for_branch",
    # branches
    "branch_exists_locally",
    "get_remote_tracking_branch",
    "remote_branch_exists",
    # parsing
    "parse_worktree_porcelain",
    "parse_worktree_legacy",
    "get_worktree_list",
    "get_main_branch_name",
    # resolution
    "auto_detect_git_dir",
    # display
    "ColorMode",
]
