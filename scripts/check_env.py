"""Print which expected env vars are set, without leaking the values themselves.

Used at the top of every Routine run so vault misconfiguration shows up
loudly in the logs before pipeline failures cascade.

For each var: prints SET (with length + first/last 2 chars) or MISSING.
Multi-line JSON values (the inlined credentials) get a parse-check too —
if the value is supposed to be JSON but isn't, that's surfaced.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.env import load_dotenv  # noqa: E402

REQUIRED = [
    "STORAGE",
    "SITE_URL",
    "SITE_REPO",
    "SITE_REPO_LOCAL",
    "CONTENT_PATH",
    "R2_ACCESS_KEY",
    "R2_SECRET_KEY",
    "R2_ENDPOINT",
    "R2_BUCKET",
    "GSC_PROPERTY",
    "GSC_OAUTH_CLIENT_JSON",
    "GSC_OAUTH_TOKEN_JSON",
    "FATHOM_API_KEY",
    "FATHOM_SITE_ID",
    "KIT_API_KEY",
    "KIT_PRIMARY_TAG_ID",
    "PSI_API_KEY",
    "DFS_LOGIN",
    "DFS_PASSWORD",
    "GOOGLE_DRIVE_FOLDER_ID",
    "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON",
    "RESEND_API_KEY",
    "REPORT_TO_EMAIL",
    "REPORT_FROM_EMAIL",
]
JSON_VARS = {"GSC_OAUTH_CLIENT_JSON", "GSC_OAUTH_TOKEN_JSON", "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON"}


def fingerprint(value: str) -> str:
    n = len(value)
    if n <= 4:
        return f"len={n}"
    return f"len={n} {value[:2]}…{value[-2:]}"


def check_json(name: str, value: str) -> str | None:
    """Return None if the value parses as JSON or is a path to a JSON file. Else return error string."""
    s = value.strip()
    if s.startswith("{"):
        try:
            json.loads(s)
            return None
        except json.JSONDecodeError as e:
            return f"inline JSON invalid: {e}"
    # Otherwise treat as path
    p = Path(s).expanduser()
    if not p.exists():
        return f"path doesn't exist: {p}"
    try:
        json.loads(p.read_text())
        return None
    except json.JSONDecodeError as e:
        return f"file at {p} not valid JSON: {e}"


def main() -> int:
    load_dotenv()
    missing: list[str] = []
    bad_json: list[tuple[str, str]] = []

    for name in REQUIRED:
        value = os.getenv(name, "")
        if not value:
            print(f"  ✗ {name}: MISSING")
            missing.append(name)
            continue
        suffix = ""
        if name in JSON_VARS:
            err = check_json(name, value)
            if err:
                suffix = f" [JSON ERROR: {err}]"
                bad_json.append((name, err))
            else:
                suffix = " [JSON ok]"
        print(f"  ✓ {name}: {fingerprint(value)}{suffix}")

    if missing or bad_json:
        print(f"\n{len(missing)} missing, {len(bad_json)} malformed JSON")
        return 1
    print(f"\nAll {len(REQUIRED)} env vars present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
