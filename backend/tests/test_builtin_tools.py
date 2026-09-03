from __future__ import annotations

from geoagent.core.agent import Agent


def test_agent_merges_builtin_tools_and_guidance():
    agent = Agent(name="test", system_prompt="You are a test assistant.")
    names = {t.name for t in agent.tools}
    assert {"task", "list_skills", "load_skill", "compact"} <= names
    assert "todo_write" not in names
    assert "todo_write" not in agent.system_prompt
