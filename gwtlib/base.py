"""Choose and validate the ref used as the repository's trunk."""

import subprocess

from gwtlib.config import load_config
from gwtlib.git_ops import run_git_quiet
from gwtlib.parsing import get_main_branch_name


class BaseRefError(Exception):
    """The requested starting ref could not be prepared."""


def _remote_names(git_dir):
    return run_git_quiet(["remote"], git_dir).stdout.splitlines()


def get_base_ref(git_dir):
    """Use a configured base, or origin/main when origin is available.

    A repository without origin retains the previous main-worktree behavior.
    """
    configured = load_config().get("repos", {}).get(git_dir, {}).get("base_ref")
    if configured:
        return configured

    if "origin" in _remote_names(git_dir):
        return "origin/main"

    main_branch = get_main_branch_name(git_dir)
    if not main_branch:
        raise BaseRefError(
            "Could not determine the trunk branch; set repos.<git-dir>.base_ref."
        )
    return main_branch


def _remote_for_ref(ref, git_dir):
    short_ref = ref.removeprefix("refs/remotes/")
    for remote in sorted(_remote_names(git_dir), key=len, reverse=True):
        if short_ref.startswith(f"{remote}/"):
            return remote
    return None


def prepare_base_ref(git_dir, ref=None, fetch=True):
    """Fetch a remote base if needed, then ensure it names a commit."""
    base_ref = ref or get_base_ref(git_dir)
    remote = _remote_for_ref(base_ref, git_dir)
    if remote and fetch:
        try:
            run_git_quiet(["fetch", remote], git_dir)
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or "").strip() or str(e)
            raise BaseRefError(f"Could not fetch '{remote}': {detail}.") from e
    try:
        run_git_quiet(["rev-parse", "--verify", f"{base_ref}^{{commit}}"], git_dir)
    except subprocess.CalledProcessError as e:
        raise BaseRefError(f"Could not resolve base ref '{base_ref}'.") from e
    return base_ref
