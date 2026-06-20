# gwtlib/tree.py
"""Render worktrees that have commits as a stacked-PR tree.

The trunk (the main worktree's branch, or an explicit --base) is the root.
Every other worktree whose branch has commits not in the trunk becomes a node,
attached under its *parent* branch -- the closest branch it is stacked on top of.

A node dict looks like:
    {
      "branch": str,
      "sha": str,            # full tip SHA
      "head": str,           # short SHA for display
      "path": str | None,    # worktree path (None if branch has no worktree)
      "is_root": bool,
      "unrelated": bool,     # branch shares no history with the trunk
      "ahead": int | None,   # commits ahead of parent (None for root)
      "behind": int | None,  # commits behind parent (None for root)
      "children": list[node],
    }
"""

import os
import subprocess
import sys

from gwtlib.display import ColorMode
from gwtlib.git_ops import run_git_quiet, run_git_rc
from gwtlib.parsing import parse_worktree_legacy, parse_worktree_porcelain
from gwtlib.paths import is_path_current_worktree, rel_display_path


def _rev_parse(git_dir, ref):
    """Return the full commit SHA a ref points at, or None."""
    try:
        res = run_git_quiet(["rev-parse", "--verify", f"{ref}^{{commit}}"], git_dir)
        return res.stdout.strip() or None
    except subprocess.CalledProcessError:
        return None


def _is_ancestor(git_dir, ancestor_sha, descendant_sha):
    """True if ancestor_sha is an ancestor of (or equal to) descendant_sha."""
    return (
        run_git_rc(
            ["merge-base", "--is-ancestor", ancestor_sha, descendant_sha], git_dir
        )
        == 0
    )


def _rev_list(git_dir, rev_range):
    """Full SHAs reachable in a revision range, newest first ([] on error)."""
    try:
        return run_git_quiet(["rev-list", rev_range], git_dir).stdout.split()
    except subprocess.CalledProcessError:
        return []


def build_stack_tree(git_dir, base=None):
    """Build the stacked-PR tree.

    Returns (root_node, notes). root_node is None (with an explanatory note) if the
    trunk branch cannot be determined or resolved. notes is a list of advisory strings.
    """
    notes = []

    # Gather worktree branches (skip detached / branchless).
    entries = parse_worktree_porcelain(git_dir, include_main=True)
    if entries is None:
        entries = parse_worktree_legacy(git_dir, include_main=True)
    entries = entries or []

    wt_by_branch: dict[str, dict] = {}
    main_branch = None
    for e in entries:
        b = e.get("branch")
        if not b or e.get("detached") or b == "(detached)":
            continue
        wt_by_branch.setdefault(b, e)
        if e.get("is_main") and main_branch is None:
            main_branch = b

    # Determine the trunk/root branch.
    root_branch = base or main_branch
    if not root_branch:
        return None, [
            "Could not determine the trunk branch (main worktree is detached?); "
            "pass --base BRANCH."
        ]

    root_sha = _rev_parse(git_dir, root_branch)
    if not root_sha:
        return None, [f"Could not resolve base branch '{root_branch}'."]

    # For each worktree branch, collect the commits it carries that are *not* in the
    # trunk, with one `git rev-list` per branch. This keeps the whole command O(n) in
    # the number of worktrees -- everything below is computed from these commit sets
    # in Python, with no further per-pair git subprocesses.
    tip = {root_branch: root_sha}
    commits = {root_branch: set()}  # commits in (root..branch], as full SHAs
    for b in wt_by_branch:
        if b == root_branch:
            continue
        ahead_commits = _rev_list(git_dir, f"{root_sha}..refs/heads/{b}")
        if ahead_commits:
            tip[b] = ahead_commits[0]
            commits[b] = set(ahead_commits)

    # Candidates: worktree branches with commits not in the trunk.
    candidates = sorted(b for b in commits if b != root_branch and commits[b])
    included = [root_branch] + candidates
    ahead_root = {b: len(commits[b]) for b in included}

    # Which candidates actually descend from the trunk (one cheap is-ancestor each)?
    descends_root = {b: _is_ancestor(git_dir, root_sha, tip[b]) for b in candidates}

    def is_anc(p, b):
        """True if branch p's tip is an ancestor of branch b's tip."""
        if p == root_branch:
            return descends_root.get(b, False)
        return tip[p] in commits[b]

    def parent_of(b):
        """The branch b is stacked on (the trunk is always a valid fallback).

        A parent must sit "below" b in the stack -- fewer commits ahead of the
        trunk, breaking ties by name -- so the resulting graph is always an
        acyclic forest. Among the valid parents we pick the one sharing the most
        history with b, preferring a clean ancestor and the nearest fork point.
        This still recognises a parent that has advanced past where b forked from
        it (its tip is no longer in b's history), which surfaces "behind" branches.
        """
        scored = []
        for p in included:
            if p == b or tip[p] == tip[b]:
                continue
            # Keep the order strict to stay acyclic.
            if not (
                ahead_root[p] < ahead_root[b]
                or (ahead_root[p] == ahead_root[b] and p < b)
            ):
                continue
            shared = len(commits[p] & commits[b])  # commits common past the trunk
            advance = ahead_root[p] - shared  # how far p moved past the fork point
            key = (
                shared,  # deepest shared history wins
                1 if is_anc(p, b) else 0,  # prefer a clean ancestor
                -advance,  # prefer the nearest fork point
                1 if p == root_branch else 0,  # prefer the trunk on ties
            )
            scored.append((key, p))
        if not scored:
            return None
        best_key = max(k for k, _ in scored)
        # Break ties deterministically by branch name.
        return min(p for k, p in scored if k == best_key)

    parent = {}
    unrelated = set()
    for b in candidates:
        parent[b] = parent_of(b) or root_branch
        if not descends_root[b]:
            unrelated.add(b)

    def make_node(branch, is_root):
        e = wt_by_branch.get(branch)
        node = {
            "branch": branch,
            "sha": tip[branch],
            "head": (tip[branch] or "")[:10],
            "path": e["path"] if e else None,
            "is_root": is_root,
            "unrelated": branch in unrelated,
            "ahead": None,
            "behind": None,
            "children": [],
        }
        if not is_root:
            p = parent[branch]
            node["ahead"] = len(commits[branch] - commits[p])  # commits b has, p lacks
            node["behind"] = len(commits[p] - commits[branch])  # commits p has, b lacks
        return node

    nodes = {root_branch: make_node(root_branch, True)}
    for b in candidates:
        nodes[b] = make_node(b, False)
    for b in candidates:
        nodes[parent[b]]["children"].append(nodes[b])
    for n in nodes.values():
        n["children"].sort(key=lambda c: c["branch"].lower())

    return nodes[root_branch], notes


def _color_enabled(color_mode):
    if color_mode == ColorMode.ALWAYS:
        return True
    if color_mode == ColorMode.AUTO:
        return sys.stderr.isatty() and (os.environ.get("NO_COLOR") is None)
    return False


def render_tree(root, git_dir, color=ColorMode.AUTO, absolute=False):
    """Return a list of formatted lines (without trailing newlines) for the tree."""
    enable = _color_enabled(color)
    BOLD = "\033[1m" if enable else ""
    DIM = "\033[2m" if enable else ""
    GREEN = "\033[32m" if enable else ""
    YELLOW = "\033[33m" if enable else ""
    RESET = "\033[0m" if enable else ""

    rows = []

    def add_row(node, tree_prefix):
        path = node.get("path")
        is_cur = bool(path) and is_path_current_worktree(path)
        marker = "•" if is_cur else " "

        name = node["branch"]
        name_disp = f"{BOLD}{name}{RESET}" if (enable and is_cur) else name
        if node["is_root"]:
            label_plain = f"{name} (root)"
            label_disp = (
                f"{name_disp} {DIM}(root){RESET}" if enable else f"{name} (root)"
            )
        else:
            label_plain, label_disp = name, name_disp

        col1_plain = f"{tree_prefix}{marker} {label_plain}"
        col1_disp = f"{tree_prefix}{marker} {label_disp}"

        if node["is_root"]:
            counts_plain = counts_disp = ""
        else:
            ahead, behind = node["ahead"] or 0, node["behind"] or 0
            counts_plain = f"+{ahead}"
            counts_disp = f"{GREEN}+{ahead}{RESET}" if enable else counts_plain
            if behind and not node["unrelated"]:
                counts_plain += f" -{behind} ⚠"
                counts_disp += (
                    f" {YELLOW}-{behind} ⚠{RESET}" if enable else f" -{behind} ⚠"
                )
            if node["unrelated"]:
                counts_plain += " (unrelated)"
                counts_disp += (
                    f" {YELLOW}(unrelated){RESET}" if enable else " (unrelated)"
                )

        disp_path = rel_display_path(path, git_dir, absolute) if path else ""
        path_disp = f"{DIM}{disp_path}{RESET}" if (enable and disp_path) else disp_path

        rows.append(
            {
                "col1_plain": col1_plain,
                "col1_disp": col1_disp,
                "counts_plain": counts_plain,
                "counts_disp": counts_disp,
                "head": node.get("head") or "",
                "path_disp": path_disp,
            }
        )

    def walk(node, prefix, is_last, is_root):
        tree_prefix = "" if is_root else prefix + ("└─ " if is_last else "├─ ")
        add_row(node, tree_prefix)
        child_prefix = "" if is_root else prefix + ("   " if is_last else "│  ")
        children = node["children"]
        for i, ch in enumerate(children):
            walk(ch, child_prefix, i == len(children) - 1, is_root=False)

    walk(root, "", True, is_root=True)

    col1_w = max((len(r["col1_plain"]) for r in rows), default=0)
    counts_w = max((len(r["counts_plain"]) for r in rows), default=0)
    head_w = max((len(r["head"]) for r in rows), default=0)
    sep = "  "

    lines = []
    for r in rows:
        col1 = r["col1_disp"] + " " * (col1_w - len(r["col1_plain"]))
        counts = r["counts_disp"] + " " * (counts_w - len(r["counts_plain"]))
        head = r["head"].ljust(head_w)
        line = f"{col1}{sep}{counts}{sep}{head}{sep}{r['path_disp']}".rstrip()
        lines.append(line)
    return lines


def show_tree(git_dir, base=None, color=ColorMode.AUTO, absolute=False):
    """Print the stacked-PR tree to stderr (stdout stays clean for the shell wrapper)."""
    root, notes = build_stack_tree(git_dir, base=base)
    if root is None:
        for n in notes:
            print(n, file=sys.stderr)
        return

    if not root["children"]:
        print(
            f"No worktrees with commits ahead of '{root['branch']}'.", file=sys.stderr
        )
        for n in notes:
            print(n, file=sys.stderr)
        return

    for ln in render_tree(root, git_dir, color=color, absolute=absolute):
        print(ln, file=sys.stderr)

    if notes:
        print("", file=sys.stderr)
        for n in notes:
            print(n, file=sys.stderr)
