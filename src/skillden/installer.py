import contextlib
import hashlib
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from .agents import AGENTS, skill_dir
from .registry import RegistryError
from .resolver import ConstraintError as _ConstraintError
from .resolver import ResolutionError
from .resolver import VersionError as _VersionError
from .resolver import resolve as _resolve
from .resolver import select_version as _select_version
from .scanner import SecurityBlocked, scan_bundle


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


def _dest_for(name, agent, dest_root=None) -> Path:
    if dest_root is not None:
        dirname = name.strip().strip("/").split("/")[-1]
        return Path(dest_root) / ".skills" / dirname
    return _resolve_dest(agent, name)


def _install_bundle(
    registry,
    name,
    version,
    agent="generic",
    force=False,
    allow_risky=False,
    dest_root=None,
) -> Path:
    from .agents import AGENTS as _AGENTS
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

    dest = _dest_for(name, agent, dest_root)

    for inst in registry.list_installs():
        if (
            inst.get("skill_name") == name
            and inst.get("version") == version
            and inst.get("agent") == agent
            and str(inst.get("dest_path")) == str(dest)
        ):
            if not force:
                raise _RegistryError(f"already installed: {name} {version} for {agent}")
            break

    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)

    bundle_zip_path = registry.bundle_zip(content_sha256)
    expected_zip_sha256 = rec.get("zip_sha256")
    if expected_zip_sha256:
        actual_zip_sha256 = hashlib.sha256(bundle_zip_path.read_bytes()).hexdigest()
        if actual_zip_sha256 != expected_zip_sha256:
            raise IntegrityError(f"bundle zip hash mismatch for {name} {version}")

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
        scan_report = scan_bundle(Path(tmp), rec.get("manifest") or {})
        if scan_report.verdict == "blocked" and not allow_risky:
            raise SecurityBlocked(scan_report)
        if scan_report.verdict != "clean":
            print(
                f"warning: security scan {scan_report.verdict}: "
                f"{scan_report.summary()}",
                file=sys.stderr,
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


def install(
    registry,
    name,
    version=None,
    agent="generic",
    force=False,
    allow_risky=False,
    with_deps=True,
    dest_root=None,
) -> Path:
    from .agents import AGENTS as _AGENTS
    from .registry import RegistryError as _RegistryError

    if (isinstance(_AGENTS, dict) and agent not in _AGENTS) or (
        isinstance(_AGENTS, (list, tuple, set)) and agent not in _AGENTS
    ):
        raise _RegistryError(f"unknown agent: {agent}")

    try:
        versions = registry.list_versions(name)
    except _RegistryError:
        raise
    except Exception as exc:
        raise _RegistryError(f"unknown skill: {name}") from exc

    if not versions:
        raise _RegistryError(f"unknown skill: {name}")

    if version is None:
        resolved_version = versions[-1]
        resolve_constraint = "*"
    elif version in versions:
        resolved_version = version
        resolve_constraint = version
    else:
        try:
            selected = _select_version(registry, name, version)
        except (_ConstraintError, _VersionError):
            raise ResolutionError(
                f"no version of {name} satisfies {version!r}"
            ) from None
        if selected is None:
            raise ResolutionError(f"no version of {name} satisfies {version!r}")
        resolved_version = selected
        resolve_constraint = version

    root_dest = _dest_for(name, agent, dest_root)
    for inst in registry.list_installs():
        if (
            inst.get("skill_name") == name
            and inst.get("version") == resolved_version
            and inst.get("agent") == agent
            and str(inst.get("dest_path")) == str(root_dest)
        ):
            if not force:
                raise _RegistryError(
                    f"already installed: {name} {resolved_version} for {agent}"
                )
            break

    if not with_deps:
        return _install_bundle(
            registry, name, resolved_version, agent, force, allow_risky, dest_root
        )

    try:
        pins = _resolve(registry, name, resolve_constraint)
    except (_ConstraintError, _VersionError) as exc:
        raise ResolutionError(
            f"no version of {name} satisfies {resolve_constraint!r}"
        ) from exc

    for dep_name, dep_version in pins:
        if dep_name == name:
            continue
        already = False
        dep_dest = _dest_for(dep_name, agent, dest_root)
        for inst in registry.list_installs():
            if (
                inst.get("skill_name") == dep_name
                and inst.get("version") == dep_version
                and inst.get("agent") == agent
                and str(inst.get("dest_path")) == str(dep_dest)
            ):
                already = True
                break
        if already and not force:
            continue
        _install_bundle(
            registry, dep_name, dep_version, agent, True, allow_risky, dest_root
        )

    return _install_bundle(
        registry, name, resolved_version, agent, force, allow_risky, dest_root
    )


@contextlib.contextmanager
def extract_bundle(registry, name, version=None):
    """Yield (bundle_dir, manifest) for a registry version, integrity-verified.

    Verifies the stored zip sha256 (when present), rejects unsafe zip
    member names, extracts to a temp dir, and verifies the content sha256.
    Raises RegistryError for unknown skill/version, IntegrityError on any
    mismatch or unsafe member.
    """
    versions = registry.list_versions(name)
    if version is None:
        if not versions:
            raise RegistryError(f"unknown skill: {name}")
        version = versions[-1]
    elif version not in versions:
        raise RegistryError(f"unknown version: {name} {version}")
    rec = registry.get_version(name, version)
    if rec is None:
        raise RegistryError(f"unknown version: {name} {version}")
    zip_path = registry.bundle_zip(rec["content_sha256"])
    expected = rec.get("zip_sha256")
    if expected and hashlib.sha256(zip_path.read_bytes()).hexdigest() != expected:
        raise IntegrityError(f"bundle zip hash mismatch for {name} {version}")
    with tempfile.TemporaryDirectory(prefix="skillden-audit-") as tmp:
        try:
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                for member in zf.namelist():
                    mp = Path(member)
                    if mp.is_absolute() or ".." in mp.parts:
                        raise IntegrityError(
                            f"unsafe zip member for {name} {version}: {member}"
                        )
                zf.extractall(tmp)
        except zipfile.BadZipFile as e:
            raise IntegrityError(
                f"bundle zip is corrupt for {name} {version}: {e}"
            ) from e
        if _hash_dir(Path(tmp)) != rec["content_sha256"]:
            raise IntegrityError(f"content hash mismatch for {name} {version}")
        yield Path(tmp), rec.get("manifest") or {}


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
