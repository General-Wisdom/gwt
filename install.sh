#!/bin/bash

set -e

# Default paths
BIN_DIR="${HOME}/.local/bin"
APPDIR="${XDG_DATA_HOME:-$HOME/.local/share}/gwt"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

# Create directories
mkdir -p "$BIN_DIR" "$APPDIR"

# Check if ~/.local/bin is in PATH
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
    echo "WARNING: $HOME/.local/bin is not in your PATH"
    echo "You need to add it to use gwt. Add this to your shell config:"
    # shellcheck disable=SC2016
    echo '  export PATH="$HOME/.local/bin:$PATH"  # for bash/zsh'
    # shellcheck disable=SC2016
    echo '  fish_add_path $HOME/.local/bin         # for fish'
    echo ""
fi

# Detect user's shell
USER_SHELL=$(basename "$SHELL")
echo "Detected shell: $USER_SHELL"

# Check if uv is available
if command -v uv >/dev/null 2>&1; then
    echo "✓ Found uv - will run gwt in an isolated environment"
else
    echo "ℹ uv not found - falling back to system Python (python3)"
    echo "  For best performance and isolation, install uv: https://github.com/astral-sh/uv"
    echo ""

    # Check for Python packages only if uv is not available
    echo "Checking for required Python packages (Python <3.11)..."
    if ! python3 -c "import tomli, tomli_w" 2>/dev/null; then
        echo "  WARNING: 'tomli' and 'tomli-w' not installed for system Python"
        echo "  Install with: pip install tomli tomli-w"
        echo ""
    fi
    if ! python3 -c "import rapidfuzz" 2>/dev/null; then
        echo "  WARNING: 'rapidfuzz' not installed for system Python"
        echo "  Install with: pip install rapidfuzz"
        echo ""
    fi
fi

# Clean up old installation if present (gwt.py in ~/.local/bin from pre-AppDir versions)
if [ -f "$BIN_DIR/gwt.py" ]; then
    echo "Removing old gwt.py from $BIN_DIR (now using AppDir layout)..."
    rm -f "$BIN_DIR/gwt.py"
fi

# Copy Python sources to AppDir
echo "Installing core to $APPDIR..."
# Ensure a clean package directory
rm -rf "$APPDIR/gwtlib"
cp "$SCRIPT_DIR/gwt.py" "$APPDIR/gwt.py"
cp -r "$SCRIPT_DIR/gwtlib" "$APPDIR/gwtlib"
chmod +x "$APPDIR/gwt.py"

# Sanity check
if [ ! -f "$APPDIR/gwt.py" ] || [ ! -d "$APPDIR/gwtlib" ]; then
    echo "ERROR: Failed to install Python sources to $APPDIR" >&2
    exit 1
fi

# Install wrappers to ~/.local/bin
echo "Installing shell wrappers to $BIN_DIR..."
cp "$SCRIPT_DIR/gwt.sh" "$BIN_DIR/gwt.sh"
chmod +x "$BIN_DIR/gwt.sh"

if [ "$USER_SHELL" = "fish" ] || [ -d "$HOME/.config/fish" ]; then
    if [ -f "$SCRIPT_DIR/gwt.fish" ]; then
        cp "$SCRIPT_DIR/gwt.fish" "$BIN_DIR/gwt.fish"
        chmod +x "$BIN_DIR/gwt.fish"
        echo "✓ Fish shell support installed"
    fi
fi

# Set up shell integration
echo ""
echo "Setting up shell integration..."

case "$USER_SHELL" in
    fish)
        FISH_CONFIG="$HOME/.config/fish/config.fish"
        
        # Ensure fish config exists
        mkdir -p "$(dirname "$FISH_CONFIG")"
        touch "$FISH_CONFIG"
        
        # Check if PATH needs to be updated for fish
        if ! grep -q "$HOME/.local/bin" "$FISH_CONFIG" 2>/dev/null; then
            {
                echo ""
                echo "# Add ~/.local/bin to PATH"
                echo "fish_add_path $HOME/.local/bin"
            } >> "$FISH_CONFIG"
            echo "✓ Added ~/.local/bin to fish PATH"
        fi
        
        # Add gwt source
        if ! grep -q "gwt.fish" "$FISH_CONFIG" 2>/dev/null; then
            {
                echo ""
                echo "# GWT setup"
                echo "source \"$BIN_DIR/gwt.fish\""
            } >> "$FISH_CONFIG"
            echo "✓ Fish integration added"
        else
            echo "✓ Fish integration already configured"
        fi
        ;;
        
    bash|zsh)
        RC_FILE="$HOME/.${USER_SHELL}rc"
        
        # Check if PATH needs to be updated
        if ! grep -q "$HOME/.local/bin" "$RC_FILE" 2>/dev/null; then
            {
                echo ""
                echo "# Add ~/.local/bin to PATH"
                # shellcheck disable=SC2016
                echo 'export PATH="$HOME/.local/bin:$PATH"'
            } >> "$RC_FILE"
            echo "✓ Added ~/.local/bin to PATH"
        fi
        
        # Add gwt source
        if ! grep -q "gwt.sh" "$RC_FILE" 2>/dev/null; then
            {
                echo ""
                echo "# GWT setup"
                echo "source \"$BIN_DIR/gwt.sh\""
            } >> "$RC_FILE"
            echo "✓ Bash/Zsh integration added"
        else
            echo "✓ Bash/Zsh integration already configured"
        fi
        ;;
        
    *)
        echo "Unknown shell: $USER_SHELL"
        echo "Please manually add to your shell config:"
        echo "  source $BIN_DIR/gwt.sh   # for bash/zsh"
        echo "  source $BIN_DIR/gwt.fish  # for fish"
        ;;
esac

# Create config directory
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/gwt"
CONFIG_FILE="$CONFIG_DIR/config.toml"

mkdir -p "$CONFIG_DIR"

if [ ! -f "$CONFIG_FILE" ]; then
    echo ""
    echo "Creating default config file..."
    cat > "$CONFIG_FILE" << 'EOL'
# GWT Configuration

# Default repository to use if GWT_GIT_DIR env var isn't set
# default_repo = "/path/to/your/repo.git"

# Repository-specific post-create commands
# [repos."/path/to/your/repo.git"]
# post_create_commands = [
#   "npm install",
#   "echo 'Worktree setup complete!'"
# ]
EOL
    echo "✓ Config file created at $CONFIG_FILE"
fi

# Ask for default GWT_GIT_DIR
echo ""
echo "Enter default GWT_GIT_DIR (optional, press Enter to skip):"
read -r default_git_dir

if [ -n "$default_git_dir" ]; then
    # Expand tilde to home directory
    default_git_dir="${default_git_dir/#\~/$HOME}"
    
    # Check if it's a directory and append .git if needed
    if [ -d "$default_git_dir" ]; then
        # Check if it's a bare repo or needs .git appended
        if [ -d "$default_git_dir/.git" ]; then
            default_git_dir="$default_git_dir/.git"
        elif [ ! -f "$default_git_dir/HEAD" ]; then
            echo "Warning: $default_git_dir doesn't appear to be a git repository"
        fi
    fi
    
    # Update shell config
    case "$USER_SHELL" in
        fish)
            echo "set -gx GWT_GIT_DIR \"$default_git_dir\"" >> "$FISH_CONFIG"
            ;;
        bash|zsh)
            echo "export GWT_GIT_DIR=\"$default_git_dir\"" >> "$RC_FILE"
            ;;
    esac
    echo "✓ Default GWT_GIT_DIR set to: $default_git_dir"
fi

echo ""
echo "========================================="
echo "✅ Installation complete!"
echo ""
echo "Please reload your shell or run:"
case "$USER_SHELL" in
    fish)
        echo "  source ~/.config/fish/config.fish"
        ;;
    bash|zsh)
        echo "  source ~/.${USER_SHELL}rc"
        ;;
esac
echo ""
echo "Then you can use: gwt --help"
echo "========================================="