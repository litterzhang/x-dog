"""Extension loader: discover and load extensions from disk."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml

from coding.config import get_extensions_dir
from coding.core.defaults import EXTENSION_MANIFEST
from coding.core.extensions.types import Extension, ExtensionHook, ExtensionManifest


class ExtensionLoadError(Exception):
    """Raised when an extension cannot be loaded."""


def load_manifest(ext_dir: Path) -> ExtensionManifest:
    """Load an extension manifest from its directory."""
    manifest_path = ext_dir / EXTENSION_MANIFEST
    if not manifest_path.exists():
        raise ExtensionLoadError(f"No {EXTENSION_MANIFEST} found in {ext_dir}")

    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ExtensionLoadError(f"Invalid YAML in {manifest_path}: {exc}") from exc

    return ExtensionManifest(
        name=raw.get("name", ext_dir.name),
        version=raw.get("version", "0.0.0"),
        description=raw.get("description", ""),
        author=raw.get("author", ""),
        entry_point=raw.get("entry_point", "main"),
        dependencies=tuple(raw.get("dependencies", [])),
        settings_schema=raw.get("settings_schema", {}),
    )


def load_extension(ext_dir: Path) -> Extension:
    """Load a single extension from *ext_dir*.

    The extension directory must contain an ``extension.yaml`` manifest
    and a Python module specified by the ``entry_point`` field.
    """
    manifest = load_manifest(ext_dir)
    module_path = ext_dir / f"{manifest.entry_point}.py"

    hooks: list[ExtensionHook] = []

    if module_path.exists():
        try:
            spec = importlib.util.spec_from_file_location(
                f"pi_ext_{manifest.name}",
                str(module_path),
            )
            if spec is None or spec.loader is None:
                raise ExtensionLoadError(f"Cannot create module spec from {module_path}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            # Discover hook instances
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if isinstance(obj, ExtensionHook):
                    hooks.append(obj)
                elif isinstance(obj, type) and issubclass(obj, ExtensionHook) and obj is not ExtensionHook:
                    try:
                        hooks.append(obj())
                    except Exception:
                        pass

        except Exception as exc:
            raise ExtensionLoadError(
                f"Failed to load extension module {module_path}: {exc}"
            ) from exc

    return Extension(manifest=manifest, hooks=hooks)


def discover_extensions(
    extensions_dir: Path | None = None,
    names: list[str] | None = None,
) -> list[Extension]:
    """Discover and load extensions.

    Parameters
    ----------
    extensions_dir:
        Directory containing extension subdirectories.
    names:
        If given, only load extensions with these names.
    """
    base = extensions_dir or get_extensions_dir()
    if not base.is_dir():
        return []

    extensions: list[Extension] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        if names is not None and entry.name not in names:
            continue
        manifest_path = entry / EXTENSION_MANIFEST
        if not manifest_path.exists():
            continue
        try:
            ext = load_extension(entry)
            extensions.append(ext)
        except ExtensionLoadError:
            continue

    return extensions
