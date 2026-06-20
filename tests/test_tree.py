import os
import subprocess
import sys
from pathlib import Path

import gwt


def _git(repo: Path, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path):
    subprocess.run(
        ["git", "init", str(repo)], check=True, capture_output=True, text=True
    )
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "commit", "--allow-empty", "-m", "init")


def _default_branch(repo: Path) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _add_worktrees(repo: Path, git_dir: str, *branches: str):
    base = Path(gwt.get_worktree_base(git_dir))
    for br in branches:
        _git(repo, "worktree", "add", str(base / br), br)


def _run_tree(git_dir: str, tmp_path: Path, cwd: Path, extra=None):
    gwt_script = Path(__file__).parent.parent / "gwt.py"
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["GWT_GIT_DIR"] = git_dir
    args = [sys.executable, str(gwt_script), "tree", "--color", "never"]
    if extra:
        args += extra
    return subprocess.run(args, env=env, cwd=str(cwd), capture_output=True, text=True)


def test_tree_shows_stacked_structure(tmp_path):
    """main -> feature-a -> feature-b, with feature-c branched off main directly."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    default = _default_branch(repo)

    _git(repo, "checkout", "-b", "feature-a")
    _git(repo, "commit", "--allow-empty", "-m", "a1")
    _git(repo, "commit", "--allow-empty", "-m", "a2")
    _git(repo, "checkout", "-b", "feature-b")  # off feature-a's tip
    _git(repo, "commit", "--allow-empty", "-m", "b1")
    _git(repo, "checkout", default)
    _git(repo, "checkout", "-b", "feature-c")  # off main
    _git(repo, "commit", "--allow-empty", "-m", "c1")
    _git(repo, "checkout", default)

    git_dir = str(repo / ".git")
    _add_worktrees(repo, git_dir, "feature-a", "feature-b", "feature-c")

    outside = tmp_path / "outside"
    outside.mkdir()
    res = _run_tree(git_dir, tmp_path, cwd=outside)

    assert res.returncode == 0, res.stderr
    err = res.stderr
    assert "(root)" in err
    for br in ("feature-a", "feature-b", "feature-c"):
        assert br in err, err

    lines = err.splitlines()
    line_a = next(ln for ln in lines if "feature-a" in ln)
    line_b = next(ln for ln in lines if "feature-b" in ln)
    line_c = next(ln for ln in lines if "feature-c" in ln)

    # feature-b is stacked on feature-a -> rendered more deeply indented.
    assert line_b.index("feature-b") > line_a.index("feature-a")
    # feature-c is a sibling of feature-a (both direct children of the root).
    assert line_c.index("feature-c") == line_a.index("feature-a")


def test_tree_excludes_worktree_with_no_commits(tmp_path):
    """A worktree sitting exactly on the trunk is not shown."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    default = _default_branch(repo)

    _git(repo, "checkout", "-b", "feature-a")
    _git(repo, "commit", "--allow-empty", "-m", "a1")
    _git(repo, "checkout", default)
    _git(repo, "branch", "ontrunk")  # points at the trunk tip: 0 commits ahead

    git_dir = str(repo / ".git")
    _add_worktrees(repo, git_dir, "feature-a", "ontrunk")

    outside = tmp_path / "outside"
    outside.mkdir()
    res = _run_tree(git_dir, tmp_path, cwd=outside)

    assert res.returncode == 0, res.stderr
    assert "feature-a" in res.stderr
    assert "ontrunk" not in res.stderr


def test_tree_marks_branch_behind_parent(tmp_path):
    """feature-b forked from feature-a, then feature-a advanced -> behind marker."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    default = _default_branch(repo)

    _git(repo, "checkout", "-b", "feature-a")
    _git(repo, "commit", "--allow-empty", "-m", "a1")
    _git(repo, "checkout", "-b", "feature-b")  # off feature-a @ a1
    _git(repo, "commit", "--allow-empty", "-m", "b1")
    _git(repo, "checkout", "feature-a")
    _git(repo, "commit", "--allow-empty", "-m", "a2")  # feature-a moves ahead
    _git(repo, "checkout", default)

    git_dir = str(repo / ".git")
    _add_worktrees(repo, git_dir, "feature-a", "feature-b")

    outside = tmp_path / "outside"
    outside.mkdir()
    res = _run_tree(git_dir, tmp_path, cwd=outside)

    assert res.returncode == 0, res.stderr
    lines = res.stderr.splitlines()
    line_a = next(ln for ln in lines if "feature-a" in ln)
    line_b = next(ln for ln in lines if "feature-b" in ln)

    # feature-b is 1 ahead / 1 behind its parent feature-a.
    assert "-1" in line_b and "⚠" in line_b, line_b
    assert line_b.index("feature-b") > line_a.index("feature-a")


def test_tree_no_candidates_message(tmp_path):
    """With no worktrees ahead of the trunk, a friendly message is printed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    git_dir = str(repo / ".git")
    outside = tmp_path / "outside"
    outside.mkdir()
    res = _run_tree(git_dir, tmp_path, cwd=outside)

    assert res.returncode == 0, res.stderr
    assert "No worktrees with commits ahead" in res.stderr
