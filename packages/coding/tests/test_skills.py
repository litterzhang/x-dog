import pytest
from pathlib import Path
from coding.core.skills import Skill, SkillRegistry

def test_skill_registry_load(tmp_path: Path):
    yaml_content = """
name: my_skill
description: Does things
aliases:
  - ms
prompt: |
  A multiline
  prompt here.
"""
    (tmp_path / "skill1.yaml").write_text(yaml_content)
    
    registry = SkillRegistry(skills_dir=tmp_path)
    
    # should lazily load
    skill = registry.get("my_skill")
    assert skill is not None
    assert skill.name == "my_skill"
    assert skill.description == "Does things"
    assert "A multiline" in skill.prompt_template
    
    # Check alias
    assert registry.get("ms") is skill
    
    # Check listing (unique)
    skills = registry.list_skills()
    assert len(skills) == 1
    assert skills[0].name == "my_skill"
