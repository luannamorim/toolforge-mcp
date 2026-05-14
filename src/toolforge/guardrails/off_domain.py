from __future__ import annotations

import re

# Positive tokens — if any appear in the prompt, pass regardless of negative patterns.
# Prevents false positives on operational prompts that happen to contain off-domain words
# (e.g. "read the poem.txt file" or "open the issue called Story Time").
_POSITIVE_RE = re.compile(
    r"\b(file|repo|commit|pr|pull.request|branch|issue|slack|channel|"
    r"directory|path|read_|write_|deploy|build|log|diff|patch|merge|"
    r"github|filesystem|toolforge|codebase|function|class|method|module|"
    r"error|debug|refactor|snippet|script|code)\b",
    re.IGNORECASE,
)

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("poem_story_song", re.compile(
        r"\bwrite (me )?(a |an )?(poem|story|song|essay|novel|haiku|lyrics|limerick)\b",
        re.IGNORECASE,
    )),
    ("joke_trivia", re.compile(
        r"\b(tell me a joke|tell me (a )?trivia|what('?s| is) (a )?fun fact)\b",
        re.IGNORECASE,
    )),
    ("weather", re.compile(
        r"\bwhat('?s| is) the weather\b",
        re.IGNORECASE,
    )),
    ("translate", re.compile(
        r"\btranslate (this|the following|.{1,40}?) ?(to|into) (french|spanish|german|"
        r"italian|portuguese|japanese|chinese|korean|arabic|russian)\b",
        re.IGNORECASE,
    )),
    ("horoscope_recipe", re.compile(
        r"\b(horoscope|astrology|give me a recipe|what('?s| is) my star sign)\b",
        re.IGNORECASE,
    )),
]

_FIXED_DETAIL = (
    "off-domain prompt rejected: ToolForge handles developer-productivity tasks "
    "(filesystem, GitHub, Slack). Please restate your request in that context."
)


def classify_off_domain(text: str) -> str | None:
    """Return the pattern name if the prompt is clearly off-domain, else None.

    False negatives (off-domain prompt that slips through) are tolerable — the
    LLM system prompt also instructs refusal. False positives (blocking a
    legitimate dev prompt) are not — so any positive operational token short-
    circuits the check.
    """
    if not text:
        return None
    if _POSITIVE_RE.search(text):
        return None
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            return name
    return None
