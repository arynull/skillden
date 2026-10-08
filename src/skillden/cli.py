"""Skillden command line interface."""

import argparse
import sqlite3
import sys
from pathlib import Path

from skillden.installer import IntegrityError, SecurityBlocked, extract_bundle
from skillden.installer import install as installer_install
from skillden.installer import uninstall as installer_uninstall
from skillden.manifest import ManifestError, load_manifest, validate_bundle
from skillden.registry import Registry, RegistryError
from skillden.scanner import scan_bundle

try:
    from skillden.agents import AGENTS
except ImportError:  # pragma: no cover
    AGENTS = {
        "claude-code": {},
        "cursor": {},
        "generic": {},
    }

try:
    from skillden.lockfile import write_lock
except ImportError:  # pragma: no cover

    def write_lock(*args, **kwargs):
        raise OSError("lockfile subsystem unavailable")


try:
    from skillden.resolver import ResolutionError
    from skillden.resolver import resolve as _resolve_skill
except ImportError:  # pragma: no cover

    class ResolutionError(Exception):  # type: ignore[no-redef]
        pass

    def _resolve_skill(*args, **kwargs):
        raise ResolutionError("resolver subsystem unavailable")


VERSION = "0.3.0"
AGENT_CHOICES = ["claude-code", "cursor", "generic"]


def _parse_skill_ref(ref):
    ref = (ref or "").strip()
    if "@" in ref:
        name, _, ver = ref.rpartition("@")
        if not name:
            return ref, None
        if not ver:
            return name, None
        return name, ver
    return ref, None


def _safe_get_version(reg, name, version):
    try:
        return reg.get_version(name, version)
    except TypeError as e:
        # Incompatible signature; try next form.
        _ = e
    try:
        return reg.get_version(f"{name}@{version}")
    except TypeError as e:
        # Incompatible signature; try single-arg form.
        _ = e
    # last resort single-arg form
    return reg.get_version(name)


def _safe_list_versions(reg, name):
    return reg.list_versions(name)


def _normalize_install(rec):
    if isinstance(rec, dict):
        n = rec.get("skill_name") or rec.get("name") or ""
        v = rec.get("version") or ""
        a = rec.get("agent") or "generic"
        p = rec.get("dest_path") or rec.get("path") or ""
        return str(n), str(v), str(a), str(p)
    if isinstance(rec, (list, tuple)):
        if len(rec) == 4:
            return str(rec[0]), str(rec[1]), str(rec[2]), str(rec[3])
        if len(rec) == 3:
            return str(rec[0]), str(rec[1]), str(rec[2]), ""
        if len(rec) == 2:
            return str(rec[0]), str(rec[1]), "generic", ""
        if len(rec) == 1:
            return str(rec[0]), "", "generic", ""
    return str(rec), "", "generic", ""


def _extract_search_result(reg, r):
    if isinstance(r, dict):
        name = r.get("name") or r.get("skill") or r.get("id") or ""
        desc = r.get("description", "") or ""
        return str(name), str(desc)
    if isinstance(r, str):
        name = r
        try:
            vers = reg.list_versions(name)
            if vers:
                m = _safe_get_version(reg, name, vers[-1])
                if isinstance(m, dict):
                    return name, str(m.get("description", "") or "")
        except (RegistryError, sqlite3.Error, OSError, ValueError, TypeError) as e:
            # Ignore lookup failures; fall back to name only.
            _ = e
        return name, ""
    if isinstance(r, (list, tuple)):
        if len(r) >= 2:
            return str(r[0]), str(r[1])
        if len(r) == 1:
            return str(r[0]), ""
    return str(r), ""


def _do_install(name, version, agent, force, allow_risky=False, with_deps=True):
    reg = Registry()
    return installer_install(
        reg,
        name,
        version,
        agent=agent,
        force=force,
        allow_risky=allow_risky,
        with_deps=with_deps,
    )


def _do_uninstall(name, agent):
    reg = Registry()
    return installer_uninstall(reg, name, agent=agent)


def _installed_version(reg, name, agent):
    try:
        installs = reg.list_installs() or []
    except (RegistryError, sqlite3.Error, OSError):
        return None
    for inst in installs:
        iname, iver, iagent, _ipath = _normalize_install(inst)
        if iname == name and iagent == agent:
            return iver
    return None


def _write_lock_file(reg, root, constraint, resolved):
    pins = []
    for n, v in resolved:
        try:
            rec = _safe_get_version(reg, n, v)
        except (RegistryError, sqlite3.Error, OSError, ValueError, TypeError):
            rec = None
        digest = ""
        if isinstance(rec, dict):
            digest = rec.get("content_sha256") or rec.get("content_sha") or ""
        pins.append((n, v, str(digest)))
    write_lock(Path("skillden.lock"), root, constraint, pins)


def _cmd_registry_add(args) -> int:
    try:
        manifest = load_manifest(args.skill_json)
    except ManifestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        validate_bundle(manifest, Path(args.bundle))
    except ManifestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        reg = Registry()
        try:
            name, version = reg.add_skill(manifest, args.bundle)
        except TypeError:
            name, version = reg.add_skill(args.bundle)
        print(f"added {name}@{version}")
        try:
            _report = scan_bundle(Path(args.bundle), manifest)
            if _report.verdict != "clean":
                print(
                    f"warning: security scan {_report.verdict}: {_report.summary()}",
                    file=sys.stderr,
                )
        except (OSError, ValueError, TypeError):
            pass
        return 0
    except (ManifestError, RegistryError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _cmd_search(args) -> int:
    reg = Registry()
    try:
        results = reg.search(args.query)
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not results:
        print("no skills found")
        return 0
    print("NAME  VERSIONS  DESCRIPTION")
    for r in results:
        try:
            name, desc = _extract_search_result(reg, r)
        except (RegistryError, sqlite3.Error, OSError, ValueError, TypeError) as e:
            # Skip malformed result; continue with next.
            _ = e
            continue
        if not name:
            continue
        try:
            versions = _safe_list_versions(reg, name)
        except (RegistryError, sqlite3.Error, OSError, ValueError, TypeError):
            # Fall back to versions embedded in result, if any.
            if isinstance(r, dict) and isinstance(r.get("versions"), list):
                versions = r.get("versions")
            else:
                versions = []
        versions_str = ",".join(versions) if versions else ""
        print(f"{name}  {versions_str}  {desc}")
    return 0


def _cmd_info(args) -> int:
    name, ver = _parse_skill_ref(args.skill)
    reg = Registry()
    try:
        if ver is not None:
            try:
                manifest = _safe_get_version(reg, name, ver)
            except RegistryError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            try:
                versions = _safe_list_versions(reg, name)
            except (RegistryError, sqlite3.Error, OSError, ValueError, TypeError):
                # Fall back to requested version on lookup failure.
                versions = [ver]
        else:
            try:
                versions = _safe_list_versions(reg, name)
            except RegistryError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            if not versions:
                print(f"error: unknown skill: {name}", file=sys.stderr)
                return 1
            latest = versions[-1]
            try:
                manifest = _safe_get_version(reg, name, latest)
            except RegistryError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not isinstance(manifest, dict):
        print(f"error: unknown skill: {name}", file=sys.stderr)
        return 1
    print(f"Name: {manifest.get('name', name)}")
    print(f"Description: {manifest.get('description', '')}")
    print(f"Author: {manifest.get('author', '')}")
    print(f"License: {manifest.get('license', '')}")
    try:
        vers_str = ",".join(versions)
    except (TypeError, ValueError):
        vers_str = ""
    print(f"Versions: {vers_str}")
    try:
        installs = reg.list_installs() or []
    except (RegistryError, sqlite3.Error, OSError):
        installs = []
    related = []
    for inst in installs:
        iname, iver, iagent, _ipath = _normalize_install(inst)
        if iname == name:
            related.append((iagent, iver))
    if related:
        print("Installed:")
        for ag, vr in sorted(related):
            print(f"  {ag}: {vr}")
    else:
        print("Installed: none")
    return 0


def _cmd_audit(args) -> int:
    name, ver = _parse_skill_ref(args.skill)
    reg = Registry()
    try:
        with extract_bundle(reg, name, ver) as (bundle_dir, manifest):
            report = scan_bundle(bundle_dir, manifest)
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except IntegrityError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"verdict: {report.verdict}")
    print(report.summary())
    for f in report.findings:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        print(f"{f.severity.upper()}  {f.rule_id}  {loc}  {f.message}")
    return 3 if report.verdict == "blocked" else 0


def _cmd_install(args) -> int:
    name, ver = _parse_skill_ref(args.skill)
    agent = args.agent
    force = bool(getattr(args, "force", False))
    allow_risky = bool(getattr(args, "allow_risky", False))
    with_deps = not bool(getattr(args, "no_deps", False))
    try:
        dest = _do_install(name, ver, agent, force, allow_risky, with_deps)
    except IntegrityError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except SecurityBlocked as e:
        print(
            f"error: security scan blocked install of {name}: "
            f"{e.report.summary()} (use --allow-risky to override)",
            file=sys.stderr,
        )
        return 3
    except ResolutionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    disp_ver = _installed_version(Registry(), name, agent)
    if disp_ver is None:
        if ver is None:
            try:
                reg = Registry()
                try:
                    vers = reg.list_versions(name)
                    disp_ver = vers[-1] if vers else "unknown"
                except (
                    RegistryError,
                    sqlite3.Error,
                    OSError,
                    ValueError,
                    TypeError,
                ) as e:
                    # Ignore lookup failure; disp_ver stays None.
                    _ = e
                    disp_ver = "unknown"
            except (RegistryError, sqlite3.Error, OSError, ValueError, TypeError) as e:
                # Ignore display-version lookup failures.
                _ = e
                disp_ver = "unknown"
        else:
            disp_ver = ver
        if disp_ver is None:
            disp_ver = "unknown"
    print(f"installed {name}@{disp_ver} -> {dest}")
    constraint = ver if ver is not None else "*"
    try:
        reg2 = Registry()
        try:
            resolved = _resolve_skill(reg2, name, constraint)
        except (ResolutionError, RegistryError) as e:
            print(
                f"warning: failed to resolve for lockfile: {e}",
                file=sys.stderr,
            )
            return 0
        try:
            _write_lock_file(reg2, name, constraint, resolved)
        except OSError as e:
            print(
                f"warning: failed to write lockfile: {e}",
                file=sys.stderr,
            )
    except OSError as e:
        print(f"warning: failed to write lockfile: {e}", file=sys.stderr)
    return 0


def _cmd_update(args) -> int:
    name, ver = _parse_skill_ref(args.skill)
    agent = args.agent
    allow_risky = bool(getattr(args, "allow_risky", False))
    constraint = ver if ver is not None else "*"
    reg = Registry()
    try:
        pins = _resolve_skill(reg, name, constraint)
    except ResolutionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    changed: list[tuple[str, str, str]] = []
    for n, v in pins:
        old = _installed_version(reg, n, agent)
        if old == v:
            continue
        try:
            installer_install(
                reg,
                n,
                v,
                agent=agent,
                force=True,
                allow_risky=allow_risky,
                with_deps=False,
            )
        except IntegrityError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        except SecurityBlocked as e:
            print(
                f"error: security scan blocked install of {n}: "
                f"{e.report.summary()} (use --allow-risky to override)",
                file=sys.stderr,
            )
            return 3
        except ResolutionError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        except RegistryError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        old_display = old if old is not None else "none"
        changed.append((n, old_display, v))
    try:
        _write_lock_file(reg, name, constraint, pins)
    except OSError as e:
        print(f"warning: failed to write lockfile: {e}", file=sys.stderr)
    if not changed:
        print("up to date")
    else:
        for n, old_v, new_v in changed:
            print(f"updated {n} {old_v} -> {new_v}")
    return 0


def _cmd_list(args) -> int:
    reg = Registry()
    try:
        installs = reg.list_installs()
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not installs:
        print("nothing installed")
        return 0
    print("NAME@VERSION  AGENT  PATH")
    for inst in installs:
        n, v, a, p = _normalize_install(inst)
        print(f"{n}@{v}  {a}  {p}")
    return 0


def _cmd_uninstall(args) -> int:
    name, _ver = _parse_skill_ref(args.skill)
    agent = args.agent
    try:
        _do_uninstall(name, agent)
    except RegistryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"uninstalled {name}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="skillden")
    parser.add_argument("--version", action="version", version="skillden 0.3.0")
    sub = parser.add_subparsers(dest="command", required=True)

    p_reg = sub.add_parser("registry", help="registry operations")
    reg_sub = p_reg.add_subparsers(dest="registry_cmd", required=True)
    p_add = reg_sub.add_parser("add", help="add a skill to the registry")
    p_add.add_argument("skill_json", help="path to skill.json FILE")
    p_add.add_argument("--bundle", required=True, help="bundle directory")
    p_add.set_defaults(func=_cmd_registry_add)

    p_search = sub.add_parser("search", help="search skills")
    p_search.add_argument("query", help="search query")
    p_search.set_defaults(func=_cmd_search)

    p_info = sub.add_parser("info", help="show skill info")
    p_info.add_argument("skill", help="author/skill[@version]")
    p_info.set_defaults(func=_cmd_info)

    p_audit = sub.add_parser("audit", help="scan a skill bundle for security issues")
    p_audit.add_argument("skill", help="author/skill[@version]")
    p_audit.set_defaults(func=_cmd_audit)

    p_install = sub.add_parser("install", help="install a skill")
    p_install.add_argument("skill", help="author/skill[@spec]")
    p_install.add_argument(
        "--agent",
        choices=AGENT_CHOICES,
        default="generic",
        help="target agent",
    )
    p_install.add_argument("--force", action="store_true", help="force reinstall")
    p_install.add_argument(
        "--allow-risky",
        action="store_true",
        help="install even if the security scan blocks it",
    )
    p_install.add_argument(
        "--no-deps",
        action="store_true",
        help="install only the named skill",
    )
    p_install.set_defaults(func=_cmd_install)

    p_update = sub.add_parser("update", help="update a skill")
    p_update.add_argument("skill", help="author/skill[@constraint]")
    p_update.add_argument(
        "--agent",
        choices=AGENT_CHOICES,
        default="generic",
        help="target agent",
    )
    p_update.add_argument(
        "--allow-risky",
        action="store_true",
        help="install even if the security scan blocks it",
    )
    p_update.set_defaults(func=_cmd_update)

    p_list = sub.add_parser("list", help="list installs")
    p_list.set_defaults(func=_cmd_list)

    p_uninstall = sub.add_parser("uninstall", help="uninstall a skill")
    p_uninstall.add_argument("skill", help="author/skill")
    p_uninstall.add_argument(
        "--agent",
        choices=AGENT_CHOICES,
        default="generic",
        help="target agent",
    )
    p_uninstall.set_defaults(func=_cmd_uninstall)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        func = getattr(args, "func", None)
        if func is None:
            parser.print_help(sys.stderr)
            return 1
        return int(func(args))
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except (OSError, ValueError) as e:
            # Ignore close failures during broken pipe handling.
            _ = e
        try:
            sys.stderr.close()
        except (OSError, ValueError) as e:
            # Ignore close failures during broken pipe handling.
            _ = e
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
