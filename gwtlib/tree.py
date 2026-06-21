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
      "ahead": int | None,   # commits ahead of parent = this PR's size (None for root)
      "behind": int | None,  # commits behind parent = needs restack (None for root)
      "behind_main": int | None,  # commits behind the trunk (None for root)
      "parent_is_root": bool | None,  # True if this branch hangs directly off the trunk
      "age_days": float | None,  # days since the branch tip's last commit
      "pr": str | None,      # GitHub PR state if looked up (--pr): OPEN/MERGED/CLOSED
      "children": list[node],
    }
"""

import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from gwtlib.display import ColorMode
from gwtlib.git_ops import run_git_quiet, run_git_rc
from gwtlib.github import get_pr_state
from gwtlib.parsing import parse_worktree_legacy, parse_worktree_porcelain
from gwtlib.paths import is_path_current_worktree, rel_display_path


def _rev_parse(git_dir, ref):
    """Return the full commit SHA a ref points at, or None."""
    try:
        res = run_git_quiet(["rev-parse", "--verify", f"{ref}^{{commit}}"], git_dir)
        return res.stdout.strip() or None
    except subprocess.CalledProcessError:
        return None


def _has_merge_base(git_dir, a, b):
    """True if a and b share any common ancestor (i.e. related histories)."""
    return run_git_rc(["merge-base", a, b], git_dir) == 0


def _parallel_map(fn, items):
    """Run fn over items across a small thread pool (git/subprocess release the GIL)."""
    items = list(items)
    if not items:
        return []
    if len(items) == 1:
        return [fn(items[0])]
    workers = min(16, max(1, (os.cpu_count() or 4)), len(items))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


def _branch_meta(git_dir):
    """One `for-each-ref` call -> {branch: (tip_sha, age_days)} for all local heads."""
    try:
        res = run_git_quiet(
            [
                "for-each-ref",
                "--format=%(refname:short) %(objectname) %(committerdate:unix)",
                "refs/heads/",
            ],
            git_dir,
        )
    except subprocess.CalledProcessError:
        return {}
    now = time.time()
    meta = {}
    for line in res.stdout.splitlines():
        parts = line.split(" ")
        if len(parts) < 3:
            continue
        name, sha, ts = parts[0], parts[1], parts[2]
        try:
            age = max(0.0, (now - float(ts)) / 86400.0)
        except ValueError:
            age = None
        meta[name] = (sha, age)
    return meta


def _branch_diffs(git_dir, root_sha, branches):
    """For each branch, one `git rev-list --left-right root...branch` (run in parallel).

    Returns {branch: (ahead_shas:set, behind_count:int)} where ahead = commits the
    branch has beyond the trunk and behind = commits the trunk has beyond the branch.
    A single call yields both directions, and behind==0 also means "descends from
    trunk" -- so this replaces the old three-calls-per-branch (ahead, behind, is
    ancestor) with one.
    """

    def one(branch):
        try:
            toks = run_git_quiet(
                ["rev-list", "--left-right", f"{root_sha}...refs/heads/{branch}"],
                git_dir,
            ).stdout.split()
        except subprocess.CalledProcessError:
            toks = []
        ahead, behind = set(), 0
        for tok in toks:
            if tok[:1] == ">":
                ahead.add(tok[1:])
            elif tok[:1] == "<":
                behind += 1
        return branch, (ahead, behind)

    return dict(_parallel_map(one, branches))


def _format_age(days):
    """Compact relative age, e.g. '<1h', '5h', '3d', '6w', '8mo' ('' if unknown)."""
    if days is None:
        return ""
    if days < 1:
        hours = days * 24
        return "<1h" if hours < 1 else f"{int(hours)}h"
    if days < 14:
        return f"{int(days)}d"
    if days < 60:
        return f"{int(days / 7)}w"
    return f"{int(days / 30)}mo"


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

    # Branch tips + ages for every local head in a single for-each-ref call.
    meta = _branch_meta(git_dir)
    ages = {b: m[1] for b, m in meta.items()}
    tip = {b: sha for b, (sha, _age) in meta.items()}

    root_sha = tip.get(root_branch) or _rev_parse(git_dir, root_branch)
    if not root_sha:
        return None, [f"Could not resolve base branch '{root_branch}'."]
    tip[root_branch] = root_sha

    # For each worktree branch, one `git rev-list --left-right root...branch` (run in
    # parallel) yields both how far ahead and how far behind the trunk it is. behind==0
    # also means it descends from the trunk, so this single call per branch replaces
    # the old ahead + behind + is-ancestor trio.
    wt_branches = [b for b in wt_by_branch if b != root_branch and b in tip]
    diffs = _branch_diffs(git_dir, root_sha, wt_branches)

    commits = {root_branch: set()}  # commits in (root..branch], as full SHAs
    behind_root = {}
    for b, (ahead, behind) in diffs.items():
        if ahead:
            commits[b] = ahead
            behind_root[b] = behind

    # Candidates: worktree branches with commits not in the trunk.
    candidates = sorted(b for b in commits if b != root_branch and commits[b])
    included = [root_branch] + candidates
    ahead_root = {b: len(commits[b]) for b in included}

    # Descends from trunk == nothing behind it (derived, no extra git call).
    descends_root = {b: behind_root.get(b, 0) == 0 for b in candidates}

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

    parent = {b: parent_of(b) or root_branch for b in candidates}

    # "unrelated" means genuinely disconnected history (no merge-base with the trunk),
    # not merely behind it -- a branch the trunk advanced past still shares an ancestor.
    # Only branches that don't descend from the trunk need the check; run those in
    # parallel since a repo can have many diverged branches.
    maybe_unrelated = [b for b in candidates if not descends_root[b]]
    unrelated = {
        b
        for b, related in zip(
            maybe_unrelated,
            _parallel_map(
                lambda b: _has_merge_base(git_dir, root_sha, tip[b]), maybe_unrelated
            ),
        )
        if not related
    }

    def make_node(branch, is_root):
        e = wt_by_branch.get(branch)
        node = {
            "branch": branch,
            "sha": tip[branch],
            "head": (tip[branch] or "")[:10],
            "path": e["path"] if e else None,
            "is_root": is_root,
            "unrelated": branch in unrelated,
            "ahead": None,  # commits vs parent (this PR's size)
            "behind": None,  # commits behind parent (needs restack)
            "behind_main": None if is_root else behind_root[branch],
            "parent_is_root": None if is_root else (parent[branch] == root_branch),
            "age_days": ages.get(branch),
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


def render_tree(
    root,
    git_dir,
    color=ColorMode.AUTO,
    absolute=False,
    stale_days=None,
    show_path=False,
    show_pr=False,
):
    """Return a list of formatted lines (without trailing newlines) for the tree."""
    enable = _color_enabled(color)
    BOLD = "\033[1m" if enable else ""
    DIM = "\033[2m" if enable else ""
    GREEN = "\033[32m" if enable else ""
    YELLOW = "\033[33m" if enable else ""
    MAGENTA = "\033[35m" if enable else ""
    RED = "\033[31m" if enable else ""
    RESET = "\033[0m" if enable else ""

    pr_colors = {"OPEN": GREEN, "MERGED": MAGENTA, "CLOSED": RED, "DRAFT": DIM}

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

        # Marker sits in a left gutter so branch names stay tight to their connectors.
        col1_plain = f"{marker} {tree_prefix}{label_plain}"
        col1_disp = f"{marker} {tree_prefix}{label_disp}"

        # "+N" = this PR's size (commits on top of its parent).
        if node["is_root"]:
            delta_plain = delta_disp = ""
        else:
            ahead = node["ahead"] or 0
            delta_plain = f"+{ahead}"
            delta_disp = f"{GREEN}+{ahead}{RESET}" if enable else delta_plain

        # Sync badge: ✓ in sync, ↻N needs restack on parent, ↓N behind main,
        # (unrelated) for a branch with no shared history.
        if node["is_root"]:
            badge_plain = badge_disp = ""
        elif node["unrelated"]:
            badge_plain = "(unrelated)"
            badge_disp = f"{DIM}(unrelated){RESET}" if enable else badge_plain
        else:
            parts = []
            behind_parent = node["behind"] or 0
            behind_main = node["behind_main"] or 0
            if not node["parent_is_root"] and behind_parent:
                parts.append(f"↻{behind_parent}")
            if behind_main:
                parts.append(f"↓{behind_main}")
            if parts:
                badge_plain = " ".join(parts) + " ⚠"
                badge_disp = f"{YELLOW}{badge_plain}{RESET}" if enable else badge_plain
            else:
                badge_plain = "✓"
                badge_disp = f"{GREEN}✓{RESET}" if enable else badge_plain

        pr_state = node.get("pr")
        pr_plain = pr_state or ""
        if enable and pr_plain:
            pr_disp = f"{pr_colors.get(pr_state, '')}{pr_plain}{RESET}"
        else:
            pr_disp = pr_plain

        age = node.get("age_days")
        age_plain = _format_age(age)
        is_stale = stale_days is not None and age is not None and age >= stale_days
        if not enable or not age_plain:
            age_disp = age_plain
        elif is_stale:
            age_disp = f"{YELLOW}{age_plain}{RESET}"
        else:
            age_disp = f"{DIM}{age_plain}{RESET}"

        disp_path = rel_display_path(path, git_dir, absolute) if path else ""
        path_disp = f"{DIM}{disp_path}{RESET}" if (enable and disp_path) else disp_path

        rows.append(
            {
                "col1_plain": col1_plain,
                "col1_disp": col1_disp,
                "delta_plain": delta_plain,
                "delta_disp": delta_disp,
                "badge_plain": badge_plain,
                "badge_disp": badge_disp,
                "pr_plain": pr_plain,
                "pr_disp": pr_disp,
                "age_plain": age_plain,
                "age_disp": age_disp,
                "head": node.get("head") or "",
                "path_disp": path_disp,
            }
        )

    def walk(node, prefix, is_last, is_root):
        # 2 columns of indent per level.
        tree_prefix = "" if is_root else prefix + ("└ " if is_last else "├ ")
        add_row(node, tree_prefix)
        child_prefix = "" if is_root else prefix + ("  " if is_last else "│ ")
        children = node["children"]
        for i, ch in enumerate(children):
            walk(ch, child_prefix, i == len(children) - 1, is_root=False)

    walk(root, "", True, is_root=True)

    col1_w = max((len(r["col1_plain"]) for r in rows), default=0)
    delta_w = max((len(r["delta_plain"]) for r in rows), default=0)
    badge_w = max((len(r["badge_plain"]) for r in rows), default=0)
    pr_w = max((len(r["pr_plain"]) for r in rows), default=0)
    age_w = max((len(r["age_plain"]) for r in rows), default=0)
    head_w = max((len(r["head"]) for r in rows), default=0)
    sep = "  "

    lines = []
    for r in rows:
        col1 = r["col1_disp"] + " " * (col1_w - len(r["col1_plain"]))
        delta = r["delta_disp"] + " " * (delta_w - len(r["delta_plain"]))
        badge = r["badge_disp"] + " " * (badge_w - len(r["badge_plain"]))
        age = r["age_disp"] + " " * (age_w - len(r["age_plain"]))
        line = f"{col1}{sep}{delta}{sep}{badge}"
        if show_pr:
            pr = r["pr_disp"] + " " * (pr_w - len(r["pr_plain"]))
            line += f"{sep}{pr}"
        line += f"{sep}{age}"
        if show_path:
            head = r["head"].ljust(head_w)
            line += f"{sep}{head}{sep}{r['path_disp']}"
        lines.append(line.rstrip())
    return lines


def _prune_stale(node, stale_days):
    """Filter a tree to fresh branches, keeping ancestors needed for structure.

    Returns (kept_node_or_None, hidden_count). A node is kept if it is the root,
    its tip is newer than stale_days (or its age is unknown), or it has a kept
    descendant. Stale leaves with no fresh descendants are dropped and counted.
    """

    def prune(node, is_root):
        kept_children = []
        hidden = 0
        for ch in node["children"]:
            kept, h = prune(ch, False)
            hidden += h
            if kept is not None:
                kept_children.append(kept)
        age = node.get("age_days")
        fresh = is_root or age is None or age < stale_days
        if not fresh and not kept_children:
            return None, hidden + 1
        new_node = dict(node)
        new_node["children"] = kept_children
        return new_node, hidden

    return prune(node, True)


def _attach_pr_states(root):
    """Look up each shown branch's GitHub PR state and store it as node['pr'].

    Network-bound (one `gh` call per branch), so this only runs under --pr and
    fetches in parallel for the branches actually displayed. Returns False if the
    gh CLI is unavailable (so the caller can warn once and skip the column).
    """
    if not shutil.which("gh"):
        return False

    nodes = []

    def collect(node):
        for ch in node["children"]:
            if ch.get("path"):
                nodes.append(ch)
            collect(ch)

    collect(root)
    if not nodes:
        return True

    def fetch(node):
        result = get_pr_state(node["branch"], cwd=node["path"], quiet=True)
        node["pr"] = result[0] if result else None

    _parallel_map(fetch, nodes)
    return True


def show_tree(
    git_dir,
    base=None,
    color=ColorMode.AUTO,
    absolute=False,
    stale_days=30,
    show_all=False,
    show_path=False,
    show_pr=False,
):
    """Print the stacked-PR tree to stderr (stdout stays clean for the shell wrapper).

    By default, branches whose tip has had no commit in `stale_days` days are
    hidden (ancestors of fresh branches are kept so the tree stays connected);
    pass show_all=True to include them.
    """
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

    hidden = 0
    if not show_all:
        root, hidden = _prune_stale(root, stale_days)

    if not root["children"]:
        print(
            f"No worktrees active in the last {stale_days} days "
            f"({hidden} stale; use --all to show).",
            file=sys.stderr,
        )
        return

    if show_pr:
        show_pr = _attach_pr_states(root)
        if not show_pr:
            print("Note: gh CLI not found; skipping PR status (--pr).", file=sys.stderr)

    for ln in render_tree(
        root,
        git_dir,
        color=color,
        absolute=absolute,
        stale_days=stale_days,
        show_path=show_path,
        show_pr=show_pr,
    ):
        print(ln, file=sys.stderr)

    # Legend for the +N / sync-badge columns.
    dim, reset = ("\033[2m", "\033[0m") if _color_enabled(color) else ("", "")
    print(
        f"{dim}  +N this PR (vs parent) · ✓ synced · ↓N behind {root['branch']} "
        f"· ↻N restack on parent{reset}",
        file=sys.stderr,
    )

    if hidden:
        print(
            f"… {hidden} stale branch{'es' if hidden != 1 else ''} hidden "
            f"(no commit in {stale_days} days; --all to show).",
            file=sys.stderr,
        )

    if notes:
        print("", file=sys.stderr)
        for n in notes:
            print(n, file=sys.stderr)
