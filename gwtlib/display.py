# gwtlib/display.py
import os
import shutil
import subprocess
import sys

from gwtlib.git_ops import is_worktree_dirty, run_git_command, run_git_quiet
from gwtlib.parsing import (
    get_worktree_list,
    parse_worktree_legacy,
    parse_worktree_porcelain,
)
from gwtlib.paths import is_path_current_worktree, rel_display_path


class ColorMode:
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


def format_worktree_rows(
    entries, git_dir, show_status=False, color_mode="auto", force_absolute=False
):
    """
    entries: list of dicts from parse_worktree_porcelain/legacy
    Returns list[str] lines (without newline), formatted for pretty output.

    Column design:
    [markers:2] [branch:var]  [head:10]  [path:var]
    """
    # Decide color enablement (stderr TTY + NO_COLOR)
    enable_color = False
    if color_mode == ColorMode.ALWAYS:
        enable_color = True
    elif color_mode == ColorMode.AUTO:
        enable_color = sys.stderr.isatty() and (os.environ.get("NO_COLOR") is None)

    # ANSI codes
    BOLD = "\033[1m" if enable_color else ""
    DIM = "\033[2m" if enable_color else ""
    RED = "\033[31m" if enable_color else ""
    YELLOW = "\033[33m" if enable_color else ""
    MAGENTA = "\033[35m" if enable_color else ""
    RESET = "\033[0m" if enable_color else ""

    # Compute status (!) lazily per worktree if requested
    def is_dirty(path):
        if not show_status:
            return False
        return is_worktree_dirty(path, include_untracked=False)

    # Sorting: current first, main second, others by branch (case-insensitive)
    def sort_key(e):
        current = is_path_current_worktree(e["path"])
        main = e.get("is_main", False)
        key_branch = (e.get("branch") or "").lower()
        return (0 if current else (1 if main else 2), key_branch)

    entries_sorted = sorted(entries, key=sort_key)

    # Precompute field sizes
    term_width = shutil.get_terminal_size(fallback=(80, 24)).columns
    head_width = 10
    sep = "  "
    marker_width = 2
    branch_names = [(e.get("branch") or "") for e in entries_sorted]
    max_branch = min(max([len(b) for b in branch_names] + [0]), 40)

    # Build lines
    lines = []
    for e in entries_sorted:
        markers = []

        if is_path_current_worktree(e["path"]):
            markers.append("•")
        else:
            markers.append(" ")

        if e.get("is_main"):
            markers.append("M")
        elif e.get("locked"):
            markers.append("L")
        elif e.get("prunable"):
            markers.append("P")
        else:
            markers.append(" ")

        dirty = is_dirty(e["path"])
        if dirty:
            markers[-1] = "!"

        marker_str = "".join(markers)

        branch = e.get("branch") or ""
        head = e.get("head") or ""
        path = rel_display_path(e["path"], git_dir, force_absolute)

        # Truncation to fit terminal
        fixed = marker_width + len(sep) + head_width + len(sep)
        avail = max(term_width - fixed, 20)
        branch_width = min(max_branch, avail // 2)
        path_width = max(avail - branch_width - len(sep), 10)

        def trunc(s, w):
            if len(s) <= w:
                return s.ljust(w)
            if w <= 1:
                return s[:w]
            return s[: max(0, w - 1)] + "…"

        # Truncate BEFORE applying colors
        branch_cell = trunc(branch, branch_width)
        head_cell = head.ljust(head_width)[:head_width]
        path_cell = trunc(path, path_width)

        # Apply colors AFTER truncation
        if enable_color and is_path_current_worktree(e["path"]):
            branch_cell = f"{BOLD}{branch_cell}{RESET}"
        if enable_color:
            path_cell = f"{DIM}{path_cell}{RESET}"

        # Colorize markers
        if enable_color:
            if "!" in marker_str:
                marker_str = marker_str.replace("!", f"{RED}!{RESET}")
            if "L" in marker_str:
                marker_str = marker_str.replace("L", f"{YELLOW}L{RESET}")
            if "P" in marker_str:
                marker_str = marker_str.replace("P", f"{MAGENTA}P{RESET}")

        line = f"{marker_str}{sep}{branch_cell}{sep}{head_cell}{sep}{path_cell}"
        lines.append(line.rstrip())

    return lines


def list_worktrees(
    git_dir,
    branches_only=False,
    raw=False,
    verbose=False,
    no_warn=False,
    show_status=False,
    color=ColorMode.AUTO,
    absolute=False,
):
    """
    Pretty list to stderr by default. stdout remains empty unless branches_only is True.
    """
    try:
        if branches_only:
            # For tab completion, suppress warnings and print branch names only to stdout.
            worktrees = get_worktree_list(git_dir, include_main=True, warnings=[])
            for wt in worktrees:
                if wt.get("branch"):
                    print(wt["branch"])
            return

        # Raw mode (legacy): delegate to git and print to stderr
        if raw:
            try:
                res = run_git_quiet(["worktree", "list"], git_dir)
                if res.stdout:
                    print(res.stdout, file=sys.stderr, end="")
            except subprocess.CalledProcessError as e:
                print(f"Error listing worktrees: {e}", file=sys.stderr)
                sys.exit(1)
            return

        # Pretty mode path
        # Collect warnings for summary
        warnings = [] if not no_warn else None
        # Check integrity by invoking the existing reconciliation
        _ = get_worktree_list(
            git_dir,
            include_main=False,
            warnings=warnings if warnings is not None else [],
        )

        # Parse porcelain entries (include main)
        entries = parse_worktree_porcelain(git_dir, include_main=True)
        if entries is None:
            entries = parse_worktree_legacy(git_dir, include_main=True)

        if not entries:
            print("No worktrees found", file=sys.stderr)
            if warnings is not None and verbose and warnings:
                print("", file=sys.stderr)
                for w in warnings:
                    print(w, file=sys.stderr)
            return

        lines = format_worktree_rows(
            entries,
            git_dir=git_dir,
            show_status=show_status,
            color_mode=color,
            force_absolute=absolute,
        )
        for ln in lines:
            print(ln, file=sys.stderr)

        # Warning summary
        if warnings is not None and warnings:
            if verbose:
                print("", file=sys.stderr)
                print("Notes:", file=sys.stderr)
                for w in warnings:
                    print(w, file=sys.stderr)
            else:
                n = len(warnings)
                print("", file=sys.stderr)
                print(
                    f"Notes: {n} integrity issue{'s' if n != 1 else ''}. Use -v for details.",
                    file=sys.stderr,
                )

    except Exception as e:
        if not branches_only:
            print(f"Error: {e}", file=sys.stderr)
        return


def list_all_branches(git_dir, mode="all", annotate=None):
    """List branches for tab completion.

    Args:
        mode: "all", "local", "worktrees"
        annotate: None | "bash" | "fish"
    """

    # Helper function to print branch with annotation
    def print_branch(b, kind):
        if annotate is None:
            print(b)
            return
        if annotate == "fish":
            # Fish supports "word<TAB>description"
            if kind == "worktree":
                desc = "● worktree"
            elif kind == "local":
                desc = "○ local"
            else:
                desc = "⊙ remote"
            print(f"{b}\t{desc}")
        elif annotate == "bash":
            # Bash: prefix with symbol (insertion will include symbol; stripped by wrapper)
            if kind == "worktree":
                print(f"● {b}")
            elif kind == "local":
                print(f"○ {b}")
            else:
                print(f"⊙ {b}")
        else:
            print(b)

    # Get worktree branches (always needed for categorization)
    worktree_branches = {
        wt["branch"]
        for wt in get_worktree_list(git_dir, include_main=True, warnings=[])
        if wt.get("branch")
    }

    # For worktrees mode, just print worktree branches (excluding main)
    if mode == "worktrees":
        worktrees = get_worktree_list(git_dir, include_main=False, warnings=[])
        wt_branches = [wt["branch"] for wt in worktrees if wt.get("branch")]
        for b in sorted(wt_branches):
            print_branch(b, "worktree")
        return

    # Get local branches (single fetch, used for both collection and categorization)
    local_branches = set()
    try:
        result = run_git_command(
            ["for-each-ref", "--format=%(refname:short)", "refs/heads/"], git_dir
        )
        for branch in result.stdout.strip().split("\n"):
            if branch:
                local_branches.add(branch)
    except Exception:
        pass

    # Collect all branches
    branches = set(local_branches)

    # Add remote branches (without remote prefix for completion)
    if mode == "all":
        try:
            result = run_git_command(
                ["for-each-ref", "--format=%(refname:short)", "refs/remotes/"], git_dir
            )
            for ref in result.stdout.strip().split("\n"):
                if ref and "/" in ref:
                    # Extract branch name from remote/branch
                    branch = ref.split("/", 1)[1]
                    branches.add(branch)
        except Exception:
            pass

    # Categorize branches
    worktree_list = sorted([b for b in branches if b in worktree_branches])
    local_no_worktree_list = sorted(
        [b for b in branches if b in local_branches and b not in worktree_branches]
    )
    remote_only_list = sorted([b for b in branches if b not in local_branches])

    # Output in order: worktrees, local branches, remote branches
    for branch in worktree_list:
        print_branch(branch, "worktree")
    for branch in local_no_worktree_list:
        print_branch(branch, "local")
    for branch in remote_only_list:
        print_branch(branch, "remote")
