import sys
sys.path.insert(0, "/Users/austin/parallax-skill")

from parallax_engine.subagents import (
    AgentDefinition, ALL_SUBAGENTS, SUBAGENT_BY_NAME,
    SCENE_DESIGNER, ASSET_GENERATOR, MASK_AUTHOR, CAMERA_PATHER, QA_CRITIC,
    SONNET, HAIKU,
)

print(f"Total subagents: {len(ALL_SUBAGENTS)}")
for s in ALL_SUBAGENTS:
    tools_str = ", ".join(s.allowed_tools)
    has_agent = "Agent" in s.allowed_tools
    print(f"  {s.name}: model={s.model}, tools=[{tools_str}], has_Agent={has_agent}")
    assert not has_agent, f"FAIL: {s.name} has Agent tool!"

print("Agent tool check: OK (none have it)")
print(f"SUBAGENT_BY_NAME keys: {list(SUBAGENT_BY_NAME.keys())}")
print("import OK")
