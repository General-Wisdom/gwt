"""Check that installation leaves user shell settings under user control."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("shell", "rc_file", "source_hint"),
    [
        ("bash", ".bashrc", 'source "$HOME/.local/bin/gwt.sh"'),
        ("zsh", ".zshrc", 'source "$HOME/.local/bin/gwt.sh"'),
        ("fish", ".config/fish/config.fish", 'source "$HOME/.local/bin/gwt.fish"'),
    ],
)
def test_install_prints_setup_without_editing_shell_files(
    tmp_path, shell, rc_file, source_hint
):
    home = tmp_path / "home"
    home.mkdir()
    rc_path = home / rc_file
    rc_path.parent.mkdir(parents=True, exist_ok=True)
    rc_path.write_text("# existing shell configuration\n")

    # A similarly named PATH entry must not count as ~/.local/bin.
    similar_bin = home / ".local/bin-extra"
    similar_bin.mkdir(parents=True)
    legacy_script = home / ".local/bin/gwt.py"
    legacy_script.parent.mkdir(parents=True)
    legacy_script.write_text("keep this file\n")

    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "SHELL": f"/bin/{shell}",
        "PATH": f"{similar_bin}:{os.environ['PATH']}",
    }
    env.pop("GWT_APPDIR", None)

    result = subprocess.run(
        ["bash", str(ROOT / "install.sh")],
        cwd=ROOT,
        env=env,
        input="",
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )

    assert rc_path.read_text() == "# existing shell configuration\n"
    assert legacy_script.read_text() == "keep this file\n"
    assert not (tmp_path / "config/gwt").exists()
    assert (tmp_path / "data/gwt/gwt.py").is_file()
    assert (tmp_path / "data/gwt/gwtlib").is_dir()
    assert (home / ".local/bin/gwt.sh").is_file()
    assert (home / ".local/bin/gwt.fish").is_file()
    assert source_hint in result.stdout
    assert "Bash/Zsh:\n" in result.stdout
    assert "Fish:\n" in result.stdout
    assert 'source "$HOME/.local/bin/gwt.sh"' in result.stdout
    assert 'source "$HOME/.local/bin/gwt.fish"' in result.stdout
    assert 'Bash/Zsh: export PATH="$HOME/.local/bin:$PATH"' in result.stdout
    assert 'Fish: fish_add_path "$HOME/.local/bin"' in result.stdout
    assert f'export GWT_APPDIR="{tmp_path}/data/gwt"' in result.stdout
    assert f'set -gx GWT_APPDIR "{tmp_path}/data/gwt"' in result.stdout
    assert "GWT_GIT_DIR" in result.stdout
    assert f"PATH does not include {home}/.local/bin" in result.stdout

    # Reinstalling also leaves shell files alone and recognizes an exact PATH entry.
    env["PATH"] = f"{home}/.local/bin:{env['PATH']}"
    rerun = subprocess.run(
        ["bash", str(ROOT / "install.sh")],
        cwd=ROOT,
        env=env,
        input="",
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert rc_path.read_text() == "# existing shell configuration\n"
    assert "PATH does not include" not in rerun.stdout


def test_custom_appdir_setup_uses_installed_location(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    appdir = tmp_path / 'custom $data "quoted"'
    env = {
        **os.environ,
        "HOME": str(home),
        "GWT_APPDIR": str(appdir),
        "XDG_DATA_HOME": str(tmp_path / "different-data"),
        "SHELL": "/bin/fish",
    }

    result = subprocess.run(
        ["bash", str(ROOT / "install.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )

    assert (appdir / "gwt.py").is_file()
    bash_setting = next(
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("  export GWT_APPDIR=")
    )
    fish_setting = next(
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("  set -gx GWT_APPDIR ")
    )
    assert '\\$data \\"quoted\\"' in bash_setting
    assert fish_setting == bash_setting.replace(
        "export GWT_APPDIR=", "set -gx GWT_APPDIR "
    )

    # The printed setting must work after the install-time override is gone.
    env.pop("GWT_APPDIR")
    check = subprocess.run(
        [
            "bash",
            "-c",
            f'{bash_setting}\nsource "$HOME/.local/bin/gwt.sh"\nprintf "%s" "$APPDIR"',
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert check.stdout == str(appdir)
