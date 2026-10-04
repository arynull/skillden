import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from .agents import AGENTS, skill_dir
from .registry import RegistryError


class IntegrityError(Exception):
    pass


def _hash_dir(bundle_dir: Path) -> str:
    bundle_dir = Path(bundle_dir)
    entries = []
    for p in bundle_dir.rglob("*"):
        if p.is_file():
            rel = p.relative_to(bundle_dir).as_posix()
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            entries.append((rel, h))
    entries.sort(key=lambda x: x[0])
    joined = ";".join(f"{rel}:{hexdigest}" for rel, hexdigest in entries)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _resolve_dest(agent, name) -> Path:
    # skill_dir is expected to be skill_dir(agent, name).
    # Fall back to alternate ordering for compatibility.
    try:
        return Path(skill_dir(agent, name))
    except TypeError as e:
        # Incompatible signature; try alternate ordering.
        _ = e
    try:
        return Path(skill_dir(name, agent))
    except TypeError as e:
        # Incompatible signature; fall back to single-arg form.
        _ = e
    return Path(skill_dir(name))


def _check_agent(agent):
    agents = AGENTS
    if (isinstance(agents, dict) and agent not in agents) or (
        isinstance(agents, (list, tuple, set)) and agent not in agents
    ):
        raise RegistryError(f"unknown agent: {agent}")


def install(registry, name, version=None, agent="generic", force=False) -> Path:
    from .agents import AGENTS as _AGENTS
    from .agents import skill_dir as _skill_dir
    from .registry import RegistryError as _RegistryError

    if (isinstance(_AGENTS, dict) and agent not in _AGENTS) or (
        isinstance(_AGENTS, (list, tuple, set)) and agent not in _AGENTS
    ):
        raise _RegistryError(f"unknown agent: {agent}")

    versions = registry.list_versions(name)
    if version is None:
        if not versions:
            raise _RegistryError(f"unknown skill: {name}")
        version = versions[-1]
    elif version not in versions:
        raise _RegistryError(f"unknown version: {name} {version}")

    rec = registry.get_version(name, version)
    if rec is None:
        raise _RegistryError(f"unknown version: {name} {version}")

    content_sha256 = rec.get("content_sha256")
    if content_sha256 is None:
        content_sha256 = rec.get("content_sha")
    if content_sha256 is None:
        raise _RegistryError(f"version record missing content_sha256: {name} {version}")

    # Already installed check.
    for inst in registry.list_installs():
        if (
            inst.get("skill_name") == name
            and inst.get("version") == version
            and inst.get("agent") == agent
        ):
            if not force:
                raise _RegistryError(f"already installed: {name} {version} for {agent}")
            break

    # Resolve destination directory.
    try:
        dest = Path(_skill_dir(agent, name))
    except TypeError:
        try:
            dest = Path(_skill_dir(name, agent))
        except TypeError:
            dest = Path(_skill_dir(name))

    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)

    bundle_zip_path = registry.bundle_zip(content_sha256)

    tmp = tempfile.mkdtemp(dir=str(parent), prefix=".tmp-")
    success = False
    try:
        try:
            with zipfile.ZipFile(str(bundle_zip_path), "r") as zf:
                zf.extractall(tmp)
        except zipfile.BadZipFile as e:
            raise IntegrityError(
                f"bundle zip is corrupt for {name} {version}: {e}"
            ) from e

        recomputed = _hash_dir(Path(tmp))
        if recomputed != content_sha256:
            raise IntegrityError(
                f"content hash mismatch for {name} {version}: "
                f"expected {content_sha256}, got {recomputed}"
            )

        if dest.exists() or dest.is_symlink():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()

        os.replace(tmp, dest)

        registry.record_install(name, version, agent, str(dest))
        success = True
        return Path(dest)
    finally:
        if not success:
            shutil.rmtree(tmp, ignore_errors=True)


def uninstall(registry, name, agent="generic"):
    from .registry import RegistryError as _RegistryError

    match = None
    for inst in registry.list_installs():
        if inst.get("skill_name") == name and inst.get("agent") == agent:
            match = inst
            break
    if match is None:
        raise _RegistryError(f"not installed: {name} for {agent}")

    dest = Path(match.get("dest_path", ""))
    try:
        if dest.exists() or dest.is_symlink():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                try:
                    dest.unlink()
                except IsADirectoryError:
                    shutil.rmtree(dest)
    finally:
        registry.remove_install(name, agent)
