from pathlib import Path

_DIR = Path(__file__).parent


def _load(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


def load_system_prompt() -> str:
    return _load("system.md")


def load_tools_intro() -> str:
    return _load("tools_intro.md")


def load_examples() -> str:
    return _load("examples.md")
