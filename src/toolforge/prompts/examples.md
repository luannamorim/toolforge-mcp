# Few-Shot Examples — Tool Selection

<!-- Source IDs from evals/golden_tasks.jsonl: ambig-mention-001, ambig-mention-002, cross-003 -->
<!-- Rotate by editing the examples below and updating the source IDs above. -->

These examples demonstrate correct tool routing for common cases.

---

## Example 1 — Explicit mention: filesystem (ambig-mention-001)

User: Use the filesystem server to read README.md

Assistant (tool call):

    {
      "name": "read_file",
      "input": { "path": "README.md" }
    }

Server: filesystem — rule fired: explicit_mention

---

## Example 2 — Explicit mention: GitHub (ambig-mention-002)

User: On github, read the file SECURITY.md from owner anthropics repo anthropic-sdk-python

Assistant (tool call):

    {
      "name": "get_file_contents",
      "input": { "owner": "anthropics", "repo": "anthropic-sdk-python", "path": "SECURITY.md" }
    }

Server: github — rule fired: explicit_mention

---

## Example 3 — Cross-server orchestration (cross-003)

User: Read /tmp/toolforge-demo/hello.txt locally, then look up the latest release tag of the anthropics/anthropic-sdk-python repository on GitHub

Assistant (tool calls, in sequence):

    Step 1
    {
      "name": "read_file",
      "input": { "path": "/tmp/toolforge-demo/hello.txt" }
    }
    Server: filesystem — rule fired: single-candidate

    Step 2
    {
      "name": "get_release",
      "input": { "owner": "anthropics", "repo": "anthropic-sdk-python" }
    }
    Server: github — rule fired: single-candidate
