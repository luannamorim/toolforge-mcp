from pathlib import Path

_DIR = Path(__file__).parent


def load_system_prompt() -> str:
    return (_DIR / "system.md").read_text(encoding="utf-8")


def load_tools_intro() -> str:
    return (_DIR / "tools_intro.md").read_text(encoding="utf-8")
