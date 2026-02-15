# Git Worktree Tool (gwt)

(this is very new untested code -- please report bugs)

An opinionated tool for rapidly working in git worktrees. `gwt` works like `git switch` but automatically manages worktrees. It makes it fast and easy to:

- See all existing branch+worktrees in the current repo

  `gwt` or `gwt list` or `gwt ls`

- Switch to a branch+worktree [in the current repo]

  `gwt switch branch-name` or `gwt s branch-name` 
  
  This command:
  - Switches to existing worktree if it exists
  - Creates worktree for existing local branch (and runs post-create commands)
  - Auto-tracks remote branches (with --guess, enabled by default)
  - Shows helpful error if branch doesn't exist
  
  (supports tab completion of ALL branches: worktrees, local, and remote)

- Create a new branch+worktree [in the current repo]

  `gwt switch -c branch-name` or `gwt s -c branch-name`
  
  Additional options:
  - `-C` or `--force-create`: Create branch, resetting if it exists
  - `--no-guess`: Disable remote branch auto-detection

- Remove a worktree and optionally its branch

  `gwt remove branch-name` or `gwt rm branch-name`

- Switch to a different repo

  `gwt --repo /some/other/repo.git`

The "current repo" is stored in `$GWT_GIT_DIR` and a default value
can be initialized in your `.bashrc`.

### TODO

- Allow registration of setup scripts for new worktrees (e.g.,
  create `.env` files, install `node`, etc.)

## Background: bare repositories

`gwt` is designed to work with bare repositories and follows a convention for directory
layout to make worktree management easier:

- If your repo is at `/path/to/repo.git` (bare repo)
- Worktrees are stored at `/path/to/repo.gwt/branch-name`

This separation keeps Git's internal data (`.git`) separate from your working files
while maintaining a clear relationship between repositories and their worktrees.

Bare repos are the cleanest way to use worktrees. Clone with `--bare`, then 
set up remote tracking: `cd` into the repo, and run the following
([further reading](https://morgan.cugerone.com/blog/workarounds-to-git-worktree-using-bare-repository-and-cannot-fetch-remote-branches/)).

```bash
git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
```

managing git worktrees with a Bash integration for directory changing.

## Installation

### Automatic Installation

Run the installation script:

```bash
./install.sh
```

This will:
1. Install gwt.py and gwtlib/ to your AppDir: `$XDG_DATA_HOME/gwt` or `~/.local/share/gwt`
2. Install shell wrappers to `~/.local/bin` and add sourcing lines to your shell config
3. Ask for an optional default GWT_GIT_DIR

**uv vs python3:**
- gwt prefers running via `uv run --script` for isolation and speed.
- If uv is not available, gwt falls back to `python3`. On Python <3.11 you may need:
  `pip install tomli tomli-w`.

Then reload your shell:
```bash
source ~/.bashrc
```

### Manual Installation

If you prefer to install manually:

1. Make sure Python 3.11+ is installed (or Python 3.6+ with `tomli` and `tomli-w` packages)

2. Create AppDir and copy Python sources:
   ```bash
   mkdir -p "${XDG_DATA_HOME:-$HOME/.local/share}/gwt"
   cp gwt.py "${XDG_DATA_HOME:-$HOME/.local/share}/gwt/gwt.py"
   cp -r gwtlib "${XDG_DATA_HOME:-$HOME/.local/share}/gwt/gwtlib"
   ```

3. Install wrappers:
   ```bash
   mkdir -p ~/.local/bin
   cp gwt.sh ~/.local/bin/
   [ -f gwt.fish ] && cp gwt.fish ~/.local/bin/
   ```

4. Add to your shell config (bash/zsh):
   ```bash
   # GWT setup
   source ~/.local/bin/gwt.sh
   export GWT_GIT_DIR=/path/to/your/repo.git  # Optional default
   ```

   For fish:
   ```fish
   # GWT setup
   source ~/.local/bin/gwt.fish
   set -gx GWT_GIT_DIR /path/to/your/repo.git  # Optional default
   ```

5. Reload your shell:
   ```bash
   source ~/.bashrc  # or source ~/.zshrc, or source ~/.config/fish/config.fish
   ```

**Uninstall:**
- Remove AppDir: `rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/gwt"`
- Remove wrappers: `rm -f ~/.local/bin/gwt.{sh,fish}`
- Remove sourcing lines from your shell config

## Configuration

### Git Directory

There are three ways to specify which git repository to work with:

1. Set the `GWT_GIT_DIR` environment variable directly:
   ```bash
   export GWT_GIT_DIR=/path/to/your/repo.git
   ```

2. Use the built-in command (this also saves it as the default in your config file):
   ```bash
   gwt --repo /path/to/your/repo.git
   ```

3. Configure a default repository in the config file (see below)

### Configuration File

GWT can be configured using a TOML file at `~/.config/gwt/config.toml`.

Example configuration:

```toml
# Default repository to use if GWT_GIT_DIR env var isn't set
default_repo = "/path/to/default/repo.git"

# Repository-specific configurations
[repos."/path/to/repo1.git"]
# Commands to run after creating a new worktree
# These run in the new worktree directory
post_create_commands = [
    "npm install",
    "cp ../.env.example .env",
    "echo 'Worktree setup complete!'"
]

[repos."/path/to/repo2.git"]
post_create_commands = [
    "pip install -e .",
    "pre-commit install"
]
```

#### Configuration Options

- `default_repo`: Path to the git directory to use by default when `GWT_GIT_DIR` is not set
- `repos.<git-dir>.post_create_commands`: List of shell commands to run after creating a new worktree. These commands run in the newly created worktree directory. Post-create commands run whenever `gwt switch` creates a worktree (for local branches, remote branches, or new branches with `-c`).

The configuration file is created automatically when you first use the `gwt --repo` command. You can then edit it manually to add post-create commands or other settings.


## Usage

List all worktrees:
```
gwt
gwt list
gwt ls
```

Switch to a branch (creates worktree if needed):
```
gwt switch branch-name
gwt s branch-name
```

Create a new branch and worktree:
```
gwt switch -c branch-name
gwt s -c branch-name
```

Force create/reset a branch:
```
gwt switch -C branch-name
```

Switch to a remote branch (auto-tracks by default):
```
gwt switch remote-branch-name
# Or explicitly disable remote detection:
gwt switch --no-guess branch-name
```

Set the git directory for future commands:
```
gwt --repo /path/to/another/repo.git
```

Remove a worktree and optionally its branch:
```
gwt remove branch-name
gwt rm branch-name
```

## How it works

The installation combines a Python script (`gwt.py`) that handles the git operations with a Bash script (`gwt.sh`) that handles directory changing and tab completion. This approach minimizes what needs to be added to `.bashrc` while maintaining full functionality.