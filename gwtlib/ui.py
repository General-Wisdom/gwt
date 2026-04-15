# gwtlib/ui.py
"""User interaction utilities."""

import sys


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    """Prompt the user for a yes/no confirmation.

    Args:
        prompt: The prompt message (without the y/N suffix)
        default: The default value if user just presses Enter

    Returns:
        True for yes, False for no
    """
    suffix = "(Y/n)" if default else "(y/N)"
    print(f"{prompt} {suffix}: ", end="", file=sys.stderr)
    sys.stderr.flush()
    try:
        response = input().strip().lower()
    except EOFError:
        # Non-interactive context (e.g., piped input, CI)
        return default
    if not response:
        return default
    return response in ("y", "yes")
