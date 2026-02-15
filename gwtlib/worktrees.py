# gwtlib/worktrees.py
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from gwtlib.branches import (
    branch_exists_locally,
    find_remote_branch,
    get_main_branch_name,
)
from gwtlib.config import get_repo_config
from gwtlib.git_ops import run_git_command
from gwtlib.parsing import get_worktree_list
from gwtlib.paths import get_main_worktree_path, get_worktree_base


def create_worktree_for_branch(branch_name, git_dir, worktree_path):
    """Create a worktree for an existing local branch.

    This creates the git worktree and then runs any post-create commands
    configured for this repository (e.g., npm install, pip install).
    """
    try:
        run_git_command(["worktree", "add", worktree_path, branch_name], git_dir)
        print(f"Created worktree at {worktree_path}", file=sys.stderr)
        run_post_create_commands(git_dir, worktree_path, branch_name)
        print(f"cd {worktree_path}")
    except subprocess.CalledProcessError as e:
        handle_worktree_error(e, branch_name)
        sys.exit(1)


def create_tracking_worktree(branch_name, git_dir, remote_ref, worktree_path):
    """Create a worktree that tracks a remote branch.

    This creates a local branch tracking the remote, creates the git worktree,
    and then runs any post-create commands configured for this repository.
    """
    try:
        # Create local branch tracking the remote
        run_git_command(
            ["worktree", "add", "-b", branch_name, worktree_path, remote_ref], git_dir
        )
        print(f"Branch '{branch_name}' set up to track '{remote_ref}'", file=sys.stderr)
        print(f"Created worktree at {worktree_path}", file=sys.stderr)
        run_post_create_commands(git_dir, worktree_path, branch_name)
        print(f"cd {worktree_path}")
    except subprocess.CalledProcessError as e:
        handle_worktree_error(e, branch_name)
        sys.exit(1)


def handle_worktree_error(e, branch_name):
    """Handle errors from worktree creation."""
    # Show git's actual error message if available
    if hasattr(e, 'stderr') and e.stderr:
        print(f"Error: {e.stderr.strip()}", file=sys.stderr)
    elif hasattr(e, 'stdout') and e.stdout:
        print(f"Error: {e.stdout.strip()}", file=sys.stderr)
    else:
        print(
            f"Error creating worktree for branch '{branch_name}': {e}", file=sys.stderr
        )


def run_post_create_commands(git_dir, worktree_path, branch_name):
    """Run post-create commands for a worktree."""
    repo_config = get_repo_config(git_dir)
    if repo_config.get("post_create_commands"):
        print(f"Running post-create commands for {branch_name}...", file=sys.stderr)
        current_dir = os.getcwd()
        try:
            os.chdir(worktree_path)
            for cmd in repo_config["post_create_commands"]:
                print(f"Running: {cmd}", file=sys.stderr)
                # Redirect stdout to stderr to not interfere with cd command
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.stdout:
                    print(result.stdout, file=sys.stderr)
                if result.stderr:
                    print(result.stderr, file=sys.stderr)
                if result.returncode != 0:
                    raise subprocess.CalledProcessError(result.returncode, cmd)
        except Exception as e:
            print(f"Error running post-create commands: {e}", file=sys.stderr)
        finally:
            os.chdir(current_dir)


def switch_branch(branch_name, git_dir, create=False, force_create=False, guess=True):
    """Unified switch logic that handles all branch scenarios."""
    worktree_base = get_worktree_base(git_dir)
    worktree_path = os.path.join(worktree_base, branch_name)

    # Special handling for switching to main repo
    if branch_name == get_main_branch_name(git_dir):
        main_path = get_main_worktree_path(git_dir)
        if main_path:
            print(f"cd {main_path}")
            return

    # Check if worktree already exists
    worktrees = get_worktree_list(git_dir, include_main=True)
    for wt in worktrees:
        if wt["branch"] == branch_name:
            print(f"cd {wt['path']}")
            return

    # Handle create flags
    if force_create:
        # Force create new branch
        try:
            run_git_command(["branch", "-f", branch_name], git_dir)
        except subprocess.CalledProcessError:
            run_git_command(["branch", branch_name], git_dir)
        create_worktree_for_branch(branch_name, git_dir, worktree_path)
        return

    if create:
        # Create new branch
        try:
            run_git_command(["branch", branch_name], git_dir)
            create_worktree_for_branch(branch_name, git_dir, worktree_path)
            return
        except subprocess.CalledProcessError:
            print(f"Error: Branch '{branch_name}' already exists", file=sys.stderr)
            print("Use -C to force create", file=sys.stderr)
            sys.exit(1)

    # Check if local branch exists - create worktree for it
    # (this also runs any configured post-create commands)
    if branch_exists_locally(branch_name, git_dir):
        print(
            f"Branch '{branch_name}' exists locally but has no worktree. Creating worktree...",
            file=sys.stderr,
        )
        create_worktree_for_branch(branch_name, git_dir, worktree_path)
        return

    # Check remote branches if guess is enabled
    if guess:
        remote_ref = find_remote_branch(branch_name, git_dir)
        if remote_ref:
            create_tracking_worktree(branch_name, git_dir, remote_ref, worktree_path)
            return

    # Branch doesn't exist
    print(f"fatal: invalid reference: {branch_name}", file=sys.stderr)
    if guess:
        print(
            f"hint: If you meant to create a new branch, use: gwt switch -c {branch_name}",
            file=sys.stderr,
        )
    else:
        print(
            f"hint: If you meant to check out a remote branch, use: gwt switch --guess {branch_name}",
            file=sys.stderr,
        )
        print(
            f"hint: If you meant to create a new branch, use: gwt switch -c {branch_name}",
            file=sys.stderr,
        )
    sys.exit(1)


def remove_worktree(branch_name: str, git_dir: str) -> None:
    try:
        # Find the worktree path using our shared function
        worktrees = get_worktree_list(git_dir)

        # Find the worktree for this branch
        worktree_path: Optional[str] = None
        for worktree in worktrees:
            if worktree["branch"] == branch_name:
                worktree_path = worktree["path"]
                break

        if not worktree_path:
            print(
                f"Error: Worktree for branch '{branch_name}' not found", file=sys.stderr
            )
            sys.exit(1)
        # Narrow type for static checkers and add runtime safety
        assert worktree_path is not None

        # Check if we're currently in the worktree being removed
        current_dir = os.getcwd()
        worktree_abs = os.path.abspath(worktree_path)
        current_abs = os.path.abspath(current_dir)

        need_cd = False
        if current_abs.startswith(worktree_abs + os.sep) or current_abs == worktree_abs:
            need_cd = True
            # Determine the safe directory based on repo type
            git_dir_path = Path(git_dir).resolve()

            if git_dir_path.name == ".git" and git_dir_path.is_dir():
                # Non-bare repo: git_dir is /path/to/repo/.git
                # Safe dir should be the repo itself: /path/to/repo
                safe_dir = str(git_dir_path.parent)
            else:
                # Bare repo: git_dir is /path/to/repo.git
                # Safe dir should be parent of .gwt: /path/to
                safe_dir = os.path.dirname(get_worktree_base(git_dir))

            print(
                f"You're in the worktree being removed. Will change to {safe_dir} after removal.",
                file=sys.stderr,
            )

        # Remove the worktree (don't capture output as it might prompt the user)
        run_git_command(["worktree", "remove", worktree_path], git_dir, capture=False)

        # Then remove the branch if the user confirms
        # Print to stderr and flush to ensure prompt is visible immediately
        print(
            f"Do you also want to delete the branch '{branch_name}'? (y/N): ",
            end="",
            file=sys.stderr,
        )
        sys.stderr.flush()
        confirm = input()
        if confirm.lower() == "y":
            # Change to safe directory first if needed, before trying to delete branch
            if need_cd:
                os.chdir(safe_dir)
            run_git_command(["branch", "-D", branch_name], git_dir, capture=False)
            print(f"Branch '{branch_name}' has been deleted")

        print(f"Worktree for '{branch_name}' has been removed")

        # Output the cd command for the shell to execute if we were in the removed worktree
        if need_cd:
            print(f"cd {safe_dir}")
    except subprocess.CalledProcessError as e:
        print(f"Error removing worktree: {e}", file=sys.stderr)
        sys.exit(1)
