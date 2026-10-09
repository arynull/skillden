# skillden

skillden is the package manager for agent skills.

## Requirements

Python 3.12+ is required.

No other runtime dependencies are required for basic use.

## Install

Install from a local checkout:

    pip install .

After install the `skillden` command should be on your PATH.

## Quickstart

Create a bundle directory containing a `SKILL.md` file and a `skill.json` manifest.

A minimal bundle layout:

    my-skill/SKILL.md
    my-skill/skill.json

Publish the bundle to the local registry:

    skillden registry add skill.json --bundle .

Install the placeholder example skill:

    skillden install acme/demo

Show installed skills:

    skillden list

All examples in this file use placeholder data only, such as `acme/demo`.

## Command Reference

### registry add

Add a skill bundle to the local registry.

Usage:

    registry add <skill.json> --bundle <dir>

Example:

    skillden registry add skill.json --bundle ./my-skill

Registers the version described by `skill.json` and stores hashed bundle content.

### search

Search the registry by name and description.

Usage:

    search <query>

Example:

    skillden search demo

Lists matching `author/skill` entries with versions and descriptions. An empty query lists all entries.

### info

Show details for a skill.

Usage:

    info <author/skill>[@version]

Example:

    skillden info acme/demo@0.1.0

If `@version` is omitted the latest registered version is shown.

### audit

Scan a registered skill bundle for security issues.

Usage:

    audit <author/skill>[@version]

Example:

    skillden audit acme/demo

Prints the verdict (clean, warn, or blocked) and one line per finding
with severity, rule id, file, line, and message. If `@version` is omitted
the latest registered version is scanned. Exit code 3 means the scan
found high-severity findings.

### install

Install a skill from the registry.

Usage:

    install <author/skill>[@spec] [--agent {claude-code,cursor,generic}] [--force] [--allow-risky] [--no-deps]

Example:

    skillden install acme/demo@0.1.0 --agent claude-code

Copies verified bundle files to the agent skills directory. Use `--force` to overwrite an existing install.

`@spec` may be an exact version (`acme/demo@1.2.0`) or a version
constraint (`acme/demo@^1.0.0`); the newest registered version satisfying
the constraint is installed. Dependencies listed in the skill's
`requires` map are resolved and installed first (transitively), each
through the same integrity and security gates. Use `--no-deps` to
install only the named skill. Every successful install writes
`./skillden.lock` pinning the resolved versions.

Example:

    skillden install acme/demo@^1.0.0 --agent claude-code

Every install is scanned before files are copied. High-severity findings
block the install with exit code 3 and leave nothing behind. Medium and
low findings print a warning and the install proceeds. Use `--allow-risky`
to install despite a blocked scan.

### list

List installed skills.

Usage:

    list

Example:

    skillden list

Shows installed `author/skill` entries, versions, and install locations.

### uninstall

Remove an installed skill.

Usage:

    uninstall <author/skill> [--agent {claude-code,cursor,generic}]

Example:

    skillden uninstall acme/demo --agent claude-code

Removes the installed files for the skill. If `--agent` is omitted the default lookup order is used.

### update

Re-resolve a skill's dependencies to the newest satisfying versions.

Usage:

    update <author/skill>[@constraint] [--agent {claude-code,cursor,generic}] [--allow-risky]

Example:

    skillden update acme/demo

Reinstalls any dependency whose resolved version changed and rewrites
`./skillden.lock`. If `@constraint` is omitted every dependency floats
to its newest registered version.

### pin

Pin a skill (and its dependencies) to the current project.

Usage:

    pin <author/skill>[@spec] [--agent {claude-code,cursor,generic}] [--force] [--allow-risky] [--no-deps]

Example:

    skillden pin acme/demo@^1.0.0

Creates `.skillden/` in the current directory when absent (or uses the
nearest parent directory containing `.skillden/`), resolves `@spec` to exact
versions, installs everything into the project-local `.skills/` directory
through the usual integrity and security gates, and records exact versions
plus content hashes in `.skillden/pins.json`.

### unpin

Remove a skill from the current project.

Usage:

    unpin <author/skill> [--agent {claude-code,cursor,generic}]

Example:

    skillden unpin acme/demo

Removes the pin from `.skillden/pins.json`, deletes the project-local skill
directory, and drops the matching install record. Fails with exit code 1
when run outside a project.

### sync

Rebuild the project-local `.skills/` directory from `.skillden/pins.json`.

Usage:

    sync [--force] [--allow-risky]

Example:

    skillden sync

Installs every pinned skill at its exact pinned version, verifying the
recorded content hash against the registry first — any mismatch aborts with
exit code 2. Skills already installed are reported as `unchanged`; use
`--force` to reinstall everything. Fails with exit code 1 when run outside
a project.

### --version

Print the skillden version.

Usage:

    --version

Example:

    skillden --version

### Exit Codes

0 means success.

1 means usage error or not found, for example unknown skill, unknown version, or bad arguments.

2 means integrity failure, for example sha256 mismatch or corrupt registry data. Failed installs do not leave partial output.

3 means a security scan blocked the operation, for example high-severity
findings in the skill bundle. Use `--allow-risky` with install to override.

## skill.json Manifest Format

`skill.json` describes one version of a skill bundle.

| Field | Required | Description |
| --- | --- | --- |
| name | required | Skill identifier in `author/skill` form |
| version | required | Version string |
| description | required | Short human-readable description |
| entry | required | Entry file inside the bundle, usually `SKILL.md` |
| files | required | List of bundle files relative to `--bundle` dir |
| license | optional | License identifier, for example `MIT` |
| agents | optional | List of supported agents from `claude-code`, `cursor`, `generic` |
| requires | optional | Map of prerequisite skill to version constraint, for example {"acme/base": "^1.2.0"} |

Name rules:

- Must be `author/skill` with exactly one slash.
- Author and skill parts must be lowercase.
- Each part must start with a letter or digit.
- Each part may contain letters, digits, `-`, and `_`.
- Each part must be 2 to 64 characters long.

Version rules:

- Must be `X.Y.Z` numeric dot-separated form.
- Each component must be a non-negative integer without leading spaces.
- Examples of valid versions are `0.1.0` and `1.2.3`.
- Prerelease and build suffixes are not accepted.

Example manifest:

    {
        "name": "acme/demo",
        "version": "0.1.0",
        "description": "Demo skill for skillden",
        "entry": "SKILL.md",
        "files": [
            "SKILL.md"
        ],
        "license": "MIT",
        "agents": [
            "claude-code",
            "cursor",
            "generic"
        ],
        "requires": {
            "acme/base": "^1.2.0"
        }
    }

The bundle directory for the above manifest would contain:

    SKILL.md
    skill.json

### Version constraints

Constraints select versions with comma-separated clauses (all must match).

- `>=1.2.0` at least this version
- `<=1.2.0` at most this version
- `>1.2.0` newer than this version
- `<1.2.0` older than this version
- `==1.2.0` or `=1.2.0` exactly this version
- `!=1.2.0` any version except this one
- `1.2.3` bare version means exact
- `^1.2.3` compatible release (`>=1.2.3,<2.0.0`; `^0.2.3` means `>=0.2.3,<0.3.0`; `^0.0.3` means `==0.0.3`)
- `~1.2.3` patch-compatible release (`>=1.2.3,<1.3.0`)
- `*` or empty means any version

Example:

    skillden install acme/demo@">=1.2.0,<2.0.0"

## Agent Support

| Agent | Install Location |
| --- | --- |
| claude-code | ~/.claude/skills |
| cursor | ~/.cursor/skills |
| generic | ./.skills |

Install locations are per skill name. For example installing `acme/demo` for `claude-code` creates `~/.claude/skills/acme-demo` content or an equivalent namespaced directory.

If `--agent` is not given, `generic` is used as the default target.

## Project Environments

A project is any directory containing a `.skillden/` subdirectory. `pin`
records exact skill versions (with content hashes) in
`.skillden/pins.json` and installs the skill files into the project-local
`.skills/` directory, isolated from your global installs.

Usage:

    cd my-project
    skillden pin acme/demo@^1.0.0
    skillden sync

`sync` rebuilds `.skills/` from `.skillden/pins.json` — run it on a fresh
checkout to reproduce the exact same skill tree with no network access.
`unpin` removes a skill from the project.

## Data Directory and Integrity

Registry data and caches live under `~/.skillden`.

Override the location with the environment variable:

    SKILLDEN_DATA_DIR=/tmp/skillden-data skillden list

Integrity model:

- Every bundle file is hashed with sha256 at `registry add` time.
- Hashes are stored in the registry alongside sizes and paths.
- Every `install` re-hashes bundle content before copying.
- Installs are fail-closed: any mismatch aborts with exit code 2.
- Installs write to a temporary directory first, then complete with an atomic move.
- The bundle zip hash is stored at `registry add` time and re-verified
  before extraction on every install.
- Every install scans the verified bundle for prompt-injection,
  exfiltration, remote-code-execution, and destructive patterns; high
  findings block the install (exit code 3) unless `--allow-risky` is given.
- No partial or unverified files are left in the destination on failure.
- `install` resolves `requires` constraints to exact versions and pins
  them in `./skillden.lock` with content hashes for reproducibility.
