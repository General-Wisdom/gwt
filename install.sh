#!/usr/bin/env bash
# shellcheck disable=SC2016

set -euo pipefail

# The printed shell setup examples deliberately leave $HOME and $PATH unexpanded.
# Install the Python sources and shell functions without choosing how the user
# manages their shell environment.
BIN_DIR="$HOME/.local/bin"
APPDIR="${GWT_APPDIR:-${XDG_DATA_HOME:-$HOME/.local/share}/gwt}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v uv >/dev/null 2>&1; then
    echo "Found uv; gwt will run in an isolated environment."
elif command -v python3 >/dev/null 2>&1; then
    echo "uv not found; gwt will use system Python."
    echo "Install uv for managed dependencies, or install the Python dependencies listed in gwt.py."
else
    echo "ERROR: gwt requires uv or python3." >&2
    exit 1
fi

mkdir -p "$BIN_DIR" "$APPDIR"

echo "Installing Python sources to $APPDIR..."
# Remove stale modules from an earlier version before copying the current package.
rm -rf "$APPDIR/gwtlib"
cp "$SCRIPT_DIR/gwt.py" "$APPDIR/gwt.py"
cp -r "$SCRIPT_DIR/gwtlib" "$APPDIR/gwtlib"
chmod +x "$APPDIR/gwt.py"

echo "Installing shell functions to $BIN_DIR..."
cp "$SCRIPT_DIR/gwt.sh" "$BIN_DIR/gwt.sh"
cp "$SCRIPT_DIR/gwt.fish" "$BIN_DIR/gwt.fish"
chmod +x "$BIN_DIR/gwt.sh" "$BIN_DIR/gwt.fish"

echo
echo "Installation complete. Add the following to your shell setup where appropriate:"
echo "Bash/Zsh:"
if [[ -n "${GWT_APPDIR:-}${XDG_DATA_HOME:-}" ]]; then
    # These escapes keep a custom path literal when pasted into either shell.
    appdir_quoted=${APPDIR//\/\\}
    appdir_quoted=${appdir_quoted//\"/\\\"}
    appdir_quoted=${appdir_quoted//\$/\\$}
    appdir_quoted=${appdir_quoted//\`/\\\`}
    printf '  export GWT_APPDIR="%s"\n' "$appdir_quoted"
fi
echo '  source "$HOME/.local/bin/gwt.sh"'
echo '  export GWT_GIT_DIR=/path/to/repo.git  # Optional default repository'
echo "Fish:"
if [[ -n "${GWT_APPDIR:-}${XDG_DATA_HOME:-}" ]]; then
    printf '  set -gx GWT_APPDIR "%s"\n' "$appdir_quoted"
fi
echo '  source "$HOME/.local/bin/gwt.fish"'
echo '  set -gx GWT_GIT_DIR /path/to/repo.git  # Optional default repository'

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        echo
        echo "Your PATH does not include $BIN_DIR. Add it in your shell setup if needed:"
        echo '  Bash/Zsh: export PATH="$HOME/.local/bin:$PATH"'
        echo '  Fish: fish_add_path "$HOME/.local/bin"'
        ;;
esac

echo
echo "Open a new shell or source the integration file, then run: gwt --help"
