#!/usr/bin/env fish
# GWT Fish Shell Integration

# Resolve AppDir per XDG, allow override via GWT_APPDIR
function __gwt_appdir --description 'Compute gwt AppDir path'
    if set -q GWT_APPDIR
        echo $GWT_APPDIR
        return
    end
    if set -q XDG_DATA_HOME
        echo "$XDG_DATA_HOME/gwt"
    else
        echo "$HOME/.local/share/gwt"
    end
end

function __gwt_py --description 'Path to gwt.py in AppDir'
    set -l appdir (__gwt_appdir)
    echo "$appdir/gwt.py"
end

# Runner that picks uv if available, else python3
function __gwt_run --description 'Run gwt via uv or python3'
    set -l PYTHON_SCRIPT (__gwt_py)
    if not test -f "$PYTHON_SCRIPT"
        echo "gwt: AppDir not found or incomplete at: "(__gwt_appdir) >&2
        echo "Re-run ./install.sh to install gwt." >&2
        return 1
    end
    if type -q uv
        uv run --script "$PYTHON_SCRIPT" $argv
    else
        python3 "$PYTHON_SCRIPT" $argv
    end
end

function gwt
    # Interactive remove path
    if test "$argv[1]" = "remove" -o "$argv[1]" = "rm"
        set -l tmpfile (mktemp)
        __gwt_run $argv | tee $tmpfile
        set -l exit_code $status
        set -l last_line (tail -n1 $tmpfile)
        rm $tmpfile

        if string match -q "cd *" $last_line
            set -l dir (string replace "cd " "" $last_line)
            cd $dir; or echo "Failed to change directory to $dir"
        end
        return $exit_code
    else
        set -l output (__gwt_run $argv)
        set -l exit_code $status

        if string match -q "cd *" $output
            set -l dir (string replace "cd " "" $output)
            cd $dir; or echo "Failed to change directory to $dir"
        else if string match -q "GWT_GIT_DIR=*" $output
            set -l dir (string replace "GWT_GIT_DIR=" "" $output)
            if test -d $dir
                set -gx GWT_GIT_DIR $dir
                echo "GWT_GIT_DIR set to $dir"
            else
                echo "Error: $dir is not a valid directory" >&2
                return 1
            end
        else
            echo $output
        end
        return $exit_code
    end
end

# Completion helpers use __gwt_run
function __gwt_complete_branches
    __gwt_run list --branches all 2>/dev/null
    or __gwt_run list --branches worktrees 2>/dev/null
end

function __gwt_complete
    set -l cmd (commandline -opc)
    set -l cur (commandline -ct)
    set -l commands repo switch s list ls l remove rm gc

    if test (count $cmd) -eq 1
        printf '%s\n' $commands
        return
    end

    set -l subcmd $cmd[2]
    switch $subcmd
        case switch s
            __gwt_run list --branches all --annotate fish 2>/dev/null
        case remove rm
            __gwt_run list --branches worktrees --annotate fish 2>/dev/null
        case repo
            __fish_complete_directories
        case '*'
            return
    end
end

complete -c gwt -f -k -a '(__gwt_complete)'
