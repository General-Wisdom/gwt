# Git Worktree Tool (gwt)

(this is very new untested code -- please report bugs)

An opinionated tool for rapidly working in git worktrees. `gwt` works like `git switch` but automatically manages worktrees. It makes it fast and easy to:

- See your worktrees as a stacked-PR tree (the default)

  `gwt` or `gwt tree`

- See all existing branch+worktrees as a flat list

  `gwt list` or `gwt ls`

- Fuzzy-find a worktree and switch to it

  `gwt fz query` or `gwt f query`

  Opens an interactive picker of the worktrees matching `query`; Enter switches
  to the highlighted one. Shift+Tab changes what Enter does.

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

- Remove a worktree and clean up branches

  `gwt remove branch-name` or `gwt rm branch-name`
  
  The behavior depends on the branch state:
  - **PR merged**: Automatically removes worktree, local branch, and remote branch
  - **Local only** (not pushed): Removes worktree, prompts for local branch deletion
  - **Pushed but PR not merged**: Shows warning, prompts for each deletion
  
  This requires the `gh` CLI for PR status detection.

- Garbage-collect stale worktrees

  `gwt gc`

  Runs a clean command (e.g. `just clean`) on worktrees older than `--clean-days`
  (7) and removes ones older than `--delete-days` (28) that are clean and merged.
  Use `-p`/`--plan` to preview, `-y` to skip the confirmation prompt.

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

Show the stacked-PR tree (the default when run with no arguments):
```
gwt
gwt tree
```

List all worktrees as a flat table:
```
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

Remove a worktree and clean up branches:

```bash
gwt remove branch-name
gwt rm branch-name
```

The removal behavior is context-aware:
- If the PR has been merged, `gwt rm` automatically cleans up the worktree, local branch, and remote branch
- If the branch was never pushed to a remote, it removes the worktree and prompts about the local branch
- If the branch is pushed but the PR isn't merged, it warns you and prompts for confirmation before each deletion

Fuzzy-find a worktree and switch to it:
```
gwt fz alph
gwt f alph
gwt fz        # no query: pick from all worktrees
```

The query is fuzzy-matched (via [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz))
against each worktree's **full** branch name and **full** path — so `gwt fz forty`
finds `a-very-long-branch-name-that-exceeds-forty-characters`, and
`gwt fz repo.gwt/alpha` matches on the path. Unlike the `ls` table, the picker
never abbreviates the branch name to fit a column.

```
❯ feat
❯ feature-alpha    …/repo.gwt/feature-alpha
  feature-beta     …/repo.gwt/feature-beta
2/4 · enter: switch (cd to worktree) · shift+tab: change action · ↑/↓ move · esc: cancel
```

Keys:

| Key | Action |
| --- | --- |
| ↑ / ↓, `ctrl+p` / `ctrl+n` | move the selection |
| any character, `backspace`, `ctrl+u` | refine the query live |
| `enter` | run the current action on the selection |
| `shift+tab` | cycle the action Enter performs |
| `esc`, `ctrl+c` | cancel (your shell stays where it is) |

Shift+Tab cycles the action through `switch → path → tree → remove`, wrapping
around; the footer always shows what Enter will do:

- **`switch`** (default) — cd to the worktree, exactly like `gwt switch`
- **`path`** — print the worktree's absolute path
- **`tree`** — show the stacked-PR tree rooted at that branch (`gwt tree --base`)
- **`remove`** — remove the worktree and clean up branches (`gwt remove`)

Pass `--action` to start on a different action (it still cycles from there):
```
gwt fz --action path alph
```

A query that matches exactly one worktree skips the picker and acts immediately.
When there's no terminal to draw on (piped output, CI), `gwt fz` acts on the
best match instead of prompting, so it stays usable in scripts.

Show worktrees with commits as a stacked-PR tree:
```
gwt tree
```

The trunk (the main worktree's branch) is the root, and each worktree that has
commits ahead of the trunk is nested under the branch it is stacked on. The tree
structure itself shows what's stacked on what, so each node shows just two things:

- **`+N`** — the size of this branch's PR: commits it adds on top of its parent.
- **a sync badge** — `✓` in sync with its parent and the trunk; `↓N ⚠` behind the
  trunk by N (needs a rebase); `↻N` behind its parent by N (needs a restack);
  `(unrelated)` if it shares no history with the trunk.

…followed by the time since its last commit. The current worktree is marked `•`.

```
  main (root)              7mo
• ├ feature-a    +3  ✓     2d
  │ └ feature-b  +1  ↻1 ⚠  1d   (needs restack onto feature-a)
  └ feature-c    +1  ↓3 ⚠  8d   (needs rebase onto main)
  +N this PR (vs parent) · ✓ synced · ↓N behind main · ↻N restack on parent
```

Pass `-l`/`--long` to also show the short SHA and worktree path. Pass `--pr` to
look up each branch's GitHub PR status (`OPEN`/`MERGED`/`CLOSED`) via the `gh` CLI —
this makes one network request per shown branch (run in parallel), so it's opt-in.

By default, stale branches (no commit in the last 30 days) are hidden so the tree
stays focused on active work; a `… N stale branch(es) hidden` footer tells you how
many were dropped. Ancestors of an active branch are always kept so the structure
stays intact.

Options:
- `-l`, `--long`: show the worktree path and SHA columns
- `--pr`: show each branch's GitHub PR status (`OPEN`/`MERGED`/`CLOSED`; needs `gh`)
- `--stale-days N`: hide branches with no commit in N days (default: 30)
- `-a`, `--all`: include stale branches (no age filtering)
- `--base BRANCH`: use a different branch as the tree root
- `--color {auto,always,never}`: control colorized output
- `--absolute`: show absolute worktree paths (implies `-l`)

## How it works

The installation combines a Python script (`gwt.py`) that handles the git operations with a Bash script (`gwt.sh`) that handles directory changing and tab completion. This approach minimizes what needs to be added to `.bashrc` while maintaining full functionality.