"""Tests for `gwt rm --merged` (bulk removal of merged-PR worktrees).

GitHub PR lookups are faked with a stub `gh` executable placed on PATH, so the
tests exercise the real CLI end to end against real git repos.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import gwt
import gwtlib.worktrees as worktrees_mod
from gwtlib.github import PrInfo

GWT_SCRIPT = Path(__file__).parent.parent / "gwt.py"


def _git(repo: Path, *args, env=None, check=True):
    """Run a git command in `repo`, returning the CompletedProcess."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path, env: dict) -> None:
    """Initialize a repo with one empty commit."""
    subprocess.run(
        ["git", "init", str(repo)], env=env, check=True, capture_output=True, text=True
    )
    _git(repo, "commit", "--allow-empty", "-m", "init", env=env)


def _write_stub_gh(bin_dir: Path, states: dict, head_refs: dict) -> str:
    """Write a `gh` stub answering `pr view <branch>` from `states`.

    `states` maps branch name to "MERGED"/"OPEN"/"CLOSED"/"AUTH_FAIL"/None
    (no PR). `head_refs` maps MERGED branches to their PR head SHA; a MERGED
    branch without one gets a null headRefOid (unverifiable). Returns a PATH
    value with `bin_dir` first.
    """
    lines = ["#!/usr/bin/env bash"]
    lines.append('if [ "$1" != "pr" ] || [ "$2" != "view" ]; then')
    lines.append("  exit 1")
    lines.append("fi")
    lines.append('branch="$3"')
    lines.append('case "$branch" in')
    for branch, state in states.items():
        if state is None:
            lines.append(f'  "{branch}") echo "no pull requests found" >&2; exit 1 ;;')
        elif state == "AUTH_FAIL":
            lines.append(
                f'  "{branch}") echo "failed to run git: gh auth login required" '
                ">&2; exit 1 ;;"
            )
        else:
            merged = '"2024-01-01T00:00:00Z"' if state == "MERGED" else "null"
            head = head_refs.get(branch)
            oid = f'"{head}"' if state == "MERGED" and head else "null"
            lines.append(
                f'  "{branch}") echo \'{{"state":"{state}","mergedAt":{merged},'
                f'"headRefOid":{oid}}}\' ;;'
            )
    lines.append('  *) echo "no pull requests found" >&2; exit 1 ;;')
    lines.append("esac")
    script = bin_dir / "gh"
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755)
    return str(bin_dir) + os.pathsep + os.environ["PATH"]


def _make_env(tmp_path, git_env, branches, states=None):
    """Create a repo + worktrees and a stub `gh`.

    MERGED branches get their current tip baked in as the stub's headRefOid
    (the PR head at "merge time"). Returns (env, repo, git_dir, wt_paths,
    outside) where env is ready for `_run_gwt` (PATH with stub gh first,
    XDG_CONFIG_HOME, GWT_GIT_DIR).
    """
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    _init_repo(repo, git_env)
    for br in branches:
        exists = _git(
            repo, "rev-parse", "--verify", f"refs/heads/{br}", env=git_env, check=False
        )
        if exists.returncode != 0:
            _git(repo, "branch", br, env=git_env)

    git_dir = str(repo / ".git")
    base = Path(gwt.get_worktree_base(git_dir))
    wt_paths = {}
    for br in branches:
        path = base / br
        _git(repo, "worktree", "add", str(path), br, env=git_env)
        wt_paths[br] = path

    head_refs = {}
    for br, state in (states or {}).items():
        if state == "MERGED":
            head_refs[br] = _git(repo, "rev-parse", br, env=git_env).stdout.strip()

    outside = tmp_path / "outside"
    outside.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = git_env.copy()
    env["PATH"] = _write_stub_gh(bin_dir, states or {}, head_refs)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["GWT_GIT_DIR"] = git_dir
    return env, repo, git_dir, wt_paths, outside


def _run_gwt(env, *args, input_text=None, cwd=None):
    """Run the real gwt.py CLI in a subprocess."""
    return subprocess.run(
        [sys.executable, str(GWT_SCRIPT), *args],
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _branch_exists(repo: Path, env: dict, branch: str) -> bool:
    """True if the local branch still exists."""
    res = _git(
        repo, "rev-parse", "--verify", f"refs/heads/{branch}", env=env, check=False
    )
    return res.returncode == 0


def test_rm_merged_happy_path(tmp_path, git_env):
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path,
        git_env,
        ["merged-a", "open-b", "closed-c"],
        {"merged-a": "MERGED", "open-b": "OPEN", "closed-c": "CLOSED"},
    )
    res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert "Will remove 1 worktree(s) with merged PRs:" in res.stderr
    assert "Skipping 2 worktree(s) without a merged PR." in res.stderr
    assert not wt_paths["merged-a"].exists()
    assert not _branch_exists(repo, env, "merged-a")
    assert wt_paths["open-b"].exists()
    assert _branch_exists(repo, env, "open-b")
    assert wt_paths["closed-c"].exists()
    assert _branch_exists(repo, env, "closed-c")


def test_rm_merged_dirty_skipped(tmp_path, git_env):
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path, git_env, ["merged-a"], {"merged-a": "MERGED"}
    )
    (wt_paths["merged-a"] / "untracked.txt").write_text("dirty")
    res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert "Skipping: dirty worktree (1):" in res.stderr
    assert "merged-a" in res.stderr
    assert wt_paths["merged-a"].exists()
    assert _branch_exists(repo, env, "merged-a")


def test_rm_merged_locked_skipped(tmp_path, git_env):
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path, git_env, ["merged-a"], {"merged-a": "MERGED"}
    )
    _git(repo, "worktree", "lock", str(wt_paths["merged-a"]), env=git_env)
    res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert "Skipping: locked worktree (1):" in res.stderr
    assert wt_paths["merged-a"].exists()
    assert _branch_exists(repo, env, "merged-a")


def test_rm_merged_diverged_skipped(tmp_path, git_env):
    # Commits made after the PR merged are not in the PR head; the worktree
    # must be kept even though the PR is MERGED.
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path, git_env, ["merged-a"], {"merged-a": "MERGED"}
    )
    _git(wt_paths["merged-a"], "commit", "--allow-empty", "-m", "extra", env=git_env)
    res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert "local commits not in the PR" in res.stderr
    assert wt_paths["merged-a"].exists()
    assert _branch_exists(repo, env, "merged-a")


def test_rm_merged_squash_merge_force_deleted(tmp_path, git_env):
    # squash-d has a commit not in main (so `git branch -d` would refuse), but
    # that commit IS the PR head, so the bulk flow force-deletes the branch.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    _git(repo, "checkout", "-b", "squash-d", env=git_env)
    _git(repo, "commit", "--allow-empty", "-m", "pr head", env=git_env)
    _git(repo, "checkout", "-", env=git_env)
    # _make_env is idempotent for existing branches; the stub bakes squash-d's
    # tip in as the PR headRefOid.
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path,
        git_env,
        ["squash-d"],
        {"squash-d": "MERGED"},
    )
    res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert not wt_paths["squash-d"].exists()
    assert not _branch_exists(repo, env, "squash-d")


def test_rm_merged_plan_only_removes_nothing(tmp_path, git_env):
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path, git_env, ["merged-a"], {"merged-a": "MERGED"}
    )
    res = _run_gwt(env, "rm", "--merged", "--plan", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert "Will remove 1 worktree(s) with merged PRs:" in res.stderr
    assert wt_paths["merged-a"].exists()
    assert _branch_exists(repo, env, "merged-a")


def test_rm_merged_prompt_decline_aborts(tmp_path, git_env):
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path, git_env, ["merged-a"], {"merged-a": "MERGED"}
    )
    res = _run_gwt(env, "rm", "--merged", input_text="n\n", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert "Aborted." in res.stderr
    assert wt_paths["merged-a"].exists()
    assert _branch_exists(repo, env, "merged-a")


def test_rm_merged_prompt_accept_removes(tmp_path, git_env):
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path, git_env, ["merged-a"], {"merged-a": "MERGED"}
    )
    res = _run_gwt(env, "rm", "--merged", input_text="y\n", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert not wt_paths["merged-a"].exists()
    assert not _branch_exists(repo, env, "merged-a")


def test_rm_merged_no_worktrees(tmp_path, git_env):
    env, _repo, _git_dir, _wt_paths, outside = _make_env(tmp_path, git_env, [])
    res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert "No worktrees found." in res.stderr


def test_rm_merged_nothing_merged(tmp_path, git_env):
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path, git_env, ["open-b"], {"open-b": "OPEN"}
    )
    res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert "No worktrees with merged PRs to remove." in res.stderr
    assert wt_paths["open-b"].exists()
    assert _branch_exists(repo, env, "open-b")


def test_rm_merged_gh_auth_failure_all_fail(tmp_path, git_env):
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path,
        git_env,
        ["fail-a", "fail-b"],
        {"fail-a": "AUTH_FAIL", "fail-b": "AUTH_FAIL"},
    )
    res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(outside))
    assert res.returncode == 1
    assert "PR lookup failed for all worktrees" in res.stderr
    assert wt_paths["fail-a"].exists()
    assert wt_paths["fail-b"].exists()


def test_rm_merged_gh_auth_failure_partial(tmp_path, git_env):
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path,
        git_env,
        ["merged-a", "fail-b"],
        {"merged-a": "MERGED", "fail-b": "AUTH_FAIL"},
    )
    res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert "could not check PR state for 1 worktree(s)" in res.stderr
    assert not wt_paths["merged-a"].exists()
    assert not _branch_exists(repo, env, "merged-a")
    assert wt_paths["fail-b"].exists()
    assert _branch_exists(repo, env, "fail-b")


def test_rm_merged_pr_head_missing_locally(tmp_path, git_env):
    # The PR head commit is not available locally (e.g. a suggestion was
    # committed on GitHub, or pushed from another clone). The worktree must be
    # reported as unverifiable, not as "local commits not in the PR".
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path, git_env, ["merged-a"], {"merged-a": "MERGED"}
    )
    env["PATH"] = _write_stub_gh(
        tmp_path / "bin",
        {"merged-a": "MERGED"},
        {"merged-a": "d" * 40},  # object not present in the repo
    )
    res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(outside))
    assert res.returncode == 0, res.stderr
    assert "PR head not available locally" in res.stderr
    assert "local commits not in the PR" not in res.stderr
    assert wt_paths["merged-a"].exists()
    assert _branch_exists(repo, env, "merged-a")


def test_execute_skips_branch_changed_since_planning(
    tmp_path, git_env, monkeypatch, capsys
):
    # A commit made after planning (e.g. while the Proceed? prompt is open)
    # keeps the worktree clean, so only the tip re-check can protect it from
    # the later `git branch -D`.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    pr_head = _git(repo, "rev-parse", "HEAD", env=git_env).stdout.strip()
    _git(repo, "branch", "merged-a", env=git_env)
    git_dir = str(repo / ".git")
    wt_path = Path(gwt.get_worktree_base(git_dir)) / "merged-a"
    _git(repo, "worktree", "add", str(wt_path), "merged-a", env=git_env)

    def fake_get_pr_info(branch, cwd=None, quiet=False):
        return PrInfo("MERGED", True, pr_head, None)

    monkeypatch.setattr(worktrees_mod, "get_pr_info", fake_get_pr_info)

    plan = worktrees_mod.create_merged_rm_plan(git_dir)
    assert [wt["branch"] for wt in plan.to_remove] == ["merged-a"]

    _git(wt_path, "commit", "--allow-empty", "-m", "sneaky", env=git_env)

    ok = worktrees_mod.execute_merged_rm_plan(plan, git_dir)
    assert ok is False
    assert wt_path.exists()
    assert _branch_exists(repo, git_env, "merged-a")
    assert "branch changed since planning" in capsys.readouterr().err


def test_rm_merged_gh_missing_errors(tmp_path, git_env):
    env, _repo, _git_dir, _wt_paths, outside = _make_env(tmp_path, git_env, [])
    git_only = tmp_path / "git-only"
    git_only.mkdir()
    git_bin = shutil.which("git")
    assert git_bin is not None
    os.symlink(git_bin, git_only / "git")
    env["PATH"] = str(git_only)
    res = _run_gwt(env, "rm", "--merged", cwd=str(outside))
    assert res.returncode == 1
    assert "gh CLI not found" in res.stderr


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permissions",
)
def test_rm_merged_failure_isolated(tmp_path, git_env):
    env, repo, _git_dir, wt_paths, outside = _make_env(
        tmp_path,
        git_env,
        ["good-a", "broken-b"],
        {"good-a": "MERGED", "broken-b": "MERGED"},
    )
    # Make broken-b unremovable while keeping it clean: a read-only subdir
    # holding a TRACKED file (an untracked file would make it "dirty" at plan
    # time). git worktree remove needs write permission on the directories it
    # deletes; git status and the gh stub still work read-only.
    broken_sub = wt_paths["broken-b"] / "sub"
    broken_sub.mkdir()
    (broken_sub / "f.txt").write_text("x")
    _git(wt_paths["broken-b"], "add", "sub/f.txt", env=git_env)
    _git(wt_paths["broken-b"], "commit", "-m", "tracked", env=git_env)
    # Re-bake the stub so broken-b's PR head is its current tip (the commit
    # above is "in the PR"); only the read-only dir then blocks removal.
    head_refs = {
        br: _git(repo, "rev-parse", br, env=git_env).stdout.strip()
        for br in ("good-a", "broken-b")
    }
    env["PATH"] = _write_stub_gh(
        tmp_path / "bin",
        {"good-a": "MERGED", "broken-b": "MERGED"},
        head_refs,
    )
    try:
        broken_sub.chmod(0o555)
        res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(outside))
    finally:
        broken_sub.chmod(0o755)  # let pytest's tmp cleanup remove it
    assert res.returncode == 1
    assert not wt_paths["good-a"].exists()
    assert not _branch_exists(repo, env, "good-a")
    assert _branch_exists(repo, env, "broken-b")
    assert "Failed: broken-b" in res.stderr
    assert "1 of 2 worktree(s) fully removed" in res.stderr


def test_rm_merged_cwd_inside_removed_worktree(tmp_path, git_env):
    env, repo, _git_dir, wt_paths, _outside = _make_env(
        tmp_path, git_env, ["merged-a"], {"merged-a": "MERGED"}
    )
    res = _run_gwt(env, "rm", "--merged", "-y", cwd=str(wt_paths["merged-a"]))
    assert res.returncode == 0, res.stderr
    assert not wt_paths["merged-a"].exists()
    assert not _branch_exists(repo, env, "merged-a")
    # The wrapper turns the trailing `cd <safe_dir>` line into a real cd
    assert res.stdout.strip().splitlines()[-1] == f"cd {repo}"


def test_rm_merged_usage_errors(tmp_path, git_env):
    env, _repo, _git_dir, _wt_paths, outside = _make_env(tmp_path, git_env, [])
    res = _run_gwt(env, "rm", cwd=str(outside))
    assert res.returncode == 2
    assert "a branch name or --merged is required" in res.stderr
    res = _run_gwt(env, "rm", "somebranch", "--merged", cwd=str(outside))
    assert res.returncode == 2
    assert "cannot combine a branch name with --merged" in res.stderr
    res = _run_gwt(env, "rm", "somebranch", "--plan", cwd=str(outside))
    assert res.returncode == 2
    assert "--plan and --yes require --merged" in res.stderr


def test_create_merged_rm_plan_buckets(tmp_path, git_env, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    pr_head = _git(repo, "rev-parse", "HEAD", env=git_env).stdout.strip()
    branches = (
        "merged-a",
        "open-b",
        "no-pr-c",
        "dirty-d",
        "locked-e",
        "diverge-f",
        "fail-g",
        "missing-h",
    )
    for br in branches:
        _git(repo, "branch", br, env=git_env)
    git_dir = str(repo / ".git")
    base = Path(gwt.get_worktree_base(git_dir))
    wt_paths = {}
    for br in branches:
        path = base / br
        _git(repo, "worktree", "add", str(path), br, env=git_env)
        wt_paths[br] = path

    (wt_paths["dirty-d"] / "untracked.txt").write_text("dirty")
    _git(repo, "worktree", "lock", str(wt_paths["locked-e"]), env=git_env)
    # diverge-f: commit after the PR merged -> tip moves past the PR head
    _git(wt_paths["diverge-f"], "commit", "--allow-empty", "-m", "extra", env=git_env)

    states = {
        "merged-a": ("MERGED", True, pr_head),
        "open-b": ("OPEN", False, None),
        "no-pr-c": None,
        "dirty-d": ("MERGED", True, pr_head),
        "locked-e": ("MERGED", True, pr_head),
        "diverge-f": ("MERGED", True, pr_head),
        "fail-g": "error",
        "missing-h": ("MERGED", True, "d" * 40),  # PR head object absent locally
    }

    def fake_get_pr_info(branch, cwd=None, quiet=False):
        entry = states.get(branch)
        if entry is None:
            return PrInfo(None, False, None, None)
        if isinstance(entry, str):  # error marker
            return PrInfo(None, False, None, entry)
        state, is_merged, head = entry
        return PrInfo(state, is_merged, head, None)

    monkeypatch.setattr(worktrees_mod, "get_pr_info", fake_get_pr_info)

    plan = worktrees_mod.create_merged_rm_plan(git_dir)

    assert [wt["branch"] for wt in plan.to_remove] == ["merged-a"]
    assert [wt["branch"] for wt in plan.skipped_dirty] == ["dirty-d"]
    assert [wt["branch"] for wt in plan.skipped_locked] == ["locked-e"]
    assert [wt["branch"] for wt in plan.skipped_diverged] == ["diverge-f"]
    assert [wt["branch"] for wt, _reason in plan.skipped_failed] == [
        "fail-g",
        "missing-h",
    ]
    assert plan.lookup_failed_count == 1  # only fail-g (missing-h is unverifiable)
    assert plan.skipped_other == 2  # open-b and no-pr-c
