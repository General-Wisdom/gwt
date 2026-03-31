import os

import pytest


@pytest.fixture
def git_env(tmp_path):
    """Return environment dict isolating git from user config."""
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "HOME": str(home),
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test User",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    env.pop("GWT_GIT_DIR", None)  # Tests should set this explicitly
    return env
