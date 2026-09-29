import os
import subprocess
from pathlib import Path

from gwtlib.gc import get_worktree_mtime


def _git(repo: Path, git_env, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
    )


def _init_repo(repo: Path, git_env):
    repo.mkdir()
    _git(repo, git_env, "init")
    (repo / ".gitignore").write_text("ignored/\n*.ignored\n")
    (repo / "tracked.txt").write_text("tracked\n")
    _git(repo, git_env, "add", ".gitignore", "tracked.txt")
    _git(repo, git_env, "commit", "-m", "initial")


def test_worktree_mtime_excludes_ignored_files(tmp_path, git_env):
    repo = tmp_path / "repo"
    _init_repo(repo, git_env)

    old = 1_000_000_000
    recent = 1_500_000_000
    os.utime(repo / ".gitignore", (old, old))
    os.utime(repo / "tracked.txt", (old, old))

    ignored_dir = repo / "ignored"
    ignored_dir.mkdir()
    ignored_file = ignored_dir / "artifact.bin"
    ignored_file.write_text("generated\n")
    os.utime(ignored_file, (recent, recent))

    ignored_by_pattern = repo / "cache.ignored"
    ignored_by_pattern.write_text("generated\n")
    os.utime(ignored_by_pattern, (recent, recent))

    assert get_worktree_mtime(str(repo)) == old


def test_worktree_mtime_includes_untracked_nonignored_files(tmp_path, git_env):
    repo = tmp_path / "repo"
    _init_repo(repo, git_env)

    old = 1_000_000_000
    recent = 1_500_000_000
    os.utime(repo / ".gitignore", (old, old))
    os.utime(repo / "tracked.txt", (old, old))

    untracked = repo / "notes.txt"
    untracked.write_text("work in progress\n")
    os.utime(untracked, (recent, recent))

    assert get_worktree_mtime(str(repo)) == recent


def test_worktree_mtime_includes_tracked_files_that_match_ignore_rule(
    tmp_path, git_env
):
    repo = tmp_path / "repo"
    _init_repo(repo, git_env)

    tracked_ignored = repo / "tracked.ignored"
    tracked_ignored.write_text("tracked before ignore\n")
    _git(repo, git_env, "add", "-f", "tracked.ignored")
    _git(repo, git_env, "commit", "-m", "add tracked ignored file")

    old = 1_000_000_000
    recent = 1_500_000_000
    os.utime(repo / ".gitignore", (old, old))
    os.utime(repo / "tracked.txt", (old, old))
    os.utime(tracked_ignored, (recent, recent))

    assert get_worktree_mtime(str(repo)) == recent
