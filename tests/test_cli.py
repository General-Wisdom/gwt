import os
import subprocess
import sys
from pathlib import Path


def _run_cli(tmp_path, args, env=None, input_bytes=None):
    cmd = [sys.executable, "gwt.py"] + args
    e = os.environ.copy()
    e["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    if env:
        e.update(env)
    return subprocess.run(cmd, env=e, input=input_bytes, capture_output=True, text=True)


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


def test_cli_repo_sets_env_line(tmp_path):
    # Provide any dir; tool will echo GWT_GIT_DIR=...
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    res = _run_cli(tmp_path, ["repo", str(repo)])
    assert res.returncode == 0
    assert "GWT_GIT_DIR=" in res.stdout


def test_cli_list_branches_only_local(tmp_path, git_env):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    # Create a branch
    subprocess.run(
        ["git", "-C", str(repo), "branch", "feature"], env=git_env, check=True
    )

    # Run from outside any git repo to avoid auto-detection interference
    outside = tmp_path / "outside"
    outside.mkdir()

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"
    env_vars = git_env.copy()
    env_vars["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env_vars["GWT_GIT_DIR"] = str(repo / ".git")

    original_dir = os.getcwd()
    try:
        os.chdir(outside)
        res = subprocess.run(
            [sys.executable, str(gwt_script), "list", "--branches", "local"],
            env=env_vars,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        # stdout prints branches
        out = res.stdout.strip().splitlines()
        # At minimum, expect current branch and feature
        assert "feature" in out
    finally:
        os.chdir(original_dir)


def test_cli_switch_no_guess_missing_branch(tmp_path, git_env):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    env = {**git_env, "GWT_GIT_DIR": str(repo / ".git")}
    # No such branch, and --no-guess
    res = _run_cli(tmp_path, ["switch", "--no-guess", "does-not-exist"], env=env)
    assert res.returncode != 0
    assert "invalid reference" in res.stderr


def test_cli_remove_flow(tmp_path, git_env):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    env = {**git_env, "GWT_GIT_DIR": str(repo / ".git")}
    # Create branch & worktree using the CLI path
    subprocess.run(
        ["git", "-C", str(repo), "branch", "feature"], env=git_env, check=True
    )
    # Use the module function to add the worktree to keep this faster
    import gwt as g

    wt_base = g.get_worktree_base(env["GWT_GIT_DIR"])
    wt_path = os.path.join(wt_base, "feature")
    g.create_worktree_for_branch("feature", env["GWT_GIT_DIR"], wt_path)

    # Run from outside any git repo to avoid auto-detection interference
    outside = tmp_path / "outside"
    outside.mkdir()

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"
    env_vars = git_env.copy()
    env_vars["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env_vars["GWT_GIT_DIR"] = str(repo / ".git")

    original_dir = os.getcwd()
    try:
        os.chdir(outside)
        # Remove via CLI and decline branch deletion
        res = subprocess.run(
            [sys.executable, str(gwt_script), "remove", "feature"],
            env=env_vars,
            input="n\n",
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert "removed" in res.stdout.lower() or "removed" in res.stderr.lower()
    finally:
        os.chdir(original_dir)


def test_auto_detect_from_repo_root(tmp_path, git_env):
    """Test auto-detection when run from repo root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)

    # No env or config set
    env = {**git_env, "XDG_CONFIG_HOME": str(tmp_path / "xdg")}

    # Get absolute path to gwt.py (in repo root, parent of tests directory)
    gwt_script = Path(__file__).parent.parent / "gwt.py"

    # Run from within repo
    original_dir = os.getcwd()
    try:
        os.chdir(repo)
        res = subprocess.run(
            [sys.executable, str(gwt_script), "list"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
    finally:
        os.chdir(original_dir)


def test_auto_detect_from_subdirectory(tmp_path, git_env):
    """Test auto-detection when run from subdirectory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    subdir = repo / "src" / "components"
    subdir.mkdir(parents=True)

    env = {**git_env, "XDG_CONFIG_HOME": str(tmp_path / "xdg")}

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"

    original_dir = os.getcwd()
    try:
        os.chdir(subdir)
        res = subprocess.run(
            [sys.executable, str(gwt_script), "list"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
    finally:
        os.chdir(original_dir)


def test_auto_detect_priority_over_env(tmp_path, git_env):
    """Test that auto-detect takes priority over GWT_GIT_DIR."""
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    _init_repo(repo_a, git_env)

    repo_b = tmp_path / "repo_b"
    repo_b.mkdir()
    _init_repo(repo_b, git_env)

    # Set env to repo B, but run from repo A
    env = {
        **git_env,
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "GWT_GIT_DIR": str(repo_b / ".git"),
    }

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"

    original_dir = os.getcwd()
    try:
        os.chdir(repo_a)
        res = subprocess.run(
            [sys.executable, str(gwt_script), "repo"],
            env=env,
            capture_output=True,
            text=True,
        )
        # Should use repo A (auto-detected), not repo B (env)
        assert str(repo_a / ".git") in res.stdout or str(repo_a) in res.stdout
        assert str(repo_b) not in res.stdout
    finally:
        os.chdir(original_dir)


def test_fallback_to_env_outside_repo(tmp_path, git_env):
    """Test fallback to env when outside any repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)

    outside = tmp_path / "outside"
    outside.mkdir()

    env = {
        **git_env,
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "GWT_GIT_DIR": str(repo / ".git"),
    }

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"

    original_dir = os.getcwd()
    try:
        os.chdir(outside)
        res = subprocess.run(
            [sys.executable, str(gwt_script), "list"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
    finally:
        os.chdir(original_dir)


def test_error_when_env_invalid(tmp_path, git_env):
    """Test E002 error when GWT_GIT_DIR points to invalid path."""
    outside = tmp_path / "outside"
    outside.mkdir()

    env = {
        **git_env,
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "GWT_GIT_DIR": "/nonexistent/path",
    }

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"

    original_dir = os.getcwd()
    try:
        os.chdir(outside)
        res = subprocess.run(
            [sys.executable, str(gwt_script), "list"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 1
        assert "E002" in res.stderr
        assert "GWT_GIT_DIR points to an invalid" in res.stderr
    finally:
        os.chdir(original_dir)


def test_error_when_no_repo_found(tmp_path, git_env):
    """Test E001 error when no repo detected and no config."""
    outside = tmp_path / "outside"
    outside.mkdir()

    env = {**git_env, "XDG_CONFIG_HOME": str(tmp_path / "xdg")}

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"

    original_dir = os.getcwd()
    try:
        os.chdir(outside)
        res = subprocess.run(
            [sys.executable, str(gwt_script), "list"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 1
        assert "E001" in res.stderr
        assert "No git repository detected" in res.stderr
    finally:
        os.chdir(original_dir)


def test_list_annotate_fish_format(tmp_path, git_env):
    """Test that list --annotate fish outputs tab-separated descriptions."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)

    # Create branches
    subprocess.run(
        ["git", "-C", str(repo), "branch", "local-only"], env=git_env, check=True
    )

    # Run from outside any git repo to avoid auto-detection interference
    outside = tmp_path / "outside"
    outside.mkdir()

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"
    env_vars = git_env.copy()
    env_vars["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env_vars["GWT_GIT_DIR"] = str(repo / ".git")

    original_dir = os.getcwd()
    try:
        os.chdir(outside)
        res = subprocess.run(
            [
                sys.executable,
                str(gwt_script),
                "list",
                "--branches",
                "all",
                "--annotate",
                "fish",
            ],
            env=env_vars,
            capture_output=True,
            text=True,
        )

        assert res.returncode == 0
        lines = res.stdout.strip().split('\n')

        # Should have tab-separated format: branch\tdescription
        for line in lines:
            assert '\t' in line
            parts = line.split('\t')
            assert len(parts) == 2
            # Check for symbols in description
            assert any(symbol in parts[1] for symbol in ['●', '○', '⊙'])
    finally:
        os.chdir(original_dir)


def test_list_annotate_bash_format(tmp_path, git_env):
    """Test that list --annotate bash outputs symbol-prefixed branches."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)

    # Create branches
    subprocess.run(
        ["git", "-C", str(repo), "branch", "local-only"], env=git_env, check=True
    )

    # Run from outside any git repo to avoid auto-detection interference
    outside = tmp_path / "outside"
    outside.mkdir()

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"
    env_vars = git_env.copy()
    env_vars["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env_vars["GWT_GIT_DIR"] = str(repo / ".git")

    original_dir = os.getcwd()
    try:
        os.chdir(outside)
        res = subprocess.run(
            [
                sys.executable,
                str(gwt_script),
                "list",
                "--branches",
                "all",
                "--annotate",
                "bash",
            ],
            env=env_vars,
            capture_output=True,
            text=True,
        )

        assert res.returncode == 0
        lines = res.stdout.strip().split('\n')

        # Should have symbol prefix: "● branch" or "○ branch" or "⊙ branch"
        for line in lines:
            assert any(line.startswith(symbol + ' ') for symbol in ['●', '○', '⊙'])
    finally:
        os.chdir(original_dir)


def test_list_annotate_none_is_plain(tmp_path, git_env):
    """Test that list without --annotate outputs plain branch names."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)

    # Run from outside any git repo to avoid auto-detection interference
    outside = tmp_path / "outside"
    outside.mkdir()

    # Get absolute path to gwt.py
    gwt_script = Path(__file__).parent.parent / "gwt.py"
    env_vars = git_env.copy()
    env_vars["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env_vars["GWT_GIT_DIR"] = str(repo / ".git")

    original_dir = os.getcwd()
    try:
        os.chdir(outside)
        res = subprocess.run(
            [sys.executable, str(gwt_script), "list", "--branches", "all"],
            env=env_vars,
            capture_output=True,
            text=True,
        )

        assert res.returncode == 0
        lines = res.stdout.strip().split('\n')

        # Should be plain branch names (no tabs, no symbols)
        for line in lines:
            assert '\t' not in line
            assert not any(symbol in line for symbol in ['●', '○', '⊙'])
    finally:
        os.chdir(original_dir)
