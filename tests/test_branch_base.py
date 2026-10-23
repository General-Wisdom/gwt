import os
import subprocess
import sys
from pathlib import Path

from gwtlib.base import get_base_ref
from gwtlib.gc import get_worktree_info_list
from gwtlib.tree import build_stack_tree


def _git(path, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _repo_with_origin(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(origin)],
        check=True,
        capture_output=True,
        text=True,
    )
    repo = tmp_path / "repo"
    _git(tmp_path, "clone", str(origin), str(repo))
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "tracked.txt").write_text("initial\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "push", "-u", "origin", "main")

    updater = tmp_path / "updater"
    _git(tmp_path, "clone", str(origin), str(updater))
    _git(updater, "config", "user.name", "Test User")
    _git(updater, "config", "user.email", "test@example.com")
    return repo, updater, origin


def _advance_origin(updater, message):
    _git(updater, "commit", "--allow-empty", "-m", message)
    _git(updater, "push", "origin", "main")
    return _git(updater, "rev-parse", "HEAD").stdout.strip()


def _run_gwt(tmp_path, repo, *args):
    env = os.environ.copy()
    env["GWT_GIT_DIR"] = str(repo / ".git")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    script = Path(__file__).parent.parent / "gwt.py"
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )


def test_new_branch_fetches_origin_with_dirty_main_and_uses_remote_base(
    tmp_path, monkeypatch
):
    repo, updater, _ = _repo_with_origin(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    # A stale local origin/HEAD must be refreshed before choosing the base.
    _git(
        repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/old-main"
    )
    original_main = _git(repo, "rev-parse", "main").stdout.strip()
    new_tip = _advance_origin(updater, "remote advance")
    (repo / "tracked.txt").write_text("dirty main\n")

    result = _run_gwt(tmp_path, repo, "switch", "-c", "feature")
    assert result.returncode == 0, result.stderr
    assert get_base_ref(str(repo / ".git")) == "origin/main"
    assert _git(repo, "rev-parse", "feature").stdout.strip() == new_tip
    assert _git(repo, "rev-parse", "main").stdout.strip() == original_main
    assert (repo / "tracked.txt").read_text() == "dirty main\n"
    assert (
        _git(repo, "config", "--get", "branch.feature.remote", check=False).returncode
        == 1
    )

    root, notes = build_stack_tree(str(repo / ".git"))
    assert root is not None, notes
    assert root["branch"] == "origin/main"
    feature = next(
        wt
        for wt in get_worktree_info_list(str(repo / ".git"))
        if wt.branch == "feature"
    )
    assert feature.is_merged


def test_automatic_base_follows_remote_default_even_when_main_exists(tmp_path):
    repo, updater, origin = _repo_with_origin(tmp_path)
    _git(updater, "checkout", "-b", "master")
    _git(updater, "commit", "--allow-empty", "-m", "master work")
    master_tip = _git(updater, "rev-parse", "HEAD").stdout.strip()
    _git(updater, "push", "origin", "master")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/master")

    result = _run_gwt(tmp_path, repo, "switch", "-c", "from-default")
    assert result.returncode == 0, result.stderr
    assert get_base_ref(str(repo / ".git")) == "origin/master"
    assert _git(repo, "rev-parse", "from-default").stdout.strip() == master_tip

    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    cached = _run_gwt(tmp_path, repo, "switch", "-c", "cached-default", "--no-fetch")
    assert cached.returncode == 0, cached.stderr
    assert _git(repo, "rev-parse", "cached-default").stdout.strip() == master_tip


def test_gc_fetches_before_merge_checks_and_fails_safe(tmp_path, capsys):
    repo, updater, origin = _repo_with_origin(tmp_path)
    result = _run_gwt(tmp_path, repo, "switch", "-c", "feature")
    assert result.returncode == 0, result.stderr
    old_tip = _git(repo, "rev-parse", "origin/main").stdout.strip()

    _git(repo, "remote", "set-url", "origin", str(origin) + ".missing")
    feature = next(
        wt
        for wt in get_worktree_info_list(str(repo / ".git"))
        if wt.branch == "feature"
    )
    assert not feature.is_merged
    assert "Could not fetch 'origin'" in capsys.readouterr().err

    _git(repo, "remote", "set-url", "origin", str(origin))
    _git(updater, "checkout", "--orphan", "replacement")
    _git(updater, "commit", "--allow-empty", "-m", "replacement root")
    _git(updater, "push", "--force", "origin", "HEAD:main")
    feature = next(
        wt
        for wt in get_worktree_info_list(str(repo / ".git"))
        if wt.branch == "feature"
    )
    assert _git(repo, "rev-parse", "origin/main").stdout.strip() != old_tip
    assert not feature.is_merged


def test_configured_base_override_and_offline_mode(tmp_path):
    repo, updater, origin = _repo_with_origin(tmp_path)
    first_remote_tip = _advance_origin(updater, "first remote advance")

    config_dir = tmp_path / "xdg" / "gwt"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        f'[repos."{repo / ".git"}"]\nbase_ref = "main"\n'
    )
    result = _run_gwt(tmp_path, repo, "switch", "-c", "local-base")
    assert result.returncode == 0, result.stderr
    assert (
        _git(repo, "rev-parse", "local-base").stdout.strip()
        == _git(repo, "rev-parse", "main").stdout.strip()
    )

    result = _run_gwt(
        tmp_path, repo, "switch", "-c", "remote-base", "--from", "origin/main"
    )
    assert result.returncode == 0, result.stderr
    assert _git(repo, "rev-parse", "remote-base").stdout.strip() == first_remote_tip

    checked_out = _run_gwt(
        tmp_path, repo, "switch", "-C", "local-base", "--from", "origin/main"
    )
    assert checked_out.returncode != 0
    assert "checked out in a worktree" in checked_out.stderr
    _git(repo, "worktree", "remove", str(tmp_path / "repo.gwt" / "local-base"))
    reset = _run_gwt(
        tmp_path, repo, "switch", "-C", "local-base", "--from", "origin/main"
    )
    assert reset.returncode == 0, reset.stderr
    assert _git(repo, "rev-parse", "local-base").stdout.strip() == first_remote_tip

    _advance_origin(updater, "second remote advance")
    result = _run_gwt(
        tmp_path,
        repo,
        "switch",
        "-c",
        "cached-base",
        "--from",
        "origin/main",
        "--no-fetch",
    )
    assert result.returncode == 0, result.stderr
    assert _git(repo, "rev-parse", "cached-base").stdout.strip() == first_remote_tip

    _git(repo, "remote", "set-url", "origin", str(origin) + ".missing")
    result = _run_gwt(
        tmp_path, repo, "switch", "-c", "must-fail", "--from", "origin/main"
    )
    assert result.returncode != 0
    assert "Could not fetch 'origin'" in result.stderr
    assert (
        _git(
            repo, "show-ref", "--verify", "refs/heads/must-fail", check=False
        ).returncode
        != 0
    )


def test_from_requires_branch_creation(tmp_path):
    repo, _, _ = _repo_with_origin(tmp_path)
    result = _run_gwt(tmp_path, repo, "switch", "feature", "--from", "main")
    assert result.returncode != 0
    assert "require -c or -C" in result.stderr
