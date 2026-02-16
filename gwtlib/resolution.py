# gwtlib/resolution.py
import os
import subprocess
from typing import Optional

from gwtlib.config import HAS_TOML, load_config
from gwtlib.git_ops import run_git_simple


def _normalize_repo_path(p: str) -> str:
    """Auto-append .git for non-bare repositories when given a repo root directory."""
    if os.path.isdir(p):
        dot_git = os.path.join(p, ".git")
        if os.path.isdir(dot_git):
            return dot_git
    return p


def auto_detect_git_dir(cwd: Optional[str] = None) -> Optional[str]:
    """Return absolute path to the git common dir for current directory, or None if not in a git repo.
    Uses: git rev-parse --git-common-dir
    Works for subdirs, worktrees, bare repos, and submodules.
    """
    try:
        run_cwd = cwd or os.getcwd()
        res = run_git_simple(["rev-parse", "--git-common-dir"], cwd=run_cwd)
        out = res.stdout.strip()
        if not out:
            return None
        # rev-parse may return relative path (e.g. .git), normalize to absolute
        if not os.path.isabs(out):
            out = os.path.abspath(os.path.join(run_cwd, out))
        if os.path.isdir(out):
            return out
        return None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_git_dir_with_source(explicit_git_dir: Optional[str] = None):
    """Resolve git dir with priority:
    1) explicit_git_dir (CLI) if provided
    2) auto-detect via current directory
    3) env var GWT_GIT_DIR
    4) config default_repo
    Returns (git_dir:str|None, source:str|None, meta:dict).
    meta contains useful info for error messages.
    """
    meta = {"env": os.environ.get("GWT_GIT_DIR"), "config": None}
    if HAS_TOML:
        cfg = load_config()
        meta["config"] = cfg.get("default_repo")

    # 1) Explicit (for list completion only)
    if explicit_git_dir:
        gd = _normalize_repo_path(explicit_git_dir)
        return (gd, "arg", meta)

    # 2) Auto-detect
    gd_auto = auto_detect_git_dir()
    if gd_auto:
        return (gd_auto, "auto", meta)

    # 3) Env var
    if meta["env"]:
        gd_env = _normalize_repo_path(meta["env"])
        if os.path.isdir(gd_env):
            return (gd_env, "env", meta)
        # If invalid, return with source and let caller error out
        return (None, "env_invalid", meta)

    # 4) Config
    if meta["config"]:
        gd_cfg = _normalize_repo_path(meta["config"])
        if os.path.isdir(gd_cfg):
            return (gd_cfg, "config", meta)
        return (None, "config_invalid", meta)

    return (None, None, meta)


# Backward-compatible wrapper
def get_git_dir() -> Optional[str]:
    """Get the git directory from either the environment variable or the config file.

    This is a common function to encapsulate the logic for determining the git dir.
    Automatically appends .git for non-bare repositories.

    Returns:
        The git directory path, or None if not found
    """
    gd, _, _ = get_git_dir_with_source()
    return gd
