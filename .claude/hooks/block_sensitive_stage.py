#!/usr/bin/env python3
"""Block git add of sensitive files before they reach the index."""
import json
import re
import sys

SENSITIVE = re.compile(
    r'(\.env[.\-_\w]*|\.pem\b|\.key\b|\.p12\b|\.pfx\b|credentials[\w.\-_]*|secrets[\w.\-_]*)',
    re.IGNORECASE,
)

payload = json.load(sys.stdin)
if payload.get("tool_name") != "Bash":
    sys.exit(0)

command = payload.get("tool_input", {}).get("command", "")
if not re.search(r'\bgit\s+add\b', command):
    sys.exit(0)

match = SENSITIVE.search(command)
if match:
    print(
        f"BLOCKED: '{match.group()}' matches a sensitive file pattern "
        f"(.env* / .pem / .key / credentials* / secrets*). "
        f"Add to .gitignore or remove from the stage command."
    )
    sys.exit(2)
