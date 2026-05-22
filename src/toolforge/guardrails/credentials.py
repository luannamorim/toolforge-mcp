from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("github_pat", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[bparso]-[A-Za-z0-9-]{10,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
]


def scan_credentials(text: str) -> str | None:
    """Return the pattern name of the first credential found, or None.

    Intentionally returns no matched text — callers must not include the
    detected value in logs or error responses (SPEC.md §Input guardrails).
    """
    if not text:
        return None
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            return name
    return None
