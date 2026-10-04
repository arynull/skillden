"""Manifest loading and bundle validation."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

__all__ = ["ManifestError", "load_manifest", "validate_bundle"]


class ManifestError(Exception):
    """Raised when a manifest or bundle is invalid."""


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}/[a-z0-9][a-z0-9._-]{1,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

_REQUIRED_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "license",
    "entry",
    "files",
)
_OPTIONAL_FIELDS = ("agents", "requires")
_ALLOWED_FIELDS = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load and strictly validate a manifest file.

    Args:
        path: Path to a ``.toml`` or ``.json`` manifest file.

    Returns:
        The validated manifest as a dict.

    Raises:
        ManifestError: If the file cannot be read, parsed, or fails validation.
    """
    manifest_path = Path(path)

    if not manifest_path.is_file():
        raise ManifestError(f"Manifest not found: {manifest_path}")

    try:
        suffix = manifest_path.suffix.lower()
        if suffix == ".json":
            text = manifest_path.read_text(encoding="utf-8")
            data = json.loads(text)
        elif suffix == ".toml":
            with manifest_path.open("rb") as f:
                data = tomllib.load(f)
        else:
            # Try TOML first, fall back to JSON for extensionless files.
            try:
                with manifest_path.open("rb") as f:
                    data = tomllib.load(f)
            except (OSError, ValueError):
                # Not valid TOML; try JSON instead.
                text = manifest_path.read_text(encoding="utf-8")
                data = json.loads(text)
    except ManifestError:
        raise
    except (OSError, ValueError) as exc:
        raise ManifestError(f"Failed to parse manifest {manifest_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("Manifest must be a mapping/object")

    for field in _REQUIRED_FIELDS:
        if field not in data:
            raise ManifestError(f"Missing required field: {field}")

    for key in data:
        if key not in _ALLOWED_FIELDS:
            raise ManifestError(f"Unknown field: {key}")

    name = data["name"]
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ManifestError(
            "Invalid 'name': must match "
            "'^[a-z0-9][a-z0-9._-]{1,63}/[a-z0-9][a-z0-9._-]{1,63}$'"
        )

    version = data["version"]
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise ManifestError("Invalid 'version': must match '^\\d+\\.\\d+\\.\\d+$'")

    description = data["description"]
    if not isinstance(description, str) or not description.strip():
        raise ManifestError("Invalid 'description': must be a non-empty string")

    entry = data["entry"]
    if not isinstance(entry, str) or not entry:
        raise ManifestError("Invalid 'entry': must be a non-empty string")

    files = data["files"]
    if not isinstance(files, list) or not files:
        raise ManifestError("Invalid 'files': must be a non-empty list")
    for item in files:
        if not isinstance(item, str) or not item:
            raise ManifestError(
                "Invalid 'files': each entry must be a non-empty string"
            )
    if len(set(files)) != len(files):
        raise ManifestError("Invalid 'files': entries must be unique")

    if entry not in files:
        raise ManifestError("Invalid 'entry': must be listed in 'files'")

    if "license" in data:
        lic = data["license"]
        if not isinstance(lic, str) or not lic.strip():
            raise ManifestError("Invalid 'license': must be a non-empty string")

    if "agents" in data:
        agents = data["agents"]
        if not isinstance(agents, list):
            raise ManifestError("Invalid 'agents': must be a list of strings")
        for a in agents:
            if not isinstance(a, str) or not a.strip():
                raise ManifestError(
                    "Invalid 'agents': each entry must be a non-empty string"
                )

    if "requires" in data:
        requires = data["requires"]
        if not isinstance(requires, list):
            raise ManifestError("Invalid 'requires': must be a list of strings")
        for r in requires:
            if not isinstance(r, str) or not r.strip():
                raise ManifestError(
                    "Invalid 'requires': each entry must be a non-empty string"
                )

    return data


def validate_bundle(manifest: dict[str, Any], bundle_dir: str | Path) -> bool:
    """Validate that all files in the manifest exist safely inside bundle_dir.

    Rejects absolute paths, ``..`` escapes, missing files, and symlinks
    that resolve outside the bundle directory.

    Args:
        manifest: Validated manifest dict (as returned by load_manifest).
        bundle_dir: Directory containing the bundle files.

    Returns:
        True if the bundle is valid.

    Raises:
        ManifestError: If any check fails.
    """
    if not isinstance(manifest, dict):
        raise ManifestError("Manifest must be a mapping/object")

    bundle_path = Path(bundle_dir)
    if not bundle_path.is_dir():
        raise ManifestError(f"Bundle directory not found: {bundle_path}")

    if "files" not in manifest or "entry" not in manifest:
        raise ManifestError("Manifest must contain 'files' and 'entry'")

    files = manifest["files"]
    entry = manifest["entry"]

    if not isinstance(files, list) or not files:
        raise ManifestError("Invalid 'files': must be a non-empty list")
    if not isinstance(entry, str) or not entry:
        raise ManifestError("Invalid 'entry': must be a non-empty string")
    if entry not in files:
        raise ManifestError("Invalid 'entry': must be listed in 'files'")

    bundle_resolved = bundle_path.resolve()

    for item in files:
        if not isinstance(item, str) or not item:
            raise ManifestError(f"Invalid file entry: {item!r}")

        p = Path(item)

        if p.is_absolute():
            raise ManifestError(f"Absolute path not allowed: {item}")

        if ".." in p.parts:
            raise ManifestError(f"Path escapes bundle: {item}")

        full = bundle_path / item

        # Missing (including broken symlinks).
        if not full.exists() and not full.is_symlink():
            raise ManifestError(f"Missing file: {item}")

        try:
            resolved = full.resolve()
        except OSError as exc:
            raise ManifestError(f"Cannot resolve path {item}: {exc}") from exc

        try:
            resolved.relative_to(bundle_resolved)
        except ValueError:
            raise ManifestError(f"Symlink escapes bundle: {item}") from None

        if not resolved.exists():
            raise ManifestError(f"Missing file: {item}")

    return True
