import fcntl
import os
import pty
import select
import site
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

pytest.importorskip("rapidfuzz")

from gwtlib.fuzzy import ACTIONS, DEFAULT_ACTION, score_entries  # noqa: E402

GWT = Path(__file__).parent.parent / "gwt.py"
LONG_BRANCH = "a-very-long-branch-name-that-definitely-exceeds-forty-characters"


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


def _add_worktrees(repo: Path, git_dir: str, env: dict, *branches: str):
    import gwt

    base = Path(gwt.get_worktree_base(git_dir))
    for br in branches:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", str(base / br), "-b", br],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )


@pytest.fixture
def demo(tmp_path, git_env):
    """A repo with three worktrees; returns (git_dir, env, outside_dir)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo, git_env)
    git_dir = str(repo / ".git")
    _add_worktrees(repo, git_dir, git_env, "feature-alpha", "feature-beta", LONG_BRANCH)

    outside = tmp_path / "outside"
    outside.mkdir()
    env = git_env.copy()
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["GWT_GIT_DIR"] = git_dir
    # git_env repoints HOME, which would hide a user-site rapidfuzz from the
    # child interpreter; keep this interpreter's imports reachable.
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (site.getusersitepackages(), env.get("PYTHONPATH", "")) if p
    )
    return git_dir, env, outside


def _run_fz(env, cwd, args, stdin=""):
    """Run `gwt fz ...` with stdin on a pipe, i.e. the non-interactive path."""
    return subprocess.run(
        [sys.executable, str(GWT), "fz"] + args,
        env=env,
        cwd=str(cwd),
        input=stdin,
        capture_output=True,
        text=True,
    )


def test_fz_unique_query_auto_selects(demo):
    """A query matching exactly one worktree switches without a picker."""
    _git_dir, env, outside = demo
    res = _run_fz(env, outside, ["alpha"])
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().startswith("cd ")
    assert res.stdout.strip().endswith("feature-alpha")


def test_fz_non_tty_falls_back_to_best_match(demo):
    """With no terminal to draw on, the top-ranked match is used."""
    _git_dir, env, outside = demo
    res = _run_fz(env, outside, ["featurebeta"])
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().endswith("feature-beta")


def test_fz_no_match_exits_nonzero(demo):
    """An unmatchable query never emits a cd line."""
    _git_dir, env, outside = demo
    res = _run_fz(env, outside, ["qqqqqqqqqqjjjjj"])
    assert res.returncode == 1
    assert res.stdout.strip() == ""
    assert "No worktree matches" in res.stderr


def test_fz_action_path_prints_bare_path(demo):
    """The 'path' action prints a path, not a cd line the wrapper would follow."""
    _git_dir, env, outside = demo
    res = _run_fz(env, outside, ["--action", "path", "alpha"])
    assert res.returncode == 0, res.stderr
    out = res.stdout.strip()
    assert not out.startswith("cd ")
    assert out.endswith("feature-alpha")
    assert os.path.isdir(out)


def test_fz_matches_full_untruncated_branch_name(demo):
    """The picker matches on the whole branch name, past where `ls` would cut it."""
    _git_dir, env, outside = demo
    # This substring only exists beyond the 40th character of the branch name.
    res = _run_fz(env, outside, ["exceeds-forty-characters"])
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().endswith(LONG_BRANCH)


def test_fz_matches_on_path(demo):
    """A query can match the worktree path, not just the branch."""
    _git_dir, env, outside = demo
    res = _run_fz(env, outside, ["repo.gwt/feature-alpha"])
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().endswith("feature-alpha")


def test_fz_missing_rapidfuzz_is_a_clean_error(demo, monkeypatch):
    """Without the dep, fz explains itself instead of crashing."""
    git_dir, _env, _outside = demo
    import gwtlib.fuzzy as fuzzy

    monkeypatch.setattr(fuzzy, "HAS_RAPIDFUZZ", False)
    assert fuzzy.fuzzy_pick(git_dir, query="alpha") == 1


def _entry(branch, path=None, is_main=False):
    return {
        "path": path or f"/repo.gwt/{branch}",
        "head": "0" * 10,
        "branch": branch,
        "is_main": is_main,
        "locked": False,
        "prunable": False,
        "detached": False,
    }


def test_score_entries_ranks_best_match_first():
    entries = [_entry("feature-beta"), _entry("feature-alpha"), _entry("unrelated")]
    ranked = score_entries("alpha", entries)
    assert ranked[0][1]["branch"] == "feature-alpha"


def test_score_entries_drops_non_matches():
    entries = [_entry("feature-alpha"), _entry("totally-different")]
    ranked = score_entries("alpha", entries)
    assert [e["branch"] for _s, e in ranked] == ["feature-alpha"]


def test_score_entries_empty_query_keeps_all_in_list_order():
    entries = [_entry("zeta"), _entry("main", path="/repo", is_main=True)]
    ranked = score_entries("", entries)
    # Main sorts ahead of other branches, exactly like `gwt ls`.
    assert [e["branch"] for _s, e in ranked] == ["main", "zeta"]


def test_default_action_is_switch_and_cycle_covers_real_commands():
    assert DEFAULT_ACTION == "switch"
    assert ACTIONS == ["switch", "path", "tree", "remove"]


def test_ls_keeps_long_branch_names_when_the_terminal_has_room(tmp_path, monkeypatch):
    """`ls` used to cap the branch column at 40 chars even on a wide terminal."""
    import shutil as _shutil

    from gwtlib.display import format_worktree_rows

    monkeypatch.setattr(
        _shutil, "get_terminal_size", lambda fallback=None: os.terminal_size((200, 24))
    )
    git_dir = str(tmp_path / "repo.git")
    entry = _entry(LONG_BRANCH, path=str(tmp_path / "repo.gwt" / LONG_BRANCH))
    lines = format_worktree_rows([entry], git_dir=git_dir, color_mode="never")
    assert LONG_BRANCH in lines[0]
    assert "…" not in lines[0]


# --- Interactive picker (driven through a pty) ---------------------------------


def _drive(env, cwd, keys, args=("fz",), settle=1.5):
    """Run gwt in a pty with stdout on a pipe, like `output=$(gwt ...)` does.

    Returns (screen, stdout). Sends each key, then waits for the child to exit.
    """
    r, w = os.pipe()
    pid, fd = pty.fork()
    if pid == 0:  # child
        try:
            os.close(r)
            os.dup2(w, 1)
            os.close(w)
            os.chdir(str(cwd))
            os.execve(sys.executable, [sys.executable, str(GWT)] + list(args), env)
        except BaseException:
            os._exit(127)
    os.close(w)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))

    screen = b""

    def drain(timeout):
        nonlocal screen
        while select.select([fd], [], [], timeout)[0]:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            screen += chunk

    time.sleep(settle)
    for k in keys:
        drain(0.2)
        os.write(fd, k)
        time.sleep(0.4)
    drain(0.4)

    deadline = time.time() + 5
    while time.time() < deadline and os.waitpid(pid, os.WNOHANG)[0] == 0:
        drain(0.2)
    out = b""
    while select.select([r], [], [], 0.3)[0]:
        chunk = os.read(r, 65536)
        if not chunk:
            break
        out += chunk
    os.close(r)
    try:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    except (ChildProcessError, ProcessLookupError):
        pass
    os.close(fd)
    return screen.decode(errors="replace"), out.decode(errors="replace")


def test_picker_enter_switches_to_selection(demo):
    """Enter on the interactive picker emits the wrapper's cd line."""
    _git_dir, env, outside = demo
    _screen, out = _drive(env, outside, [b"\r"])
    assert out.strip().startswith("cd "), out


def test_picker_typing_refines_and_enter_switches(demo):
    """Typing narrows the list live; Enter switches to what's left."""
    _git_dir, env, outside = demo
    _screen, out = _drive(env, outside, [b"b", b"e", b"t", b"a", b"\r"])
    assert out.strip().endswith("feature-beta"), out


def test_picker_shift_tab_cycles_action_to_path(demo):
    """Shift+Tab (CSI Z) changes what Enter does, and the footer says so."""
    _git_dir, env, outside = demo
    screen, out = _drive(env, outside, [b"\x1b[Z", b"\r"])
    assert "enter: path" in screen, screen[-2000:]
    assert not out.strip().startswith("cd "), out
    assert out.strip().startswith("/"), out


def test_picker_shift_tab_wraps_around(demo):
    """Cycling through every action returns to the default."""
    _git_dir, env, outside = demo
    _screen, out = _drive(env, outside, [b"\x1b[Z"] * len(ACTIONS) + [b"\r"])
    assert out.strip().startswith("cd "), out


def test_picker_ctrl_c_cancels_without_switching(demo):
    """Ctrl+C leaves stdout empty so the shell never cds."""
    _git_dir, env, outside = demo
    _screen, out = _drive(env, outside, [b"\x03"])
    assert out.strip() == "", out


def test_picker_arrow_moves_selection(demo):
    """Down arrow selects a different worktree than Enter alone would."""
    _git_dir, env, outside = demo
    _screen, first = _drive(env, outside, [b"\r"])
    _screen2, second = _drive(env, outside, [b"\x1b[B", b"\r"])
    assert first.strip() != second.strip()
    assert second.strip().startswith("cd ")


def test_picker_shows_full_branch_name(demo):
    """The picker never abbreviates the branch name the way the `ls` table can."""
    _git_dir, env, outside = demo
    screen, _out = _drive(env, outside, [b"\x03"])
    assert LONG_BRANCH in screen, screen[-2000:]


def test_picker_footer_shows_default_action(demo):
    """The action Enter will run is always visible."""
    _git_dir, env, outside = demo
    screen, _out = _drive(env, outside, [b"\x03"])
    assert "enter: switch" in screen
    assert "shift+tab: change action" in screen
