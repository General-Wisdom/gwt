# gwtlib/worktrees.py
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from gwtlib.branches import (
    branch_exists_locally,
    delete_remote_branch,
    find_remote_branch,
    get_main_branch_name,
    get_pr_state,
    get_remote_tracking_branch,
    remote_branch_exists,
)
from gwtlib.config import get_repo_config
from gwtlib.display import prompt_yes_no
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


def _get_safe_dir_if_needed(worktree_path: str, git_dir: str) -> Optional[str]:
    """Check if we're in the worktree being removed, return safe dir if so.

    Returns:
        The safe directory to change to, or None if not needed.
    """
    current_dir = os.getcwd()
    worktree_abs = os.path.abspath(worktree_path)
    current_abs = os.path.abspath(current_dir)

    if current_abs.startswith(worktree_abs + os.sep) or current_abs == worktree_abs:
        git_dir_path = Path(git_dir).resolve()

        if git_dir_path.name == ".git" and git_dir_path.is_dir():
            # Non-bare repo: git_dir is /path/to/repo/.git
            safe_dir = str(git_dir_path.parent)
        else:
            # Bare repo: git_dir is /path/to/repo.git
            safe_dir = os.path.dirname(get_worktree_base(git_dir))

        print(
            f"You're in the worktree being removed. Will change to {safe_dir} after removal.",
            file=sys.stderr,
        )
        return safe_dir
    return None


def remove_worktree(branch_name: str, git_dir: str) -> None:
    """Remove a worktree and optionally its local and remote branches.

    The behavior depends on the branch state:

    1. PR is merged: Automatically removes worktree, local branch, and remote branch.
    2. Branch not synced to remote: Removes worktree, prompts for local branch deletion.
    3. Branch synced but PR not merged: Shows warning, prompts for each deletion.
    """
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
        assert worktree_path is not None

        # Check if we need to change directory after removal
        safe_dir = _get_safe_dir_if_needed(worktree_path, git_dir)

        # Determine branch state
        remote_ref = get_remote_tracking_branch(branch_name, git_dir)
        has_remote = remote_ref is not None and remote_branch_exists(
            remote_ref, git_dir
        )

        # Parse remote info for deletion
        remote_name: Optional[str] = None
        if has_remote and remote_ref:
            # remote_ref is like 'origin/branch-name'
            parts = remote_ref.split("/", 1)
            if len(parts) == 2:
                remote_name = parts[0]

        # Check PR state if branch has a remote
        pr_state = None
        pr_is_merged = False
        if has_remote:
            pr_info = get_pr_state(branch_name)
            if pr_info:
                pr_state, pr_is_merged = pr_info

        # Determine removal strategy based on state
        if pr_is_merged:
            # Case 1: PR is merged - clean up everything automatically
            print(
                f"PR for '{branch_name}' has been merged. Cleaning up...",
                file=sys.stderr,
            )
            _remove_all(
                branch_name,
                git_dir,
                worktree_path,
                safe_dir,
                remote_name,
            )
        elif not has_remote:
            # Case 2: Branch not synced to remote - current behavior
            _remove_local_only(branch_name, git_dir, worktree_path, safe_dir)
        else:
            # Case 3: Branch synced to remote but PR not merged (or no PR)
            if pr_state == "OPEN":
                print(
                    f"\nWARNING: PR for '{branch_name}' is still OPEN!",
                    file=sys.stderr,
                )
            elif pr_state == "CLOSED":
                print(
                    f"\nWARNING: PR for '{branch_name}' was CLOSED (not merged).",
                    file=sys.stderr,
                )
            else:
                print(
                    f"\nWARNING: Branch '{branch_name}' is synced to remote.",
                    file=sys.stderr,
                )
            print(
                "Others may have access to this branch. Proceeding with caution.\n",
                file=sys.stderr,
            )
            _remove_with_prompts(
                branch_name,
                git_dir,
                worktree_path,
                safe_dir,
                remote_name,
            )

    except subprocess.CalledProcessError as e:
        print(f"Error removing worktree: {e}", file=sys.stderr)
        sys.exit(1)


def _remove_all(
    branch_name: str,
    git_dir: str,
    worktree_path: str,
    safe_dir: Optional[str],
    remote_name: Optional[str],
) -> None:
    """Remove worktree, local branch, and remote branch without prompting."""
    # Remove worktree
    run_git_command(["worktree", "remove", worktree_path], git_dir, capture=False)
    print(f"Removed worktree for '{branch_name}'", file=sys.stderr)

    # Change to safe directory if needed before branch operations
    if safe_dir:
        os.chdir(safe_dir)

    # Remove local branch
    try:
        run_git_command(["branch", "-D", branch_name], git_dir, capture=False)
        print(f"Deleted local branch '{branch_name}'", file=sys.stderr)
    except subprocess.CalledProcessError:
        print(f"Note: Could not delete local branch '{branch_name}'", file=sys.stderr)

    # Remove remote branch
    if remote_name:
        if delete_remote_branch(branch_name, remote_name, git_dir):
            print(
                f"Deleted remote branch '{remote_name}/{branch_name}'", file=sys.stderr
            )
        else:
            print(
                f"Note: Could not delete remote branch (may already be deleted)",
                file=sys.stderr,
            )

    # Output cd command if needed
    if safe_dir:
        print(f"cd {safe_dir}")


def _remove_local_only(
    branch_name: str,
    git_dir: str,
    worktree_path: str,
    safe_dir: Optional[str],
) -> None:
    """Remove worktree and prompt for local branch deletion (original behavior)."""
    # Remove worktree
    run_git_command(["worktree", "remove", worktree_path], git_dir, capture=False)
    print(f"Removed worktree for '{branch_name}'", file=sys.stderr)

    # Prompt for local branch deletion
    if prompt_yes_no(f"Delete local branch '{branch_name}'?"):
        if safe_dir:
            os.chdir(safe_dir)
        run_git_command(["branch", "-D", branch_name], git_dir, capture=False)
        print(f"Deleted local branch '{branch_name}'", file=sys.stderr)

    # Output cd command if needed
    if safe_dir:
        print(f"cd {safe_dir}")


def _remove_with_prompts(
    branch_name: str,
    git_dir: str,
    worktree_path: str,
    safe_dir: Optional[str],
    remote_name: Optional[str],
) -> None:
    """Remove with explicit prompts for each component."""
    # Prompt for worktree removal
    if not prompt_yes_no(f"Remove worktree for '{branch_name}'?"):
        print("Aborted.", file=sys.stderr)
        return

    run_git_command(["worktree", "remove", worktree_path], git_dir, capture=False)
    print(f"Removed worktree for '{branch_name}'", file=sys.stderr)

    # Prompt for local branch deletion
    delete_local = prompt_yes_no(f"Delete local branch '{branch_name}'?")
    if delete_local:
        if safe_dir:
            os.chdir(safe_dir)
        try:
            run_git_command(["branch", "-D", branch_name], git_dir, capture=False)
            print(f"Deleted local branch '{branch_name}'", file=sys.stderr)
        except subprocess.CalledProcessError:
            print(f"Note: Could not delete local branch", file=sys.stderr)

    # Prompt for remote branch deletion
    if remote_name:
        if prompt_yes_no(f"Delete remote branch '{remote_name}/{branch_name}'?"):
            if delete_remote_branch(branch_name, remote_name, git_dir):
                print(
                    f"Deleted remote branch '{remote_name}/{branch_name}'",
                    file=sys.stderr,
                )
            else:
                print(f"Note: Could not delete remote branch", file=sys.stderr)

    # Output cd command if needed
    if safe_dir:
        print(f"cd {safe_dir}")
