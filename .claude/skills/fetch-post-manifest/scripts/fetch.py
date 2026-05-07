"""fetch-post-manifest: parse the site's content collection into posts.json.

In dev (STORAGE=local), reads from SITE_REPO_LOCAL on disk.
In prod (Routine), the site repo is expected to already be cloned at SITE_REPO_LOCAL.
Future: clone fresh from SITE_REPO using GITHUB_TOKEN if SITE_REPO_LOCAL is absent.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make ./lib importable when running this script directly.
# fetch.py → scripts → fetch-post-manifest → skills → .claude → repo root
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

import frontmatter  # noqa: E402

from lib.env import load_dotenv  # noqa: E402
from lib.storage import get_storage  # noqa: E402
from lib.week import current_week_id, report_week_id  # noqa: E402

REQUIRED_FIELDS = ("title", "description", "pub_date", "target_keyword")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id, e.g. 2026-W18. Defaults to current week.")
    return parser.parse_args()


def discover_posts(content_dir: Path) -> list[Path]:
    return sorted(p for p in content_dir.rglob("*.md") if p.is_file()) + sorted(
        p for p in content_dir.rglob("*.mdx") if p.is_file()
    )


def parse_post(path: Path, content_dir: Path, repo_root: Path, site_url: str) -> dict:
    post = frontmatter.load(path)
    fm = post.metadata
    slug = path.stem
    rel_url_path = f"/blog/{slug}"

    pub_date = fm.get("pubDate")
    updated_date = fm.get("updatedDate")

    record = {
        "slug": slug,
        "url": site_url.rstrip("/") + rel_url_path,
        "path": str(path.relative_to(repo_root)),
        "title": fm.get("title", ""),
        "description": fm.get("description", ""),
        "pub_date": _to_iso_date(pub_date),
        "updated_date": _to_iso_date(updated_date),
        "target_keyword": fm.get("target_keyword"),
        "secondary_keywords": fm.get("secondary_keywords", []) or [],
        "categories": fm.get("categories", []) or [],
        "tags": fm.get("tags", []) or [],
        "draft": bool(fm.get("draft", False)),
        "kit_tag_id": fm.get("kitTagId"),
        "kit_form_id": fm.get("kitFormId"),
    }

    missing = [f for f in REQUIRED_FIELDS if not _has_value(record, f)]
    record["frontmatter_complete"] = not missing
    record["missing_fields"] = missing
    return record


def _has_value(record: dict, field: str) -> bool:
    val = record.get(field)
    if val is None:
        return False
    if isinstance(val, str) and not val.strip():
        return False
    if isinstance(val, list) and not val:
        return False
    return True


def _to_iso_date(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def build_manifest_issues(posts: list[dict]) -> list[dict]:
    issues = []
    for post in posts:
        for field in post["missing_fields"]:
            issues.append(
                {
                    "slug": post["slug"],
                    "issue": f"missing {field}",
                    "severity": "warn",
                }
            )
    return issues


def main() -> int:
    load_dotenv()
    args = parse_args()
    week = args.week or report_week_id()

    site_url = os.getenv("SITE_URL", "https://claudecodeformarketers.com")
    repo_local = os.getenv("SITE_REPO_LOCAL")
    content_path = os.getenv("CONTENT_PATH", "src/content/blog")

    if not repo_local:
        print("error: SITE_REPO_LOCAL is required (set in .env)", file=sys.stderr)
        return 1

    repo_root = Path(repo_local).expanduser()
    content_dir = repo_root / content_path
    if not content_dir.is_dir():
        print(f"error: content dir not found: {content_dir}", file=sys.stderr)
        return 1

    post_paths = discover_posts(content_dir)
    posts = [parse_post(p, content_dir, repo_root, site_url) for p in post_paths]

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "post_count": len(posts),
        "posts": posts,
        "manifest_issues": build_manifest_issues(posts),
    }

    storage = get_storage()
    key = f"reports/{week}/posts.json"
    storage.write_json(key, manifest)

    issues_count = len(manifest["manifest_issues"])
    print(f"wrote {key}: {len(posts)} posts, {issues_count} frontmatter issue(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
