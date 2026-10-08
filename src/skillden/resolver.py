"""Version parsing, constraint matching, and dependency resolution."""

from __future__ import annotations

__all__ = [
    "ConstraintError",
    "ResolutionError",
    "VersionError",
    "compare",
    "parse_constraint",
    "parse_version",
    "resolve",
    "satisfies",
    "select_version",
    "sort_versions",
]


class VersionError(Exception):
    pass


class ConstraintError(Exception):
    pass


class ResolutionError(Exception):
    pass


def parse_version(s: str) -> tuple[int, int, int]:
    if not isinstance(s, str):
        raise VersionError(f"invalid version: {s!r}")
    text = s.strip()
    parts = text.split(".")
    if len(parts) != 3:
        raise VersionError(f"invalid version: {s!r}")
    nums: list[int] = []
    for part in parts:
        if not part.isdigit():
            raise VersionError(f"invalid version: {s!r}")
        nums.append(int(part))
    return (nums[0], nums[1], nums[2])


def compare(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def sort_versions(versions: list[str]) -> list[str]:
    return sorted(versions, key=parse_version)


def parse_constraint(
    s: str,
) -> tuple[tuple[str, tuple[int, int, int] | None], ...]:
    if not isinstance(s, str):
        raise ConstraintError(f"invalid constraint: {s!r}")
    text = s.strip()
    if text == "" or text == "*":
        return (("any", None),)
    raw_clauses = text.split(",")
    out: list[tuple[str, tuple[int, int, int] | None]] = []
    for raw in raw_clauses:
        clause = raw.strip()
        if clause == "":
            raise ConstraintError(f"invalid constraint: {s!r}: empty clause")
        if clause == "*":
            out.append(("any", None))
            continue
        op: str
        ver_str: str
        if clause.startswith(">="):
            op = ">="
            ver_str = clause[2:].strip()
        elif clause.startswith("<="):
            op = "<="
            ver_str = clause[2:].strip()
        elif clause.startswith("=="):
            op = "=="
            ver_str = clause[2:].strip()
        elif clause.startswith("!="):
            op = "!="
            ver_str = clause[2:].strip()
        elif clause.startswith(">"):
            op = ">"
            ver_str = clause[1:].strip()
        elif clause.startswith("<"):
            op = "<"
            ver_str = clause[1:].strip()
        elif clause.startswith("="):
            op = "=="
            ver_str = clause[1:].strip()
        elif clause.startswith("^"):
            op = "^"
            ver_str = clause[1:].strip()
        elif clause.startswith("~"):
            op = "~"
            ver_str = clause[1:].strip()
        else:
            op = "=="
            ver_str = clause
        if ver_str == "":
            raise ConstraintError(f"invalid constraint: {s!r}: missing version")
        try:
            ver = parse_version(ver_str)
        except VersionError as exc:
            raise ConstraintError(
                f"invalid constraint: {s!r}: bad version {ver_str!r}"
            ) from exc
        out.append((op, ver))
    if not out:
        raise ConstraintError(f"invalid constraint: {s!r}")
    return tuple(out)


def _satisfies_caret(v: tuple[int, int, int], base: tuple[int, int, int]) -> bool:
    if v < base:
        return False
    major, minor, _patch = base
    if major > 0:
        return v < (major + 1, 0, 0)
    if minor > 0:
        return v < (0, minor + 1, 0)
    return v == base


def _satisfies_tilde(v: tuple[int, int, int], base: tuple[int, int, int]) -> bool:
    if v < base:
        return False
    major, minor, _patch = base
    return v < (major, minor + 1, 0)


def satisfies(version: str, constraint: str) -> bool:
    v = parse_version(version)
    clauses = parse_constraint(constraint)
    for op, cver in clauses:
        if op == "any":
            continue
        if cver is None:
            continue
        if op == "==":
            if v != cver:
                return False
        elif op == "!=":
            if v == cver:
                return False
        elif op == ">=":
            if not v >= cver:
                return False
        elif op == "<=":
            if not v <= cver:
                return False
        elif op == ">":
            if not v > cver:
                return False
        elif op == "<":
            if not v < cver:
                return False
        elif op == "^":
            if not _satisfies_caret(v, cver):
                return False
        elif op == "~":
            if not _satisfies_tilde(v, cver):
                return False
        else:
            raise ConstraintError(f"unknown operator: {op}")
    return True


def select_version(registry, name: str, constraint: str) -> str | None:
    try:
        versions = registry.list_versions(name)
    except Exception:
        return None
    if not versions:
        return None
    parse_constraint(constraint)
    best: str | None = None
    best_t: tuple[int, int, int] | None = None
    for cand in versions:
        try:
            ok = satisfies(cand, constraint)
        except VersionError:
            continue
        if not ok:
            continue
        try:
            t = parse_version(cand)
        except VersionError:
            continue
        if best_t is None or t > best_t:
            best_t = t
            best = cand
    return best


def resolve(
    registry, root_name: str, root_constraint: str = "*"
) -> list[tuple[str, str]]:
    if not isinstance(root_name, str) or not root_name.strip():
        raise ResolutionError(f"unknown skill: {root_name}")
    if root_constraint is None:
        root_constraint = "*"
    if isinstance(root_constraint, str) and root_constraint.strip() == "":
        root_constraint = "*"

    constraints: dict[str, list[str]] = {}
    pinned: dict[str, str] = {}
    expanded: set[str] = set()

    def _list(name: str) -> list[str]:
        try:
            vers = registry.list_versions(name)
        except Exception:
            raise ResolutionError(f"unknown skill: {name}") from None
        if not vers:
            raise ResolutionError(f"unknown skill: {name}")
        return list(vers)

    def _requires(name: str, version: str) -> dict[str, str]:
        try:
            rec = registry.get_version(name, version)
        except Exception:
            raise ResolutionError(f"unknown skill: {name}") from None
        if rec is None:
            raise ResolutionError(f"unknown skill: {name}")
        manifest = rec.get("manifest") or {}
        reqs = manifest.get("requires") or {}
        if not isinstance(reqs, dict):
            return {}
        return dict(reqs)

    def _best(vers: list[str], clist: list[str]) -> str | None:
        best: str | None = None
        best_t: tuple[int, int, int] | None = None
        for cand in vers:
            try:
                t = parse_version(cand)
            except VersionError:
                continue
            ok = True
            for c in clist:
                if not satisfies(cand, c):
                    ok = False
                    break
            if not ok:
                continue
            if best_t is None or t > best_t:
                best_t = t
                best = cand
        return best

    def _ensure(name: str, constr: str, stack: list[str]) -> None:
        if name in stack:
            idx = stack.index(name)
            cycle = [*stack[idx:], name]
            raise ResolutionError(f"circular dependency: {' -> '.join(cycle)}")
        lst = constraints.setdefault(name, [])
        if constr not in lst:
            lst.append(constr)
        vers = _list(name)
        best = _best(vers, lst)
        if best is None:
            if len(lst) == 1:
                raise ResolutionError(f"no version of {name} satisfies {lst[0]}")
            raise ResolutionError(
                f"conflicting constraints for {name}: {' vs '.join(lst)}"
            )
        old = pinned.get(name)
        if old == best and name in expanded:
            return
        pinned[name] = best
        if old != best:
            expanded.discard(name)
        reqs = _requires(name, best)
        new_stack = [*stack, name]
        for dep in sorted(reqs):
            dep_c = reqs[dep]
            if not isinstance(dep_c, str):
                raise ResolutionError(
                    f"conflicting constraints for {name}: invalid constraint"
                )
            _ensure(dep, dep_c, new_stack)
        expanded.add(name)

    _ensure(root_name, root_constraint, [])

    order: list[tuple[str, str]] = []
    visited: set[str] = set()

    def _visit(n: str, stack: list[str]) -> None:
        if n in stack:
            idx = stack.index(n)
            cycle = [*stack[idx:], n]
            raise ResolutionError(f"circular dependency: {' -> '.join(cycle)}")
        if n in visited:
            return
        stack.append(n)
        reqs = _requires(n, pinned[n])
        for dep in sorted(reqs):
            _visit(dep, stack)
        stack.pop()
        visited.add(n)
        order.append((n, pinned[n]))

    _visit(root_name, [])
    return order
