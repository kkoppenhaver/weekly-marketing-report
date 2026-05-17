"""fetch-kit: pull tag-based audience size, weekly new-signup history,
and per-post attribution from Kit (ConvertKit) v4.

CC4M stores subscribers by tag (every signup is tagged with the primary
CC4M tag; some posts use additional tags for post-specific lead magnets).
This script keys off tags, not forms.

Uses include_total_count=true with per_page=1 to get counts cheaply
without walking pagination.

API reference: https://developers.kit.com/api-reference
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from lib.env import load_dotenv  # noqa: E402
from lib.storage import get_storage  # noqa: E402
from lib.week import current_week_id, report_week_id, previous_week_id, week_bounds  # noqa: E402

API_BASE = "https://api.kit.com/v4"
WEEKS_BACK = 8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id (default: current week)")
    return parser.parse_args()


def headers(api_key: str) -> dict[str, str]:
    return {"X-Kit-Api-Key": api_key, "Accept": "application/json"}


def tag_count(client: httpx.Client, tag_id: str, *, tagged_after: date | None = None,
              tagged_before: date | None = None) -> int:
    """Return the count of subscribers with this tag, optionally bounded by tagged-at dates."""
    params = {
        "per_page": 1,
        "include_total_count": "true",
        "status": "active",
    }
    if tagged_after:
        params["tagged_after"] = tagged_after.isoformat()
    if tagged_before:
        # Kit's tagged_before is exclusive of the date itself in some endpoints;
        # add a day to make it inclusive of `tagged_before`.
        params["tagged_before"] = (tagged_before + timedelta(days=1)).isoformat()
    resp = client.get(f"{API_BASE}/tags/{tag_id}/subscribers", params=params, timeout=30.0)
    resp.raise_for_status()
    payload = resp.json()
    pagination = payload.get("pagination") or {}
    if "total_count" in pagination:
        return int(pagination["total_count"])
    # Fallback: some Kit responses put total_count at top level.
    if "total_count" in payload:
        return int(payload["total_count"])
    return len(payload.get("subscribers", []))


def signup_url_pathname(raw: str) -> str | None:
    """Normalize a SIGNUP_URL value to a pathname comparable with Fathom data.

    Accepts full URLs ("https://claudecodeformarketers.com/foo/") or bare paths ("/foo").
    Strips query/fragment and trailing slash (root stays '/').
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    path = parsed.path if parsed.scheme else raw.split("?", 1)[0].split("#", 1)[0]
    if not path:
        path = "/"
    if not path.startswith("/"):
        path = "/" + path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path


def fetch_subscribers_by_signup_url(
    client: httpx.Client, tag_id: str, *, tagged_after: date, tagged_before: date
) -> tuple[Counter, int, int]:
    """Page through subscribers tagged in the window; tally SIGNUP_URL custom field.

    Returns (counter of pathname -> count, total_subscribers_seen, missing_signup_url_count).
    """
    counts: Counter = Counter()
    seen = 0
    missing = 0
    params: dict = {
        "per_page": 500,
        "status": "active",
        "tagged_after": tagged_after.isoformat(),
        "tagged_before": (tagged_before + timedelta(days=1)).isoformat(),
        "include_total_count": "false",
    }
    url = f"{API_BASE}/tags/{tag_id}/subscribers"
    while True:
        resp = client.get(url, params=params, timeout=30.0)
        resp.raise_for_status()
        payload = resp.json()
        subs = payload.get("subscribers", [])
        for sub in subs:
            seen += 1
            raw = (sub.get("fields") or {}).get("SIGNUP_URL")
            path = signup_url_pathname(raw) if raw else None
            if not path:
                missing += 1
                continue
            counts[path] += 1
        pagination = payload.get("pagination") or {}
        if not pagination.get("has_next_page"):
            break
        after = pagination.get("end_cursor")
        if not after:
            break
        params["after"] = after
    return counts, seen, missing


def weekly_history(client: httpx.Client, tag_id: str, current_week: str, weeks_back: int) -> list[dict]:
    """Return list of {week, new} for the past `weeks_back` weeks (oldest first)."""
    history: list[dict] = []
    week = current_week
    weeks: list[str] = []
    for _ in range(weeks_back):
        weeks.append(week)
        week = previous_week_id(week)
    weeks.reverse()
    for week_id in weeks:
        start, end = week_bounds(week_id)
        count = tag_count(client, tag_id, tagged_after=start, tagged_before=end)
        history.append({"week": week_id, "new": count})
        print(f"  {week_id}: {count} new")
    return history


def main() -> int:
    load_dotenv()
    args = parse_args()
    week = args.week or report_week_id()

    api_key = os.getenv("KIT_API_KEY")
    primary_tag = os.getenv("KIT_PRIMARY_TAG_ID")
    if not api_key:
        print("error: KIT_API_KEY required", file=sys.stderr)
        return 1
    if not primary_tag:
        print("error: KIT_PRIMARY_TAG_ID required (the master CC4M tag)", file=sys.stderr)
        return 1

    storage = get_storage()
    manifest_key = f"reports/{week}/posts.json"
    if not storage.exists(manifest_key):
        print(f"error: missing {manifest_key} — run fetch-post-manifest first", file=sys.stderr)
        return 1
    manifest = storage.read_json(manifest_key)

    week_start, week_end = week_bounds(week)
    prev_week = previous_week_id(week)
    prev_start, prev_end = week_bounds(prev_week)

    with httpx.Client(headers=headers(api_key)) as client:
        print(f"primary tag {primary_tag}: total active subscribers")
        total = tag_count(client, primary_tag)
        print(f"  total: {total}")

        print(f"this week ({week_start} → {week_end}):")
        this_week_new = tag_count(client, primary_tag, tagged_after=week_start, tagged_before=week_end)
        print(f"  new: {this_week_new}")

        print(f"last week ({prev_start} → {prev_end}):")
        last_week_new = tag_count(client, primary_tag, tagged_after=prev_start, tagged_before=prev_end)
        print(f"  new: {last_week_new}")

        print(f"weekly history (last {WEEKS_BACK} weeks):")
        history = weekly_history(client, primary_tag, week, WEEKS_BACK)

        print(f"signup-url attribution for this week ({week_start} → {week_end}):")
        signup_counts, seen, missing = fetch_subscribers_by_signup_url(
            client, primary_tag, tagged_after=week_start, tagged_before=week_end
        )
        signup_by_path = [
            {"pathname": path, "new": count}
            for path, count in signup_counts.most_common()
        ]
        print(f"  scanned {seen} subscribers, {missing} missing SIGNUP_URL")
        for row in signup_by_path[:5]:
            print(f"  {row['pathname']}: {row['new']}")

        # Per-post attribution: only include posts whose kit_tag_id is genuinely
        # distinct from the primary tag. Posts that explicitly set kitTagId to
        # the primary tag duplicate the audience number with no extra signal.
        per_post: list[dict] = []
        post_tags = [
            p for p in manifest["posts"]
            if p.get("kit_tag_id") and str(p["kit_tag_id"]) != str(primary_tag)
        ]
        if post_tags:
            print(f"per-post attribution: {len(post_tags)} post(s) with custom tags")
            for post in post_tags:
                tag_id = post["kit_tag_id"]
                t = tag_count(client, tag_id)
                tw = tag_count(client, tag_id, tagged_after=week_start, tagged_before=week_end)
                per_post.append(
                    {
                        "slug": post["slug"],
                        "kit_tag_id": tag_id,
                        "total": t,
                        "this_week_new": tw,
                    }
                )
                print(f"  {post['slug']} (tag {tag_id}): total={t}, this_week={tw}")
        else:
            print("per-post attribution: no posts have custom kit_tag_id")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "subscribers": {
            "primary_tag_id": primary_tag,
            "total": total,
            "this_week_new": this_week_new,
            "last_week_new": last_week_new,
            "weekly_history": history,
        },
        "by_post": per_post,
        "signup_urls_this_week": {
            "subscribers_scanned": seen,
            "missing_signup_url": missing,
            "by_pathname": signup_by_path,
        },
    }

    out_key = f"reports/{week}/kit.json"
    storage.write_json(out_key, output)
    print(f"\nwrote {out_key}: {total} total subscribers, {this_week_new} new this week, {len(per_post)} post(s) attributed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
