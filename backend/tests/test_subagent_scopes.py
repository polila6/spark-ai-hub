"""Workspace write scopes of parallel sub-agents (pure logic). Run: docker compose run --rm --no-deps backend pytest -q tests"""
from app.services.agent import SUB_AGENT_TOOLS, WS_TOOLS, in_scope, scope_overlaps


def test_scopes_overlap_when_nested_equal_or_whole_workspace():
    assert scope_overlaps(["src"], ["src/app.py"])
    assert scope_overlaps(["src/app.py"], ["src"])
    assert scope_overlaps(["docs"], ["docs"])
    assert scope_overlaps([""], ["tests"])  # "" = the whole workspace
    assert not scope_overlaps(["src"], ["tests"])
    assert not scope_overlaps(["src/a.py", "src/b.py"], ["src/c.py", "README.md"])
    assert not scope_overlaps(["src"], ["src2"])  # a prefix that is not a folder boundary


def test_writes_must_stay_inside_the_scope():
    assert in_scope("src/app.py", ["src"]) and in_scope("README.md", [""])
    assert in_scope("tests/test_x.py", ["src", "tests"])
    assert not in_scope("src2/x.py", ["src"]) and not in_scope("main.py", ["src/main.py/x"])


def test_workspace_tools_are_sub_agent_tools():
    assert WS_TOOLS <= SUB_AGENT_TOOLS and {"ws_write", "ws_run", "ws_read"} <= WS_TOOLS
