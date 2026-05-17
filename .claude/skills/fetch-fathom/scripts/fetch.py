"""fetch-fathom: pull 90-day pageviews/uniques per pathname, current-week referrers,
and tracked event counts (this week vs. last week) from Fathom Analytics.

Aggregates Fathom's daily data into ISO weeks (Fathom doesn't support week
grouping natively). Joins pathnames against posts.json so the report knows
which post each row maps to.

API reference: https://usefathom.com/api
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from lib.env import load_dotenv  # noqa: E402
from lib.storage import get_storage  # noqa: E402
from lib.week import current_week_id, report_week_id, week_bounds  # noqa: E402

API_BASE = "https://api.usefathom.com/v1"
WINDOW_DAYS = 90


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id (default: current week)")
    return parser.parse_args()


def headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def get(client: httpx.Client, path: str, params: dict | None = None) -> Any:
    resp = client.get(f"{API_BASE}{path}", params=params or {}, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def list_events(client: httpx.Client, site_id: str) -> list[dict]:
    """Return all events configured on the site, paginating if needed."""
    events: list[dict] = []
    starting_after: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 100}
        if starting_after:
            params["starting_after"] = starting_after
        payload = get(client, f"/sites/{site_id}/events", params)
        page = payload.get("data", [])
        events.extend(page)
        if not page or len(page) < 100:
            break
        starting_after = page[-1]["id"]
    return events


def fetch_pageviews_by_path(client: httpx.Client, site_id: str, start: date, end: date) -> list[dict]:
    """Daily pageviews + uniques grouped by pathname over the date range."""
    rows: list[dict] = []
    # Fathom's aggregations endpoint paginates; pull until no more rows.
    # field_grouping=pathname returns one row per (pathname, date_grouping bucket).
    params = {
        "entity": "pageview",
        "entity_id": site_id,
        "aggregates": "pageviews,visits,uniques,avg_duration",
        "field_grouping": "pathname",
        "date_grouping": "day",
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "timezone": "UTC",
        "limit": 1000,
    }
    # Walk pages by adjusting `starting_after` if Fathom paginates this endpoint.
    # Empirically the aggregations endpoint returns the full result up to `limit`
    # and clients re-request with a higher limit if needed. We bump high.
    payload = get(client, "/aggregations", params)
    rows.extend(payload if isinstance(payload, list) else payload.get("data", []))
    return rows


def fetch_top_referrers(client: httpx.Client, site_id: str, start: date, end: date, limit: int = 25) -> list[dict]:
    params = {
        "entity": "pageview",
        "entity_id": site_id,
        "aggregates": "pageviews,uniques",
        "field_grouping": "referrer_hostname",
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "sort_by": "pageviews:desc",
        "limit": limit,
        "timezone": "UTC",
    }
    payload = get(client, "/aggregations", params)
    return payload if isinstance(payload, list) else payload.get("data", [])


def fetch_event_count(client: httpx.Client, event_id: str, start: date, end: date) -> int:
    params = {
        "entity": "event",
        "entity_id": event_id,
        "aggregates": "conversions",
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "timezone": "UTC",
    }
    payload = get(client, "/aggregations", params)
    rows = payload if isinstance(payload, list) else payload.get("data", [])
    if not rows:
        return 0
    val = rows[0].get("conversions") or 0
    return int(val)


def fetch_event_by_pathname(client: httpx.Client, event_id: str, start: date, end: date) -> list[dict]:
    """Conversions for an event grouped by the pathname where it fired."""
    params = {
        "entity": "event",
        "entity_id": event_id,
        "aggregates": "conversions",
        "field_grouping": "pathname",
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "sort_by": "conversions:desc",
        "limit": 1000,
        "timezone": "UTC",
    }
    payload = get(client, "/aggregations", params)
    rows = payload if isinstance(payload, list) else payload.get("data", [])
    out: list[dict] = []
    for row in rows:
        path = row.get("pathname")
        if not path:
            continue
        conv = int(row.get("conversions") or 0)
        if conv <= 0:
            continue
        out.append({"pathname": _normalize_path(path), "conversions": conv})
    return out


def aggregate_to_weeks(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """Convert daily rows → {pathname: {week_id: {pageviews, uniques, ...}}}."""
    out: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {
        "pageviews": 0,
        "visits": 0,
        "uniques": 0,
        "duration_sum": 0.0,  # used to compute weighted avg_duration; dropped before output
    }))
    for row in rows:
        pathname = row.get("pathname")
        date_str = row.get("date")
        if not pathname or not date_str:
            continue
        pathname = _normalize_path(pathname)
        d = date.fromisoformat(date_str[:10])
        year, week, _ = d.isocalendar()
        week_id = f"{year}-W{week:02d}"
        bucket = out[pathname][week_id]
        pv = int(row.get("pageviews") or 0)
        bucket["pageviews"] += pv
        bucket["visits"] += int(row.get("visits") or 0)
        bucket["uniques"] += int(row.get("uniques") or 0)
        avg_dur = float(row.get("avg_duration") or 0)
        bucket["duration_sum"] += avg_dur * pv  # weight by pageviews

    # Finalize: compute avg_duration, drop intermediate sum
    final: dict[str, dict[str, dict]] = {}
    for path, weeks in out.items():
        final[path] = {}
        for week_id, b in weeks.items():
            avg = (b["duration_sum"] / b["pageviews"]) if b["pageviews"] else 0
            final[path][week_id] = {
                "pageviews": b["pageviews"],
                "visits": b["visits"],
                "uniques": b["uniques"],
                "avg_duration": round(avg, 1),
            }
    return final


def url_to_pathname(url: str) -> str:
    return _normalize_path(urlparse(url).path or "/")


def _normalize_path(path: str) -> str:
    """Strip trailing slash so /blog/foo/ and /blog/foo compare equal. Root stays '/'."""
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path


def main() -> int:
    load_dotenv()
    args = parse_args()
    week = args.week or report_week_id()

    api_key = os.getenv("FATHOM_API_KEY")
    site_id = os.getenv("FATHOM_SITE_ID")
    if not api_key or not site_id:
        print("error: FATHOM_API_KEY and FATHOM_SITE_ID required", file=sys.stderr)
        return 1

    storage = get_storage()
    manifest_key = f"reports/{week}/posts.json"
    if not storage.exists(manifest_key):
        print(f"error: missing {manifest_key} — run fetch-post-manifest first", file=sys.stderr)
        return 1
    manifest = storage.read_json(manifest_key)

    week_start, week_end = week_bounds(week)
    window_end = week_end
    window_start = week_end - timedelta(days=WINDOW_DAYS - 1)
    prev_start = week_start - timedelta(days=7)
    prev_end = week_start - timedelta(days=1)

    with httpx.Client(headers=headers(api_key)) as client:
        print(f"fetching pageviews {window_start} → {window_end} (90d)")
        pv_rows = fetch_pageviews_by_path(client, site_id, window_start, window_end)
        print(f"  got {len(pv_rows)} daily rows")

        print(f"fetching top referrers for week {week} ({week_start} → {week_end})")
        referrers = fetch_top_referrers(client, site_id, week_start, week_end)

        print("listing site events")
        events = list_events(client, site_id)
        print(f"  found {len(events)} event(s): {[e.get('name') for e in events]}")

        event_data: dict[str, dict] = {}
        for event in events:
            name = event.get("name") or event.get("id")
            ev_id = event.get("id")
            if not ev_id:
                continue
            this_week = fetch_event_count(client, ev_id, week_start, week_end)
            last_week = fetch_event_count(client, ev_id, prev_start, prev_end)
            by_path = fetch_event_by_pathname(client, ev_id, week_start, week_end)
            event_data[name] = {
                "id": ev_id,
                "this_week": this_week,
                "last_week": last_week,
                "this_week_by_pathname": by_path,
            }
            if by_path:
                top = ", ".join(f"{r['pathname']}={r['conversions']}" for r in by_path[:3])
                print(f"  {name}: this_week={this_week}, top pages: {top}")

    by_path_weekly = aggregate_to_weeks(pv_rows)
    known_paths = {url_to_pathname(p["url"]) for p in manifest["posts"]}
    by_page = {path: {"weekly": [
        {"week": week_id, **vals}
        for week_id, vals in sorted(weeks.items())
    ]} for path, weeks in by_path_weekly.items() if path in known_paths or path == "/"}

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "by_page": by_page,
        "top_referrers_this_week": referrers,
        "events": event_data,
    }

    out_key = f"reports/{week}/fathom.json"
    storage.write_json(out_key, output)

    matched = len(by_page)
    total_paths = len(by_path_weekly)
    unmatched = total_paths - matched
    print(
        f"\nwrote {out_key}: {matched} known path(s), "
        f"{unmatched} unknown path(s) skipped, "
        f"{len(referrers)} referrer(s), "
        f"{len(event_data)} event(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
