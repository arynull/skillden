"""Agent skill directory mappings."""

from pathlib import Path

AGENTS = {
    "claude-code": "~/.claude/skills",
    "cursor": "~/.cursor/skills",
    "generic": "./.skills",
}

__all__ = ["AGENTS", "skill_dir"]


def skill_dir(agent: str, skill_name: str) -> Path:
    """Return install directory for a skill for the given agent.

    Args:
        agent: Agent key from AGENTS.
        skill_name: Skill name in ``author/skill`` form. The directory
            name is the last ``/``-separated segment.

    Returns:
        Path to the skill directory.

    Raises:
        ValueError: If the agent is unknown or the skill name is invalid.
    """
    if agent not in AGENTS:
        raise ValueError(f"Unknown agent: {agent!r}")

    if not isinstance(skill_name, str) or not skill_name.strip():
        raise ValueError(f"Invalid skill name: {skill_name!r}")

    dirname = skill_name.strip().strip("/").split("/")[-1].strip()
    if not dirname:
        raise ValueError(f"Invalid skill name: {skill_name!r}")

    base = Path(AGENTS[agent]).expanduser()
    return base / dirname
