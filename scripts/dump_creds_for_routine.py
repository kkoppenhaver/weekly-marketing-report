"""Print every env var from .env in paste-ready form for the Routine vault.

For variables whose value is a file path that points at a JSON file
(e.g., GSC_OAUTH_CLIENT_JSON, GSC_OAUTH_TOKEN_JSON,
GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON), the file is read and its contents
are inlined as a compact single-line JSON string. Pipeline scripts
already accept either form so dev (file paths) and prod (inline JSON)
share the same code path.

Variables to skip in production are listed in `ROUTINE_SKIP` —
typically the dev-only `SITE_REPO_LOCAL` and `STORAGE=local`.

DO NOT pipe this output to a file or paste it anywhere except the
Routine vault. It contains every credential. After pasting, clear
your terminal scrollback (Cmd+K in iTerm/Terminal).

Usage:
    python scripts/dump_creds_for_routine.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.env import load_dotenv  # noqa: E402

# These don't belong in the Routine vault — they're dev-only or get a different
# value in production (STORAGE flips local→r2; SITE_REPO_LOCAL becomes the
# Routine's mounted-repo path).
ROUTINE_SKIP = {"SITE_REPO_LOCAL"}
PRODUCTION_OVERRIDES: dict[str, str] = {
    "STORAGE": "r2",
}


def parse_dotenv(path: Path) -> list[tuple[str, str]]:
    """Return [(name, raw_value)] preserving file order and skipping comments / blanks."""
    entries: list[tuple[str, str]] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        value = value.strip()
        # Strip surrounding quotes if present
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        # Strip trailing inline comment
        if "#" in value and " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        entries.append((name, value))
    return entries


def maybe_inline_json(value: str) -> tuple[str, str]:
    """If value is a local file path to an existing JSON file, return
    (compact_json, 'inlined'). Otherwise return (value, 'literal').
    Returns ('', 'missing') only when the path-shaped value can't be read.

    Path heuristic: starts with '/' (absolute) or '~/' (home-relative).
    URLs, repo refs like `org/repo`, and relative paths like
    `src/content/blog` are all treated as literal strings.
    """
    s = value.strip()
    if not s:
        return s, "literal"
    if not (s.startswith("/") or s.startswith("~/")):
        return s, "literal"
    path = Path(s).expanduser()
    if not path.exists():
        return "", "missing"
    if not path.is_file():
        return s, "literal"
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return s, "literal"
    return json.dumps(data, separators=(",", ":")), "inlined"


def main() -> int:
    load_dotenv()
    env_path = Path(".env")
    if not env_path.exists():
        print("error: .env not found in cwd", file=sys.stderr)
        return 1

    entries = parse_dotenv(env_path)
    print("Routine env-var vault — paste each NAME and VALUE pair into the dashboard.")
    print("Variables marked [INLINED] are JSON files whose content has been read in.")
    print("Variables marked [SKIP] are dev-only and should NOT go in the Routine.")
    print("Variables marked [OVERRIDE] have a different production value.\n")
    print("=" * 78)

    for name, raw in entries:
        if name in ROUTINE_SKIP:
            print(f"\n# {name}  [SKIP — dev-only]")
            continue
        if name in PRODUCTION_OVERRIDES:
            override = PRODUCTION_OVERRIDES[name]
            print(f"\n{name}  [OVERRIDE — production value below]")
            print(override)
            continue
        if not raw:
            print(f"\n# {name}  [empty in .env, set it before configuring the Routine]")
            continue

        encoded, status = maybe_inline_json(raw)
        if status == "missing":
            print(f"\n# {name}  [MISSING file at {raw} — fix locally before continuing]")
            continue
        marker = "  [INLINED]" if status == "inlined" else ""
        print(f"\n{name}{marker}")
        print(encoded)

    print("\n" + "=" * 78)
    print("\nReminder: clear scrollback (Cmd+K in iTerm/Terminal) after pasting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
