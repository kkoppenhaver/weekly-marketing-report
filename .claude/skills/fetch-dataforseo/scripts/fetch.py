"""fetch-dataforseo: search volume, keyword difficulty, intent, and SERP features
for the union of declared keywords (from post frontmatter) and GSC-discovered
keywords (queries with meaningful impressions where we rank 1-30).

Cost discipline:
  - Hard cap: MAX_KEYWORDS keywords per run.
  - 14-day persistent cache at cache/dataforseo/{slug}.json. Volume/KD change
    slowly, so re-runs within the cache window cost zero.
  - SERP enrichment is opt-in via TOP_N_FOR_SERP — only the top N candidates
    (by impressions × inverse-position) get the more expensive SERP call.
  - Every API call is logged with its keyword and a synthetic cost estimate.

API: https://api.dataforseo.com/v3
Auth: Basic auth, login + API password (not your account password).
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from lib.env import load_dotenv  # noqa: E402
from lib.storage import Storage, get_storage  # noqa: E402
from lib.week import current_week_id, report_week_id  # noqa: E402

API_BASE = "https://api.dataforseo.com/v3"
LANGUAGE_CODE = "en"
LOCATION_CODE = 2840  # United States — change if the audience is elsewhere

# Cost discipline. Raise this if cap is cutting off useful candidates;
# keyword_overview is batched and cheap (~$0.0006 per keyword), so even 100
# keywords/run costs ~$0.06 + cache hits over time.
MAX_KEYWORDS = 40
TOP_N_FOR_SERP = 10
CACHE_TTL_DAYS = 14
GSC_MIN_IMPRESSIONS = 30  # over the 90d window
GSC_MIN_POSITION = 1
GSC_MAX_POSITION = 30

# Estimated unit costs (per DataForSEO docs)
COST_KEYWORD_OVERVIEW = 0.0006
COST_SERP_ADVANCED = 0.002


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id (default: current week)")
    parser.add_argument("--dry-run", action="store_true", help="List candidates and skip API calls")
    parser.add_argument(
        "--no-serp",
        action="store_true",
        help="Skip the SERP enrichment phase (cuts cost ~half)",
    )
    return parser.parse_args()


def slugify(keyword: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")[:80]


def cache_key(keyword: str) -> str:
    return f"cache/dataforseo/{slugify(keyword)}.json"


def cache_get(storage: Storage, keyword: str) -> dict | None:
    key = cache_key(keyword)
    if not storage.exists(key):
        return None
    try:
        entry = storage.read_json(key)
    except Exception:
        return None
    fetched = entry.get("fetched_at")
    if not fetched:
        return None
    age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched)
    if age > timedelta(days=CACHE_TTL_DAYS):
        return None
    return entry


def cache_put(storage: Storage, keyword: str, payload: dict, has_serp: bool) -> None:
    storage.write_json(cache_key(keyword), {
        "keyword": keyword,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "payload": payload,
        "has_serp": has_serp,
    })


def auth_header(login: str, password: str) -> str:
    raw = f"{login}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def aggregate_query_stats(weekly: list[dict]) -> tuple[int, float]:
    """Return (total_impressions, impressions-weighted avg position)."""
    impr = sum(w.get("impressions", 0) for w in weekly)
    if not impr:
        return 0, 0.0
    pos_w = sum(w.get("position", 0) * w.get("impressions", 0) for w in weekly)
    return impr, pos_w / impr


def collect_candidates(posts_doc: dict, gsc: dict | None) -> list[tuple[str, dict]]:
    """Return list of (keyword, source_metadata). Order = priority (declared first, then GSC)."""
    seen: dict[str, dict] = {}

    # Declared keywords first — they're load-bearing for tracking
    for post in posts_doc.get("posts") or []:
        if post.get("draft"):
            continue
        if t := (post.get("target_keyword") or "").strip().lower():
            if t not in seen:
                seen[t] = {"source": "declared_target", "post_slug": post["slug"]}
        for s in post.get("secondary_keywords") or []:
            sk = (s or "").strip().lower()
            if sk and sk not in seen:
                seen[sk] = {"source": "declared_secondary", "post_slug": post["slug"]}

    # GSC-discovered next, sorted by opportunity score (impressions * 1/position)
    if gsc:
        gsc_candidates: list[tuple[float, str, dict]] = []
        for query, payload in (gsc.get("by_query") or {}).items():
            impr, avg_pos = aggregate_query_stats(payload.get("weekly") or [])
            if impr < GSC_MIN_IMPRESSIONS or not (GSC_MIN_POSITION <= avg_pos <= GSC_MAX_POSITION):
                continue
            score = impr * (1.0 / avg_pos) if avg_pos else 0
            gsc_candidates.append((score, query.strip().lower(), {
                "source": "gsc_discovered",
                "impressions_90d": impr,
                "avg_position_90d": round(avg_pos, 1),
            }))
        gsc_candidates.sort(reverse=True)
        for _, q, meta in gsc_candidates:
            if q not in seen:
                seen[q] = meta

    return list(seen.items())


def fetch_keyword_overview(client: httpx.Client, headers: dict, keywords: list[str]) -> dict[str, dict]:
    """Batched keyword overview — search volume, KD, CPC, intent, monthly history."""
    if not keywords:
        return {}
    body = [{
        "keywords": keywords,
        "language_code": LANGUAGE_CODE,
        "location_code": LOCATION_CODE,
    }]
    resp = client.post(
        f"{API_BASE}/dataforseo_labs/google/keyword_overview/live",
        headers=headers,
        json=body,
        timeout=60.0,
    )
    resp.raise_for_status()
    payload = resp.json()
    out: dict[str, dict] = {}
    tasks = payload.get("tasks") or []
    for task in tasks:
        for result in task.get("result") or []:
            for item in result.get("items") or []:
                kw = (item.get("keyword") or "").strip().lower()
                if not kw:
                    continue
                kw_info = item.get("keyword_info") or {}
                kw_props = item.get("keyword_properties") or {}
                search_intent = item.get("search_intent_info") or {}
                out[kw] = {
                    "search_volume": kw_info.get("search_volume"),
                    "cpc": kw_info.get("cpc"),
                    "competition": kw_info.get("competition"),
                    "competition_level": kw_info.get("competition_level"),
                    "monthly_searches": kw_info.get("monthly_searches"),
                    "keyword_difficulty": kw_props.get("keyword_difficulty"),
                    "intent": search_intent.get("main_intent"),
                    "secondary_intents": search_intent.get("foreign_intent"),
                }
    return out


def fetch_serp(client: httpx.Client, headers: dict, keyword: str, site_domain: str) -> dict:
    """Top organic results + SERP features for a single keyword."""
    body = [{
        "keyword": keyword,
        "language_code": LANGUAGE_CODE,
        "location_code": LOCATION_CODE,
        "depth": 10,
        "calculate_rectangles": False,
    }]
    resp = client.post(
        f"{API_BASE}/serp/google/organic/live/advanced",
        headers=headers,
        json=body,
        timeout=60.0,
    )
    resp.raise_for_status()
    payload = resp.json()

    top_domains: list[str] = []
    your_position: int | None = None
    features: set[str] = set()

    tasks = payload.get("tasks") or []
    for task in tasks:
        for result in task.get("result") or []:
            for item in result.get("items") or []:
                item_type = item.get("type")
                if item_type == "organic":
                    domain = (item.get("domain") or "").lower()
                    if domain and domain not in top_domains:
                        top_domains.append(domain)
                    if site_domain in domain and your_position is None:
                        your_position = int(item.get("rank_absolute") or 0) or None
                elif item_type:
                    features.add(item_type)
    return {
        "your_position": your_position,
        "top_10_domains": top_domains[:10],
        "features": sorted(features - {"organic"}),
    }


def main() -> int:
    load_dotenv()
    args = parse_args()
    week = args.week or report_week_id()

    login = os.getenv("DFS_LOGIN")
    password = os.getenv("DFS_PASSWORD")
    if not login or not password:
        print("error: DFS_LOGIN and DFS_PASSWORD required", file=sys.stderr)
        return 1

    site_url = os.getenv("SITE_URL", "https://claudecodeformarketers.com")
    site_domain = site_url.replace("https://", "").replace("http://", "").rstrip("/").lower()

    storage = get_storage()
    posts_doc = storage.read_json(f"reports/{week}/posts.json")
    gsc = storage.read_json(f"reports/{week}/search-console.json") if storage.exists(f"reports/{week}/search-console.json") else None

    candidates = collect_candidates(posts_doc, gsc)
    print(f"found {len(candidates)} candidate keywords (declared + GSC-discovered)")
    if len(candidates) > MAX_KEYWORDS:
        print(f"  capping to top {MAX_KEYWORDS}")
        candidates = candidates[:MAX_KEYWORDS]

    if args.dry_run:
        print("DRY RUN — would query the following:")
        for kw, meta in candidates:
            print(f"  [{meta['source']}] {kw}")
        return 0

    # Cache lookup
    fresh_keywords: list[str] = []
    cached: dict[str, dict] = {}
    for kw, _ in candidates:
        entry = cache_get(storage, kw)
        if entry:
            cached[kw] = entry["payload"]
        else:
            fresh_keywords.append(kw)
    print(f"cache: {len(cached)} hit, {len(fresh_keywords)} miss")

    headers = {"Authorization": auth_header(login, password), "Content-Type": "application/json"}
    overview_calls = serp_calls = 0
    cost = 0.0
    serp_errors: list[dict] = []

    new_data: dict[str, dict] = {}
    if fresh_keywords:
        with httpx.Client() as client:
            print(f"keyword_overview: 1 batched call for {len(fresh_keywords)} keyword(s)")
            new_data = fetch_keyword_overview(client, headers, fresh_keywords)
            overview_calls = 1
            cost += COST_KEYWORD_OVERVIEW * len(fresh_keywords)

    # Merge cached + new keyword data
    keyword_data: dict[str, dict] = {**cached, **new_data}

    # SERP enrichment for the top opportunities
    if not args.no_serp:
        # Rank candidates by opportunity (volume from DFS if known, else GSC impressions)
        scored: list[tuple[float, str]] = []
        for kw, meta in candidates:
            data = keyword_data.get(kw, {})
            volume = data.get("search_volume") or 0
            impr_signal = meta.get("impressions_90d", 0) if isinstance(meta, dict) else 0
            score = max(volume, impr_signal)
            scored.append((score, kw))
        scored.sort(reverse=True)
        serp_targets = [kw for _, kw in scored[:TOP_N_FOR_SERP]]

        with httpx.Client() as client:
            for kw in serp_targets:
                # SERP results are also cached (14d) — skip if cached AND already enriched.
                cache_entry = cache_get(storage, kw)
                if cache_entry and cache_entry.get("has_serp"):
                    continue
                print(f"  SERP: {kw}")
                try:
                    serp = fetch_serp(client, headers, kw, site_domain)
                except httpx.HTTPStatusError as e:
                    body = e.response.text[:600]
                    serp_errors.append({"keyword": kw, "status": e.response.status_code, "body": body})
                    print(f"    ! SERP failed ({e.response.status_code})")
                    print(f"      response body: {body}")
                    continue
                except httpx.HTTPError as e:
                    serp_errors.append({"keyword": kw, "error": str(e)})
                    print(f"    ! SERP failed ({e}); continuing without it")
                    continue
                serp_calls += 1
                cost += COST_SERP_ADVANCED
                # Merge into the keyword data and rewrite cache with SERP attached
                merged = {**keyword_data.get(kw, {}), "serp": serp}
                keyword_data[kw] = merged
                cache_put(storage, kw, merged, has_serp=True)
        if serp_errors:
            print(f"  SERP errors: {len(serp_errors)} (see output JSON for details)")

    # Persist all uncached keyword overview results to cache
    for kw in fresh_keywords:
        if kw in keyword_data:
            data = keyword_data[kw]
            cache_put(storage, kw, data, has_serp="serp" in data)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "keyword_count": len(keyword_data),
        "cost_summary": {
            "keyword_overview_calls": overview_calls,
            "serp_calls": serp_calls,
            "cache_hits": len(cached),
            "estimated_cost_usd": round(cost, 4),
        },
        "serp_errors": serp_errors,
        "keywords": {kw: {"source": next((m for k, m in candidates if k == kw), {}).get("source", "unknown"), **data} for kw, data in keyword_data.items()},
    }

    out_key = f"reports/{week}/dataforseo.json"
    storage.write_json(out_key, output)
    print(f"\nwrote {out_key}: {len(keyword_data)} keyword(s) | est cost ${cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
