"""Skills subsystem — extend agent behavior with ``.md`` skill files.

Exports
-------
Skill : pydantic model for a single skill
SkillManager : load, query, install, and manage skills
parse_skill_file : parse a single ``.md`` file into a Skill
"""

from karna.skills.loader import Skill, SkillManager, parse_skill_file

__all__ = ["Skill", "SkillManager", "parse_skill_file"]
