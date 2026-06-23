import os
import subprocess
import sys
from pathlib import Path

import gwt
from gwtlib.tree import render_tree


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

    # feature-b is 1 behind its parent feature-a -> restack badge "↻1".
    assert "↻1" in line_b and "⚠" in line_b, line_b
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


def _commit(repo: Path, msg: str, committer_date: str | None = None):
    env = os.environ.copy()
    if committer_date:
        env["GIT_COMMITTER_DATE"] = committer_date
        env["GIT_AUTHOR_DATE"] = committer_date
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", msg],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def test_tree_hides_stale_branches(tmp_path):
    """Branches with no recent commits are hidden by default, shown with --all."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    default = _default_branch(repo)

    _git(repo, "checkout", "-b", "feature-fresh")
    _commit(repo, "fresh work")
    _git(repo, "checkout", default)
    _git(repo, "checkout", "-b", "feature-stale")
    _commit(repo, "old work", committer_date="2020-01-01T00:00:00 +0000")
    _git(repo, "checkout", default)

    git_dir = str(repo / ".git")
    _add_worktrees(repo, git_dir, "feature-fresh", "feature-stale")

    outside = tmp_path / "outside"
    outside.mkdir()

    # Default (30-day threshold): the old branch is hidden, with a footer.
    res = _run_tree(git_dir, tmp_path, cwd=outside)
    assert res.returncode == 0, res.stderr
    assert "feature-fresh" in res.stderr
    assert "feature-stale" not in res.stderr
    assert "stale branch" in res.stderr

    # --all includes the stale branch.
    res_all = _run_tree(git_dir, tmp_path, cwd=outside, extra=["--all"])
    assert res_all.returncode == 0, res_all.stderr
    assert "feature-stale" in res_all.stderr


def test_tree_behind_main_badge(tmp_path):
    """A branch the trunk advanced past shows '↓N' (behind main), not '(unrelated)'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    default = _default_branch(repo)

    _git(repo, "checkout", "-b", "feature-x")
    _commit(repo, "x1")  # 1 commit ahead of where it forked
    _git(repo, "checkout", default)
    _commit(repo, "m1")  # trunk advances after feature-x forked

    git_dir = str(repo / ".git")
    _add_worktrees(repo, git_dir, "feature-x")

    outside = tmp_path / "outside"
    outside.mkdir()
    res = _run_tree(git_dir, tmp_path, cwd=outside)
    assert res.returncode == 0, res.stderr
    line = next(ln for ln in res.stderr.splitlines() if "feature-x" in ln)
    # 1 behind main (trunk moved on); it shares history, so NOT "(unrelated)".
    assert "↓1" in line and "⚠" in line, line
    assert "(unrelated)" not in line, line


def test_tree_unrelated_only_for_disconnected_history(tmp_path):
    """An orphan branch (no shared history) is the only thing tagged (unrelated)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    default = _default_branch(repo)

    _git(repo, "checkout", "--orphan", "orphan-x")
    _commit(repo, "orphan root")  # a root commit with no ancestor in common
    _git(repo, "checkout", default)

    git_dir = str(repo / ".git")
    _add_worktrees(repo, git_dir, "orphan-x")

    outside = tmp_path / "outside"
    outside.mkdir()
    res = _run_tree(git_dir, tmp_path, cwd=outside, extra=["--all"])
    assert res.returncode == 0, res.stderr
    line = next(ln for ln in res.stderr.splitlines() if "orphan-x" in ln)
    assert "(unrelated)" in line, line


def test_tree_long_flag_toggles_path(tmp_path):
    """The worktree path/SHA columns are hidden unless -l is passed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    default = _default_branch(repo)

    _git(repo, "checkout", "-b", "feature-a")
    _commit(repo, "a1")
    _git(repo, "checkout", default)

    git_dir = str(repo / ".git")
    _add_worktrees(repo, git_dir, "feature-a")

    outside = tmp_path / "outside"
    outside.mkdir()

    res = _run_tree(git_dir, tmp_path, cwd=outside)
    assert res.returncode == 0, res.stderr
    assert "feature-a" in res.stderr
    assert "repo.gwt" not in res.stderr  # path hidden by default

    res_long = _run_tree(git_dir, tmp_path, cwd=outside, extra=["-l"])
    assert res_long.returncode == 0, res_long.stderr
    assert "repo.gwt" in res_long.stderr  # path shown with -l


def _node(branch, *, is_root=False, pr=None, ahead=0, children=None):
    return {
        "branch": branch,
        "sha": "0" * 40,
        "head": "0" * 10,
        "path": None,
        "is_root": is_root,
        "unrelated": False,
        "ahead": None if is_root else ahead,
        "behind": None if is_root else 0,
        "behind_main": None if is_root else 0,
        "parent_is_root": None if is_root else True,
        "age_days": 0.0,
        "pr": pr,
        "children": children or [],
    }


def test_render_pr_column():
    """The PR column appears only when show_pr is set."""
    root = _node(
        "main", is_root=True, children=[_node("feature-a", pr="OPEN", ahead=2)]
    )

    with_pr = "\n".join(render_tree(root, "", color="never", show_pr=True))
    assert "OPEN" in with_pr

    without_pr = "\n".join(render_tree(root, "", color="never", show_pr=False))
    assert "OPEN" not in without_pr


def test_tree_pr_flag_runs_without_gh(tmp_path):
    """`tree --pr` degrades gracefully (no PR remote / no gh) and still renders."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    default = _default_branch(repo)

    _git(repo, "checkout", "-b", "feature-a")
    _commit(repo, "a1")
    _git(repo, "checkout", default)

    git_dir = str(repo / ".git")
    _add_worktrees(repo, git_dir, "feature-a")

    outside = tmp_path / "outside"
    outside.mkdir()
    res = _run_tree(git_dir, tmp_path, cwd=outside, extra=["--pr"])
    assert res.returncode == 0, res.stderr
    assert "feature-a" in res.stderr


def test_bare_gwt_defaults_to_tree(tmp_path):
    """Running `gwt` with no subcommand renders the tree, not the flat list."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    default = _default_branch(repo)

    _git(repo, "checkout", "-b", "feature-a")
    _commit(repo, "a1")
    _git(repo, "checkout", default)

    git_dir = str(repo / ".git")
    _add_worktrees(repo, git_dir, "feature-a")

    outside = tmp_path / "outside"
    outside.mkdir()
    gwt_script = Path(__file__).parent.parent / "gwt.py"
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["GWT_GIT_DIR"] = git_dir
    res = subprocess.run(
        [sys.executable, str(gwt_script)],
        env=env,
        cwd=str(outside),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    # The tree legend is unique to the tree view (the flat list has no such line).
    assert "this PR" in res.stderr
    assert "feature-a" in res.stderr
