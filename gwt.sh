#!/usr/bin/env bash

# Resolve AppDir per XDG, allow override via GWT_APPDIR
APPDIR="${GWT_APPDIR:-${XDG_DATA_HOME:-$HOME/.local/share}/gwt}"
PYTHON_SCRIPT="$APPDIR/gwt.py"

# Select runner: uv (preferred) or python3 fallback
_gwt_init_runner() {
    if command -v uv >/dev/null 2>&1; then
        # We'll call: uv run --script "$PYTHON_SCRIPT" ...
        GWT_CMD=(uv run --script "$PYTHON_SCRIPT")
    else
        GWT_CMD=(python3 "$PYTHON_SCRIPT")
    fi
}
_gwt_init_runner

# Runner helper
_gwt_run() {
    "${GWT_CMD[@]}" "$@"
}

# Zsh compatibility: enable bash-style completion if under zsh
if [ -n "${ZSH_VERSION:-}" ]; then
    autoload -Uz bashcompinit 2>/dev/null || true
    bashcompinit 2>/dev/null || true
fi

# Verify Python script exists
if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo "gwt: AppDir not found or incomplete at: $APPDIR" >&2
    echo "Re-run ./install.sh to install gwt." >&2
    # shellcheck disable=SC2317
    return 1 2>/dev/null || exit 1
fi

_gwt_get_branches() {
    # Function to get list of branch names
    # Default to "all" branches for comprehensive completion
    local output result
    output=$(_gwt_run list --branches all 2>&1)
    result=$?

    if [ $result -eq 0 ]; then
        echo "$output"
    else
        # Fallback to worktrees only if all branches fails
        _gwt_run list --branches worktrees 2>/dev/null || true
    fi
}

_gwt_completions() {
    local cur prev commands branches _gwt_get_branches_output
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    commands="repo switch s list ls l remove rm gc"

    # Don't use file completion as a fallback
    compopt -o nospace

    # For the first argument (position 1), complete with commands
    if [[ ${COMP_CWORD} -eq 1 ]]; then
        # Add space after command completion
        compopt +o nospace
        mapfile -t COMPREPLY < <(compgen -W "${commands}" -- "${cur}")
        return 0
    fi

    # For position 2, handle specific subcommand completions
    if [[ ${COMP_CWORD} -eq 2 ]]; then
        case "${prev}" in
            repo)
                # Use directory completion for repo command
                compopt -o filenames -o nospace
                mapfile -t COMPREPLY < <(compgen -d -- "${cur}")
                return 0
                ;;
            switch|s)
                # All branches for switch
                _gwt_get_branches_output=$(_gwt_run list --branches all --annotate bash 2>/dev/null)
                if [ -n "$_gwt_get_branches_output" ]; then
                    branches=$(echo "$_gwt_get_branches_output" | tr '\n' ' ')
                    mapfile -t COMPREPLY < <(compgen -W "${branches}" -- "${cur}")
                fi
                return 0
                ;;
            remove|rm)
                # Only worktrees (excluding main)
                _gwt_get_branches_output=$(_gwt_run list --branches worktrees --annotate bash 2>/dev/null)
                if [ -n "$_gwt_get_branches_output" ]; then
                    branches=$(echo "$_gwt_get_branches_output" | tr '\n' ' ')
                    mapfile -t COMPREPLY < <(compgen -W "${branches}" -- "${cur}")
                fi
                return 0
                ;;
            list|ls|l)
                # No completions needed for list commands
                return 0
                ;;
            *)
                # No completions for unknown commands
                return 0
                ;;
        esac
    fi

    # Default: no completions for other positions
    return 0
}

# Function to handle all gwt operations
gwt() {
    # Helper: strip leading icons (●, ○, ⊙) and spaces from a single token
    _gwt_strip_visual() {
        local arg="$1"
        # Remove optional icon + space
        arg="${arg/#● /}"
        arg="${arg/#○ /}"
        arg="${arg/#⊙ /}"
        echo "$arg"
    }

    # Normalize the branch argument if present for switch/remove commands
    if [[ "$1" == "remove" || "$1" == "rm" || "$1" == "switch" || "$1" == "s" ]]; then
        if [[ $# -ge 2 ]]; then
            set -- "$1" "$(_gwt_strip_visual "$2")" "${@:3}"
        fi
    fi

    # Check if this is an interactive command that shouldn't have output captured
    if [[ "$1" == "remove" || "$1" == "rm" ]]; then
        # Run interactively but capture the last line for potential cd command
        # Use a temp file to capture output while still allowing interaction
        local tmpfile last_line
        tmpfile=$(mktemp)
        _gwt_run "$@" | tee "$tmpfile"
        last_line=$(tail -n1 "$tmpfile")
        rm "$tmpfile"

        # Check if the last line is a cd command
        if [[ "$last_line" == cd* ]]; then
            local dir="${last_line#cd }"
            cd "$dir" || echo "Failed to change directory to $dir"
        fi
    else
        # Run the Python script and capture output for non-interactive commands
        local output
        output=$(_gwt_run "$@")

        # Parse the output for special commands
        if [[ "$output" == cd* ]]; then
            # Extract the directory path and change to it
            local dir="${output#cd }"
            cd "$dir" || echo "Failed to change directory to $dir"
        elif [[ "$output" == GWT_GIT_DIR=* ]]; then
            # Extract the git directory and set it
            local dir="${output#GWT_GIT_DIR=}"
            if [ -d "$dir" ]; then
                export GWT_GIT_DIR="$dir"
                echo "GWT_GIT_DIR set to $dir"
            else
                echo "Error: $dir is not a valid directory" >&2
                return 1
            fi
        else
            # Just print the output
            echo "$output"
        fi
    fi
}

# If this script is being sourced (not executed directly)
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    # Set up the completion
    complete -F _gwt_completions gwt
else
    # If executed directly, just run the function with all arguments
    gwt "$@"
fi