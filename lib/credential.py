"""Load JSON credentials from either a file path or inline JSON content.

Local dev keeps credentials as files in ~/.config/cc4m-report/.
The Routine has no filesystem to mount those files into, so we accept the
same JSON content inlined as a multi-line environment variable.

Heuristic: if the value starts with `{` it's inline JSON; otherwise it's a
file path (with `~` expansion).
"""

from __future__ import annotations

import json
from pathlib import Path


def load_credentials_json(env_value: str) -> dict:
    """Return the parsed credentials dict.

    `env_value` is either inline JSON content (preferred for production
    Routines) or a path to a JSON file (preferred for local dev).
    """
    s = (env_value or "").strip()
    if not s:
        raise ValueError("empty credentials value")
    if s.startswith("{"):
        return json.loads(s)
    return json.loads(Path(s).expanduser().read_text())
