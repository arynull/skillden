"""Lockfile read and write."""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["LockfileError", "read_lock", "write_lock"]


class LockfileError(Exception):
    pass


def write_lock(path, root, constraint, pins) -> None:
    skills: dict[str, dict[str, str]] = {}
    for name, version, content_sha256 in pins:
        skills[str(name)] = {
            "version": str(version),
            "content_sha256": str(content_sha256),
        }
    data = {
        "constraint": str(constraint),
        "generated_by": "skillden 0.3.0",
        "root": str(root),
        "skills": skills,
    }
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    dest = Path(path)
    parent = dest.parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def read_lock(path) -> dict:
    dest = Path(path)
    try:
        text = dest.read_text(encoding="utf-8")
    except OSError as exc:
        raise LockfileError(f"cannot read lockfile: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise LockfileError(f"corrupt lockfile: {exc}") from exc
    if not isinstance(data, dict):
        raise LockfileError("corrupt lockfile: root object must exist")
    root = data.get("root")
    skills = data.get("skills")
    if not isinstance(root, str) or not root.strip():
        raise LockfileError("corrupt lockfile: bad 'root'")
    if not isinstance(skills, dict):
        raise LockfileError("corrupt lockfile: bad 'skills'")
    for key, val in skills.items():
        if not isinstance(key, str) or not key.strip():
            raise LockfileError("corrupt lockfile: bad skill name")
        if not isinstance(val, dict):
            raise LockfileError("corrupt lockfile: bad skill entry")
        ver = val.get("version")
        digest = val.get("content_sha256")
        if not isinstance(ver, str) or not ver.strip():
            raise LockfileError("corrupt lockfile: bad version")
        if not isinstance(digest, str) or not digest.strip():
            raise LockfileError("corrupt lockfile: bad content_sha256")
    return data
