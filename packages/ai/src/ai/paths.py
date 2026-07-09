"""XDG-compliant path helpers for x-dog storage.

* ``~/.config/x-dog/`` — configuration
* ``~/.local/x-dog/`` — data (auth, model cache)
"""

from pathlib import Path

_APP_NAME = "x-dog"


def config_dir() -> Path:
    return Path.home() / ".config" / _APP_NAME


def data_dir() -> Path:
    return Path.home() / ".local" / _APP_NAME


def auth_file() -> Path:
    return data_dir() / "auth.json"


def models_cache_file() -> Path:
    return data_dir() / "models_cache.json"
