"""Tiny .env loader so scripts can be run standalone without a shell wrapper."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    path = path or _find_dotenv()
    if not path or not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _find_dotenv() -> Path | None:
    cur = Path.cwd()
    for parent in [cur, *cur.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return None
