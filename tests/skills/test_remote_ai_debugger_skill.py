"""Smoke checks for remote-ai-debugger skill content."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    REPO_ROOT
    / "skills"
    / "software-development"
    / "remote-ai-debugger"
    / "SKILL.md"
)
EXAMPLE_DIR = REPO_ROOT / "examples" / "remote-debugger"


def test_remote_ai_debugger_skill_exists():
    assert SKILL_PATH.is_file()


def test_clarify2_write_only_rules():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "Clarify ② before external writes" in text
    assert "External Writes Only" in text
    assert "Read-only external probes" in text
    assert "No Clarify ②" in text  # Walkthrough B read-only SELECT


def test_clarify1_after_pure_repro():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "Clarify ① after pure repro" in text
    assert "tool_calls_made: 0" in text


def test_no_entry_clarify():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "No entry clarify" in text
    assert "Do not call `clarify` in this phase" in text


def test_examples_remote_debugger_deliverables():
    required = [
        "README.zh.md",
        "PLAN.zh.md",
        "REQUIREMENTS.zh.md",
        "config.yaml.example",
        "mcp_servers.example.yaml",
        ".env.example",
        "install-profile.ps1",
        "install-profile.sh",
        "fixtures/repro_bug.py",
    ]
    for name in required:
        assert (EXAMPLE_DIR / name).is_file(), f"missing {name}"
