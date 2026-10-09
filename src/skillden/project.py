from __future__ import annotations

import json
import shutil
from pathlib import Path

from skillden.installer import IntegrityError, install
from skillden.registry import RegistryError
from skillden.resolver import resolve

PROJECT_META_DIR = ".skillden"
PINS_FILE = "pins.json"
PROJECT_SKILLS_DIR = ".skills"
GENERATED_BY = "skillden 0.4.0"
__all__ = [
    "GENERATED_BY",
    "PINS_FILE",
    "PROJECT_META_DIR",
    "PROJECT_SKILLS_DIR",
    "Project",
    "ProjectError",
    "find_project_root",
    "init_project",
    "pin_skill",
    "sync_project",
    "unpin_skill",
]


class ProjectError(Exception):
    pass


def find_project_root(start=None) -> Path | None:
    cur = Path(start) if start is not None else Path.cwd()
    if not cur.is_absolute():
        cur = Path.cwd() / cur
    cur = cur.resolve()
    for p in (cur, *cur.parents):
        if (p / PROJECT_META_DIR).is_dir():
            return p
    return None


def init_project(root=None) -> Path:
    base = Path(root) if root is not None else Path.cwd()
    meta = base / PROJECT_META_DIR
    meta.mkdir(parents=True, exist_ok=True)
    pins = meta / PINS_FILE
    if not pins.exists():
        data = {"generated_by": GENERATED_BY, "skills": {}}
        pins.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return base


class Project:
    def __init__(self, root=None) -> None:
        self.root = Path(root) if root is not None else Path.cwd()
        self.pins_path = self.root / PROJECT_META_DIR / PINS_FILE

    def ensure(self) -> Path:
        return init_project(self.root)

    def load(self) -> dict:
        try:
            text = self.pins_path.read_text(encoding="utf-8")
        except OSError as e:
            raise ProjectError(str(e)) from e
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ProjectError(str(e)) from e
        if not isinstance(data, dict):
            raise ProjectError("invalid pins")
        skills = data.get("skills")
        if not isinstance(skills, dict):
            raise ProjectError("invalid pins")
        for v in skills.values():
            if not isinstance(v, dict):
                raise ProjectError("invalid pins")
            ver = v.get("version")
            sha = v.get("content_sha256")
            if not isinstance(ver, str) or not ver.strip():
                raise ProjectError("invalid pins")
            if not isinstance(sha, str) or not sha.strip():
                raise ProjectError("invalid pins")
        return data

    def save(self, data: dict) -> None:
        self.pins_path.parent.mkdir(parents=True, exist_ok=True)
        self.pins_path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def pin(
        self,
        name: str,
        version: str,
        constraint: str,
        content_sha256: str,
        agent: str = "generic",
    ) -> None:
        data = self.load()
        data["skills"][name] = {
            "agent": agent,
            "constraint": constraint,
            "content_sha256": content_sha256,
            "version": version,
        }
        self.save(data)

    def unpin(self, name: str) -> bool:
        data = self.load()
        if name not in data["skills"]:
            return False
        del data["skills"][name]
        self.save(data)
        return True

    def get(self, name: str) -> dict | None:
        data = self.load()
        rec = data["skills"].get(name)
        return dict(rec) if isinstance(rec, dict) else None

    def all_pins(self) -> dict:
        return dict(self.load()["skills"])


def _dirname(name: str) -> str:
    return name.split("/")[-1].strip()


def pin_skill(
    registry,
    project,
    name: str,
    constraint: str = "*",
    agent: str = "generic",
    force: bool = False,
    allow_risky: bool = False,
    with_deps: bool = True,
) -> list[tuple[str, str]]:
    try:
        versions = registry.list_versions(name)
    except RegistryError:
        raise RegistryError(f"unknown skill: {name}") from None
    if not versions:
        raise RegistryError(f"unknown skill: {name}")
    pins = resolve(registry, name, constraint)
    project.ensure()
    out: list[tuple[str, str]] = []
    for n, v in pins:
        if n != name and not with_deps:
            continue
        install(
            registry,
            n,
            v,
            agent=agent,
            force=force,
            allow_risky=allow_risky,
            with_deps=False,
            dest_root=project.root,
        )
        rec = registry.get_version(n, v)
        sha = None
        if isinstance(rec, dict):
            sha = rec.get("content_sha256") or rec.get("content_sha")
        project.pin(n, v, constraint if n == name else "*", sha, agent)
        out.append((n, v))
    return out


def unpin_skill(registry, project, name: str, agent: str = "generic") -> bool:
    if project.get(name) is None:
        return False
    dest = project.root / PROJECT_SKILLS_DIR / _dirname(name)
    if not dest.resolve().is_relative_to(project.root.resolve()):
        raise ProjectError("invalid path")
    if dest.is_dir() and not dest.is_symlink():
        shutil.rmtree(dest)
    elif dest.is_symlink() or dest.exists():
        dest.unlink()
    registry.remove_install_path(name, agent, str(dest))
    project.unpin(name)
    return True


def sync_project(
    registry, project, force: bool = False, allow_risky: bool = False
) -> list[tuple[str, str, str]]:
    pins = project.all_pins()
    if not pins:
        return []
    installs = registry.list_installs()
    out: list[tuple[str, str, str]] = []
    for n, entry in pins.items():
        version = entry["version"]
        agent = entry.get("agent", "generic")
        rec = registry.get_version(n, version)
        if not rec:
            raise RegistryError(f"unknown skill: {n}@{version}")
        reg_sha = rec.get("content_sha256") or rec.get("content_sha")
        if reg_sha != entry.get("content_sha256"):
            raise IntegrityError(f"sha mismatch for {n}@{version}")
        dest = project.root / PROJECT_SKILLS_DIR / _dirname(n)
        dest_s = str(dest)
        found = False
        if not force:
            for row in installs:
                if (
                    row.get("skill_name") == n
                    and row.get("version") == version
                    and row.get("agent") == agent
                    and row.get("dest_path") == dest_s
                ):
                    found = True
                    break
        if found and dest.is_dir():
            out.append((n, version, "unchanged"))
        else:
            install(
                registry,
                n,
                version,
                agent=agent,
                force=True,
                allow_risky=allow_risky,
                with_deps=False,
                dest_root=project.root,
            )
            out.append((n, version, "installed"))
    return out
