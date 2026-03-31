# gwtlib/cli.py
import argparse
import os
import sys

from gwtlib.config import HAS_TOML, get_config_path, load_config, save_config
from gwtlib.display import list_all_branches, list_worktrees
from gwtlib.resolution import get_git_dir, get_git_dir_with_source
from gwtlib.worktrees import remove_worktree, switch_branch


def main():
    parser = argparse.ArgumentParser(description="Git worktree wrapper")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Create a 'repo' subcommand
    repo_parser = subparsers.add_parser("repo", help="Set or show the git directory")
    repo_parser.add_argument(
        "git_dir", nargs="?", help="Path to the git directory (omit to show current)"
    )

    # Create a 'switch' subcommand with 's' as alias
    switch_parser = subparsers.add_parser(
        "switch", aliases=["s"], help="Switch to or create branch worktree"
    )
    switch_parser.add_argument("branch_name", help="Name of the branch to switch to")
    switch_parser.add_argument(
        "-c",
        "--create",
        action="store_true",
        help="Create a new branch before switching",
    )
    switch_parser.add_argument(
        "-C",
        "--force-create",
        action="store_true",
        help="Create a new branch, resetting if it exists",
    )
    switch_parser.add_argument(
        "--guess",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Guess remote branch names (default: enabled)",
    )

    # Create a 'remove' subcommand with 'rm' as alias
    remove_parser = subparsers.add_parser(
        "remove", aliases=["rm"], help="Remove a worktree and optionally its branch"
    )
    remove_parser.add_argument(
        "branch_name", help="Name of the branch worktree to remove"
    )

    # Create a 'list' subcommand that's implicit if no command is provided
    list_parser = subparsers.add_parser(
        "list", aliases=["ls", "l"], help="List all worktrees"
    )
    list_parser.add_argument(
        "--branches",
        choices=["all", "local", "worktrees"],
        nargs="?",
        const="all",
        help="List branch names for completion (default: all)",
    )
    list_parser.add_argument(
        "--git-dir", help="Explicitly specify the git directory (for tab completion)"
    )

    # New flags
    list_parser.add_argument(
        "--raw", action="store_true", help="Show raw `git worktree list` output"
    )
    list_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show detailed warnings (if any)"
    )
    list_parser.add_argument("--no-warn", action="store_true", help="Suppress warnings")
    list_parser.add_argument(
        "--status",
        action="store_true",
        help="Show '!' marker for dirty worktrees (slower)",
    )
    list_parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="Colorize output: auto (default), always, or never",
    )
    list_parser.add_argument(
        "--absolute",
        action="store_true",
        help="Show absolute paths instead of relative",
    )
    list_parser.add_argument(
        "--annotate",
        choices=["none", "bash", "fish"],
        default="none",
        help="Annotate branch completion output for shells",
    )

    # Special command to get the default repository from config
    _get_repo_parser = subparsers.add_parser(
        "get-repo", help="Get the default repository from config (internal use)"
    )

    args = parser.parse_args()

    # Handle special commands that don't need a configured git dir
    if args.command == "repo":
        if args.git_dir:
            # Auto-detect if we need to append .git for non-bare repos
            git_dir = args.git_dir
            if os.path.isdir(git_dir):
                dot_git = os.path.join(git_dir, ".git")
                if os.path.isdir(dot_git):
                    git_dir = dot_git

            # Set the git directory
            print(f"GWT_GIT_DIR={git_dir}")

            # Update the config if TOML is available
            if HAS_TOML:
                config = load_config()
                config["default_repo"] = git_dir
                save_config(config)
                print(f"Default repo set to {git_dir}", file=sys.stderr)
            else:
                print(
                    "Note: Config file not updated (TOML support not available)",
                    file=sys.stderr,
                )
        else:
            # Show the current git directory
            current_git_dir = get_git_dir()
            if current_git_dir:
                print(f"Current repo: {current_git_dir}")
            else:
                print("No repo currently configured")
        return
    elif args.command == "get-repo":
        # Special command to output the default repo from config
        # This is used by the shell completion function
        if HAS_TOML:
            config = load_config()
            if config.get("default_repo") and os.path.isdir(config["default_repo"]):
                print(config["default_repo"])
        return

    # If no command specified, default to list
    if args.command is None:
        args.command = "list"

    # Resolve git dir with new function; explicit arg for list only
    if (
        hasattr(args, "git_dir")
        and args.command in ["list", "ls", "l"]
        and args.git_dir
    ):
        explicit_arg = args.git_dir
    else:
        explicit_arg = None

    git_dir, source, meta = get_git_dir_with_source(explicit_git_dir=explicit_arg)

    if not git_dir:
        # Error message templates:
        # E001: no repo detected and no valid fallbacks
        # E002: env invalid
        # E003: config invalid

        if source == "env_invalid":
            print(
                f"Error [E002]: GWT_GIT_DIR points to an invalid git directory: {meta.get('env')}",
                file=sys.stderr,
            )
            print(
                "hint: Ensure it points to a valid bare repo or to /path/to/repo/.git",
                file=sys.stderr,
            )
            print(
                "hint: Set with: export GWT_GIT_DIR=/path/to/repo/.git or run: gwt repo /path/to/repo.git",
                file=sys.stderr,
            )
            sys.exit(1)
        if source == "config_invalid":
            cfg_path = get_config_path()
            print(
                f"Error [E003]: default_repo in config is invalid: {meta.get('config')}",
                file=sys.stderr,
            )
            print(
                "hint: Update it by running: gwt repo /path/to/repo.git",
                file=sys.stderr,
            )
            print(f"hint: Or edit config: {cfg_path}", file=sys.stderr)
            sys.exit(1)

        # No detection, no env/config
        print(
            "Error [E001]: No git repository detected here and no valid GWT_GIT_DIR or default_repo configured.",
            file=sys.stderr,
        )
        print("hint: cd into any git repo; or", file=sys.stderr)
        print("hint: set GWT_GIT_DIR=/path/to/repo/.git; or", file=sys.stderr)
        print("hint: run: gwt repo /path/to/repo.git", file=sys.stderr)
        sys.exit(1)
    # Narrow type for static checkers
    assert git_dir is not None

    # Now pass git_dir to all functions that need it
    if args.command == "repo":
        # Just print a message for the shell script to handle
        print(f"GWT_GIT_DIR={args.git_dir}")
    elif args.command in ["switch", "s"]:
        switch_branch(
            args.branch_name,
            git_dir,
            create=getattr(args, "create", False),
            force_create=getattr(args, "force_create", False),
            guess=getattr(args, "guess", True),
        )
    elif args.command in ["remove", "rm"]:
        remove_worktree(args.branch_name, git_dir)
    elif args.command in ["list", "ls", "l"]:
        if hasattr(args, "branches") and args.branches:
            # Pass annotate flag down
            annotate = getattr(args, "annotate", "none")
            list_all_branches(
                git_dir,
                mode=args.branches,
                annotate=annotate if annotate != "none" else None,
            )
        else:
            list_worktrees(
                git_dir,
                branches_only=False,
                raw=getattr(args, "raw", False),
                verbose=getattr(args, "verbose", False),
                no_warn=getattr(args, "no_warn", False),
                show_status=getattr(args, "status", False),
                color=getattr(args, "color", "auto"),
                absolute=getattr(args, "absolute", False),
            )
