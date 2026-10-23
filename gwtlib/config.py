# gwtlib/config.py
import os
import sys
from pathlib import Path

# HAS_TOML determination (from gwt.py:18-35)
try:
    if sys.version_info >= (3, 11):
        import tomllib as tomli  # type: ignore
    else:
        import tomli  # type: ignore
    import tomli_w  # type: ignore

    HAS_TOML = True
except ImportError:
    print(
        "Warning: tomli/tomli_w packages not found. Configuration features will be disabled.",
        file=sys.stderr,
    )
    print("Install with: pip install tomli tomli-w", file=sys.stderr)
    HAS_TOML = False


def get_config_path():
    """Get the path to the config file following XDG Base Directory spec."""
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        config_dir = Path(xdg_config_home) / "gwt"
    else:
        config_dir = Path.home() / ".config" / "gwt"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.toml"


def load_config():
    """Load configuration from file with fallback to defaults."""
    default_config = {"default_repo": "", "repos": {}}
    if not HAS_TOML:
        return default_config
    config_path = get_config_path()
    if not config_path.exists():
        try:
            with open(config_path, "wb") as f:
                tomli_w.dump(default_config, f)  # type: ignore
        except Exception as e:
            print(f"Error creating config file: {e}", file=sys.stderr)
        return default_config
    try:
        with open(config_path, "rb") as f:
            config = tomli.load(f)  # type: ignore
        return config
    except Exception as e:
        print(f"Error loading config file: {e}", file=sys.stderr)
        return default_config


def save_config(config):
    """Save the configuration to the config file."""
    if not HAS_TOML:
        return
    config_path = get_config_path()
    try:
        with open(config_path, "wb") as f:
            tomli_w.dump(config, f)  # type: ignore
    except Exception as e:
        print(f"Error saving config file: {e}", file=sys.stderr)


def get_repo_config(git_dir):
    """Get repository-specific configuration, creating defaults if needed."""
    config = load_config()
    if "repos" in config and git_dir in config["repos"]:
        return config["repos"][git_dir]
    default_repo_config = {"post_create_commands": []}
    if "repos" not in config:
        config["repos"] = {}
    config["repos"][git_dir] = default_repo_config
    save_config(config)
    return default_repo_config
