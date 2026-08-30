import subprocess
import shutil
from pathlib import Path

from gwtlib.gc import gc_worktrees


def _init_repo(repo: Path, env: dict):
    subprocess.run(
        ["git", "init", str(repo)], env=env, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_gc_prunes_stale_worktree_metadata(tmp_path, git_env, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    git_dir = str(repo / ".git")

    subprocess.run(["git", "-C", str(repo), "branch", "feature"], env=git_env, check=True)

    wt_path = tmp_path / "repo.gwt" / "feature"
    wt_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", f"--git-dir={git_dir}", "worktree", "add", str(wt_path), "feature"],
        env=git_env,
        check=True,
        capture_output=True,
        text=True,
    )
    shutil.rmtree(wt_path)

    gc_worktrees(git_dir, yes=True)

    out = capsys.readouterr()
    assert "Stale git worktree metadata detected" in out.err
    assert "git worktree prune" in out.err
    result = subprocess.run(
        ["git", f"--git-dir={git_dir}", "worktree", "list", "--porcelain"],
        env=git_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert str(wt_path) not in result.stdout
