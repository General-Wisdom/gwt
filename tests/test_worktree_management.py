import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import gwt


def _subprocess_result(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def test_parse_worktree_porcelain(monkeypatch, tmp_path):
    # Simulate porcelain output with main + one worktree + locked/prunable flags
    porcelain = """worktree {main}
HEAD 0123456789abcdef
branch refs/heads/main

worktree {wt1}
HEAD abcdef0123456789
branch refs/heads/feature
locked

worktree {wt2}
HEAD fedcba9876543210
branch (detached)
prunable

""".format(
        main=str(tmp_path / "repo"),
        wt1=str(tmp_path / "repo.gwt" / "feature"),
        wt2=str(tmp_path / "repo.gwt" / "det"),
    )

    def fake_run(cmd, capture_output, text, check):
        assert "worktree" in cmd
        assert "--porcelain" in cmd
        return _subprocess_result(stdout=porcelain)

    monkeypatch.setattr(subprocess, "run", fake_run)
    entries = gwt.parse_worktree_porcelain("unused.git", include_main=True)
    assert entries and entries[0]["is_main"] is True
    assert entries[1]["locked"] is True
    assert entries[2]["prunable"] is True
    assert entries[2]["detached"] is True
    assert entries[1]["branch"] == "feature"
    # head shortened to 10 chars
    assert len(entries[1]["head"]) == 10


def test_parse_worktree_legacy(monkeypatch, tmp_path):
    # Typical legacy format lines
    # path   sha    [branch]
    legacy = f"""{tmp_path}/repo  0123456 [main]
{tmp_path}/repo.gwt/feature  abcdef0 [feature]
{tmp_path}/repo.gwt/detached  deadbee [(detached)]
"""

    def fake_run(cmd, capture_output, text, check):
        # Handle both worktree list and rev-parse calls
        if "worktree" in cmd and "list" in cmd:
            return _subprocess_result(stdout=legacy)
        elif "rev-parse" in cmd:
            # For rev-parse --short we just return requested sha
            return _subprocess_result(stdout="cafebabeee")
        else:
            return _subprocess_result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    entries = gwt.parse_worktree_legacy("unused.git", include_main=True)
    assert len(entries) == 3
    assert entries[0]["is_main"]
    assert entries[2]["detached"]


def _init_repo(repo: Path, env: dict):
    subprocess.run(
        ["git", "init", str(repo)], env=env, check=True, capture_output=True, text=True
    )
    # Initial empty commit
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_branch_exists_locally_and_worktree_listing(tmp_path, git_env):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    git_dir = str(repo / ".git")

    # Create a new branch
    subprocess.run(
        ["git", "-C", str(repo), "branch", "feature"], env=git_env, check=True
    )

    assert gwt.branch_exists_locally("feature", git_dir)
    assert not gwt.branch_exists_locally("nope", git_dir)

    # Create a worktree via the tool function (exercise run_git_command path)
    base = gwt.get_worktree_base(git_dir)
    wt_path = os.path.join(base, "feature")
    gwt.create_worktree_for_branch("feature", git_dir, wt_path)

    # Now ensure get_worktree_list sees the branch
    wts = gwt.get_worktree_list(git_dir, include_main=False)
    branches = {w["branch"] for w in wts}
    assert "feature" in branches


def test_list_worktrees_excludes_main(tmp_path, git_env):
    """Test that list --branches worktrees excludes main branch."""
    import sys

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    git_dir = str(repo / ".git")

    # Create a worktree branch
    subprocess.run(
        ["git", "-C", str(repo), "branch", "feature"], env=git_env, check=True
    )

    wt_base = gwt.get_worktree_base(git_dir)
    wt_path = os.path.join(wt_base, "feature")
    gwt.create_worktree_for_branch("feature", git_dir, wt_path)

    # Run from outside any git repo to avoid auto-detection interference
    outside = tmp_path / "outside"
    outside.mkdir()

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"
    env = git_env.copy()
    env["GWT_GIT_DIR"] = git_dir
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")

    original_dir = os.getcwd()
    try:
        os.chdir(outside)
        res = subprocess.run(
            [sys.executable, str(gwt_script), "list", "--branches", "worktrees"],
            env=env,
            capture_output=True,
            text=True,
        )

        assert res.returncode == 0
        lines = res.stdout.strip().split('\n') if res.stdout.strip() else []
        assert "feature" in lines
        assert "main" not in lines  # Main should be excluded
    finally:
        os.chdir(original_dir)


def test_list_worktrees_empty_when_none_exist(tmp_path, git_env):
    """Test that list --branches worktrees returns empty when no worktrees."""
    import sys

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    git_dir = str(repo / ".git")

    # Run from outside any git repo to avoid auto-detection interference
    outside = tmp_path / "outside"
    outside.mkdir()

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"
    env = git_env.copy()
    env["GWT_GIT_DIR"] = git_dir
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")

    original_dir = os.getcwd()
    try:
        os.chdir(outside)
        res = subprocess.run(
            [sys.executable, str(gwt_script), "list", "--branches", "worktrees"],
            env=env,
            capture_output=True,
            text=True,
        )

        assert res.returncode == 0
        assert res.stdout.strip() == ""  # Empty output
    finally:
        os.chdir(original_dir)
