"""Choose and validate the ref used as the repository's trunk."""

import subprocess

from gwtlib.config import load_config
from gwtlib.git_ops import run_git_quiet
from gwtlib.parsing import get_main_branch_name


class BaseRefError(Exception):
    """The requested starting ref could not be prepared."""


def _remote_names(git_dir):
    return run_git_quiet(["remote"], git_dir).stdout.splitlines()


def _configured_base_ref(git_dir):
    return load_config().get("repos", {}).get(git_dir, {}).get("base_ref")


def get_base_ref(git_dir):
    """Use a configured base, or origin's locally recorded default branch.

    A repository without origin retains the previous main-worktree behavior.
    """
    configured = _configured_base_ref(git_dir)
    if configured:
        return configured

    if "origin" in _remote_names(git_dir):
        try:
            return run_git_quiet(
                ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
                git_dir,
            ).stdout.strip()
        except subprocess.CalledProcessError as e:
            raise BaseRefError(
                "Could not determine origin's default branch from origin/HEAD; "
                "fetch origin and run 'git remote set-head origin --auto', "
                "or set repos.<git-dir>.base_ref."
            ) from e

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


def _fetch_remote(remote, git_dir):
    try:
        run_git_quiet(["fetch", remote], git_dir)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip() or str(e)
        raise BaseRefError(f"Could not fetch '{remote}': {detail}.") from e


def prepare_base_ref(git_dir, ref=None, fetch=True):
    """Fetch a remote base if needed, then ensure it names a commit."""
    automatic_origin = (
        not ref
        and not _configured_base_ref(git_dir)
        and "origin" in _remote_names(git_dir)
    )
    if automatic_origin and fetch:
        _fetch_remote("origin", git_dir)
        try:
            run_git_quiet(["remote", "set-head", "origin", "--auto"], git_dir)
        except subprocess.CalledProcessError as e:
            raise BaseRefError(
                "Could not refresh origin's default branch; "
                "set repos.<git-dir>.base_ref to an explicit ref."
            ) from e

    base_ref = ref or get_base_ref(git_dir)
    remote = _remote_for_ref(base_ref, git_dir)
    if remote and fetch and not automatic_origin:
        _fetch_remote(remote, git_dir)
    try:
        run_git_quiet(["rev-parse", "--verify", f"{base_ref}^{{commit}}"], git_dir)
    except subprocess.CalledProcessError as e:
        raise BaseRefError(f"Could not resolve base ref '{base_ref}'.") from e
    return base_ref
