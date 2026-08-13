"""XDG-compliant path helpers for xdog storage.

* ``~/.config/xdog/`` — configuration
* ``~/.local/xdog/`` — data (auth, model cache)
"""

from pathlib import Path

_APP_NAME = "xdog"


def config_dir() -> Path:
    return Path.home() / ".config" / _APP_NAME


def data_dir() -> Path:
    return Path.home() / ".local" / _APP_NAME


def auth_file() -> Path:
    return data_dir() / "auth.json"


def models_cache_file() -> Path:
    return data_dir() / "models_cache.json"
