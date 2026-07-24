# gwtlib/fuzzy.py
"""Fuzzy worktree picker.

`gwt fz [query]` ranks every worktree against the query with rapidfuzz and shows
an interactive list. Enter runs the picker's current action on the selection;
Shift+Tab cycles that action (switch -> path -> tree -> remove).

stdout is reserved for the shell wrapper protocol (a lone `cd <path>` line that
gwt.sh/gwt.fish turn into a real cd), so the picker draws on /dev/tty instead --
under `output=$(gwt ...)` stdout is a pipe, not the terminal.
"""

import os
import select
import shutil
import signal
import sys
import termios
import tty

try:
    from rapidfuzz import fuzz, utils as fuzz_utils

    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

from gwtlib.display import ColorMode
from gwtlib.parsing import parse_worktree_legacy, parse_worktree_porcelain
from gwtlib.paths import is_path_current_worktree
from gwtlib.tree import _color_enabled, show_tree
from gwtlib.worktrees import remove_worktree

# Each action maps onto a command gwt already has.
ACTIONS = ["switch", "path", "tree", "remove"]
ACTION_HELP = {
    "switch": "cd to worktree",
    "path": "print path",
    "tree": "stack tree",
    "remove": "remove worktree",
}
DEFAULT_ACTION = "switch"

# A path match is worth slightly less than the same match on the branch name.
PATH_SCORE_WEIGHT = 0.9
SCORE_CUTOFF = 60
MAX_VISIBLE = 15

_ESCAPES = {
    b"[A": "up",
    b"[B": "down",
    b"[Z": "shift-tab",
    b"OA": "up",
    b"OB": "down",
}
_CONTROLS = {
    0x03: "cancel",  # ctrl+c (raw mode delivers the byte, not SIGINT)
    0x04: "cancel",  # ctrl+d
    0x07: "cancel",  # ctrl+g
    0x09: "tab",
    0x0A: "enter",
    0x0D: "enter",
    0x0E: "down",  # ctrl+n
    0x10: "up",  # ctrl+p
    0x15: "clear",  # ctrl+u
    0x17: "word-back",  # ctrl+w
    0x08: "backspace",
    0x7F: "backspace",
}

_resized = False


def load_worktree_entries(git_dir):
    """Every worktree (main included), as parse_worktree_porcelain dicts."""
    entries = parse_worktree_porcelain(git_dir, include_main=True)
    if entries is None:
        entries = parse_worktree_legacy(git_dir, include_main=True)
    return entries or []


def _default_sort_key(e):
    # Same ordering as `gwt ls`: current worktree first, main second, then by branch.
    current = is_path_current_worktree(e["path"])
    return (
        0 if current else (1 if e.get("is_main") else 2),
        (e.get("branch") or "").lower(),
    )


def score_entries(query, entries):
    """Rank entries against query. Returns [(score, entry)], best first.

    An empty query keeps every entry in the `gwt ls` order. Otherwise each entry
    is scored on its full branch name and its full path, and the better of the
    two wins -- so `gwt fz repo.gwt/foo` and `gwt fz foo` both find the worktree.
    """
    if not query:
        return [(0.0, e) for e in sorted(entries, key=_default_sort_key)]

    scored = []
    for e in entries:
        branch = e.get("branch") or ""
        path = e.get("path") or ""
        score = max(
            fuzz.WRatio(query, branch, processor=fuzz_utils.default_process),
            fuzz.WRatio(query, path, processor=fuzz_utils.default_process)
            * PATH_SCORE_WEIGHT,
        )
        if score >= SCORE_CUTOFF:
            scored.append((score, e))
    scored.sort(key=lambda t: (-t[0], _default_sort_key(t[1])))
    return scored


def _term_size(fd):
    try:
        size = os.get_terminal_size(fd)
    except OSError:
        size = shutil.get_terminal_size(fallback=(80, 24))
    # A terminal with no window size set reports 0x0; don't render into nothing.
    if size.columns <= 0 or size.lines <= 0:
        return os.terminal_size((80, 24))
    return size


def _fit(text, width):
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def _row_text(entry, width, marker):
    """One picker row: the FULL branch name, then as much path as still fits.

    Unlike the `ls` table this never abbreviates the branch to a fixed column --
    the path gives up space first, and only a branch too long for the terminal
    itself is ever shortened.
    """
    branch = entry.get("branch") or "(detached)"
    path = entry.get("path") or ""
    head = f"{marker} {branch}"
    if len(head) >= width:
        return _fit(head, width), ""
    avail = width - len(head) - 2
    if avail < 4:
        return head, ""
    if len(path) > avail:
        # Trim the front: the tail identifies the worktree.
        path = "…" + path[-(avail - 1) :]
    return head, path


def _render(fd, prev_lines, query, matches, total, sel, offset, action, enable):
    """Repaint the picker in place. Returns (lines_drawn, offset)."""
    BOLD = "\033[1m" if enable else ""
    DIM = "\033[2m" if enable else ""
    REVERSE = "\033[7m" if enable else ""
    CYAN = "\033[36m" if enable else ""
    RESET = "\033[0m" if enable else ""

    size = _term_size(fd)
    width, height = size.columns, size.lines
    view = max(1, min(len(matches) or 1, max(1, height - 3), MAX_VISIBLE))

    if sel < offset:
        offset = sel
    elif sel >= offset + view:
        offset = sel - view + 1
    offset = max(0, min(offset, max(0, len(matches) - view)))

    lines = [f"{CYAN}❯{RESET} {_fit(query, width - 2)}"]

    if not matches:
        lines.append(f"{DIM}  (no match){RESET}")
    for i in range(offset, min(offset + view, len(matches))):
        entry = matches[i][1]
        chosen = i == sel
        current = is_path_current_worktree(entry["path"])
        marker = "❯" if chosen else ("•" if current else " ")
        head, path = _row_text(entry, width, marker)
        if enable:
            head = f"{BOLD}{head}{RESET}" if (chosen or current) else head
            row = f"{head}  {DIM}{path}{RESET}" if path else head
            row = f"{REVERSE}{row}{RESET}" if chosen else row
        else:
            row = f"{head}  {path}" if path else head
        lines.append(row)

    footer = (
        f"{len(matches)}/{total} · enter: {action} ({ACTION_HELP[action]})"
        f" · shift+tab: change action · ↑/↓ move · esc: cancel"
    )
    lines.append(f"{DIM}{_fit(footer, width)}{RESET}")

    out = ""
    if prev_lines:
        out += f"\x1b[{prev_lines - 1}A" if prev_lines > 1 else ""
    out += "\r\x1b[J" + "\r\n".join(lines)
    os.write(fd, out.encode(errors="replace"))
    return len(lines), offset


def _clear(fd, prev_lines):
    if not prev_lines:
        return
    out = f"\x1b[{prev_lines - 1}A" if prev_lines > 1 else ""
    os.write(fd, (out + "\r\x1b[J").encode())


def _pending(fd, timeout=0.0):
    try:
        r, _, _ = select.select([fd], [], [], timeout)
    except (OSError, ValueError):
        return False
    return bool(r)


def _read_key(fd):
    """Return a key name, or ('text', str) for printable input."""
    ch = os.read(fd, 1)
    if not ch:
        return "cancel"
    b = ch[0]

    if b == 0x1B:
        if not _pending(fd, 0.05):
            return "cancel"  # a lone Esc
        intro = os.read(fd, 1)
        if intro not in (b"[", b"O"):
            return "unknown"
        seq = b""
        for _ in range(8):
            if not _pending(fd, 0.05):
                break
            c = os.read(fd, 1)
            if not c:
                break
            seq += c
            if 0x40 <= c[0] <= 0x7E:  # CSI final byte
                break
        return _ESCAPES.get(intro + seq, "unknown")

    if b in _CONTROLS:
        return _CONTROLS[b]
    if b < 0x20:
        return "unknown"

    # Gather the rest of a multi-byte UTF-8 character.
    extra = 0
    if b >= 0xF0:
        extra = 3
    elif b >= 0xE0:
        extra = 2
    elif b >= 0xC0:
        extra = 1
    for _ in range(extra):
        if not _pending(fd, 0.05):
            break
        ch += os.read(fd, 1)
    return ("text", ch.decode("utf-8", errors="ignore"))


def _on_resize(_signum, _frame):
    global _resized
    _resized = True


def _pick(fd, entries, query, action, enable):
    """Drive the picker loop. Returns (entry, action) or None if cancelled."""
    global _resized

    matches = score_entries(query, entries)
    sel, offset, prev = 0, 0, 0
    dirty = True
    while True:
        if dirty or _resized:
            _resized = False
            prev, offset = _render(
                fd, prev, query, matches, len(entries), sel, offset, action, enable
            )
            dirty = False

        if not _pending(fd, 0.2):
            continue

        key = _read_key(fd)
        if key == "cancel":
            _clear(fd, prev)
            return None
        elif key == "enter":
            if not matches:
                continue
            _clear(fd, prev)
            return matches[sel][1], action
        elif key == "shift-tab":
            action = ACTIONS[(ACTIONS.index(action) + 1) % len(ACTIONS)]
        elif key == "up":
            sel = (sel - 1) % len(matches) if matches else 0
        elif key == "down":
            sel = (sel + 1) % len(matches) if matches else 0
        elif key in ("backspace", "clear", "word-back") or isinstance(key, tuple):
            if key == "backspace":
                query = query[:-1]
            elif key == "clear":
                query = ""
            elif key == "word-back":
                query = query.rstrip().rpartition(" ")[0]
            else:
                query += key[1]
            matches = score_entries(query, entries)
            sel, offset = 0, 0
        else:
            continue
        dirty = True


def _perform(action, entry, git_dir, color):
    """Run the picker's action on the chosen worktree. Returns an exit code."""
    path = entry["path"]
    branch = entry.get("branch")

    if action == "switch":
        # The shell wrapper turns a lone `cd <path>` on stdout into a real cd.
        print(f"cd {path}")
        return 0
    if action == "path":
        print(path)
        return 0

    if not branch or entry.get("detached"):
        print(
            f"Error: worktree at {path} has no branch; '{action}' needs one.",
            file=sys.stderr,
        )
        return 1
    if action == "tree":
        show_tree(git_dir, base=branch, color=color)
        return 0
    if action == "remove":
        remove_worktree(branch, git_dir)
        return 0
    return 1


def fuzzy_pick(git_dir, query="", action=DEFAULT_ACTION, color=ColorMode.AUTO):
    """Fuzzy-match worktrees and act on the one picked.

    Falls back to acting on the best match when there is no terminal to draw on
    (piped, CI) or when a non-empty query is unambiguous.
    """
    if not HAS_RAPIDFUZZ:
        print(
            "Error: the rapidfuzz package is required for `gwt fz`.",
            file=sys.stderr,
        )
        print("Install with: pip install rapidfuzz", file=sys.stderr)
        return 1

    entries = load_worktree_entries(git_dir)
    if not entries:
        print("No worktrees found", file=sys.stderr)
        return 1

    matches = score_entries(query, entries)
    if not matches:
        print(f"No worktree matches '{query}'", file=sys.stderr)
        return 1

    # An unambiguous query needs no picker.
    if query and len(matches) == 1:
        return _perform(action, matches[0][1], git_dir, color)

    if not sys.stdin.isatty():
        return _perform(action, matches[0][1], git_dir, color)
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return _perform(action, matches[0][1], git_dir, color)

    enable = _color_enabled(color)
    saved = termios.tcgetattr(fd)
    old_winch = signal.getsignal(signal.SIGWINCH)
    try:
        signal.signal(signal.SIGWINCH, _on_resize)
        tty.setraw(fd)
        os.write(fd, b"\x1b[?25l")
        picked = _pick(fd, entries, query, action, enable)
    finally:
        os.write(fd, b"\x1b[?25h")
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        signal.signal(signal.SIGWINCH, old_winch)
        os.close(fd)

    if picked is None:
        return 0  # cancelled: nothing on stdout, so the shell stays put
    entry, action = picked
    return _perform(action, entry, git_dir, color)
