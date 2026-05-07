"""Print the three JSON credentials as single-line strings, ready to paste
into the Routine's environment-variable vault.

Reads the file paths from the local .env, dumps the file contents as
compact single-line JSON. The pipeline scripts already accept either a
path or inline JSON, so production and dev can share the same code path.

DO NOT commit the output of this script anywhere. It contains secrets.
Run interactively, copy-paste each value into the Routine's vault, and
clear your terminal scrollback after.

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

VARS_TO_DUMP = (
    "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON",
    "GSC_OAUTH_CLIENT_JSON",
    "GSC_OAUTH_TOKEN_JSON",
)


def main() -> int:
    load_dotenv()
    print("Paste each of these into the Routine's environment-variable vault.")
    print("Each is a single line. Be careful with copy-paste — newlines will corrupt the JSON.\n")
    print("=" * 70)
    for name in VARS_TO_DUMP:
        path_str = os.getenv(name)
        if not path_str:
            print(f"\n# {name} — not set in .env, skipping")
            continue
        path = Path(path_str).expanduser()
        if not path.exists():
            print(f"\n# {name} — file not found at {path}, skipping")
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"\n# {name} — file at {path} isn't valid JSON ({e}), skipping")
            continue
        compact = json.dumps(data, separators=(",", ":"))
        print(f"\n{name}:")
        print(compact)
    print("\n" + "=" * 70)
    print("\nAfter pasting, clear scrollback (Cmd+K in iTerm/Terminal).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
