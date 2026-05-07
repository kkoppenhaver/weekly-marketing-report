"""analyze-content-performance: compute winners, decay, signup attribution
from the current week's fetcher snapshots.

Pure analyzer — reads from the snapshot store, writes one JSON. No API calls.

When search-console data is unavailable, ranking-driven sections (striking
distance, surprise winners, ranking decay) are flagged in `missing_sections`
rather than silently skipped. Pageview-based decay is still computed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from lib.env import load_dotenv  # noqa: E402
from lib.storage import Storage, get_storage  # noqa: E402
from lib.week import current_week_id, report_week_id, previous_week_id  # noqa: E402

# Heuristic floors — avoid celebrating noise on low-traffic posts.
PAGEVIEW_FLOOR_FOR_WOW = 5
DECAY_RATIO_THRESHOLD = 0.70  # current week ≤ 70% of trailing avg flags decay
DECAY_TRAILING_WEEKS = 4
DECAY_AVG_FLOOR = 5
TOP_N_WINNERS = 5
TOP_N_DECAY = 5

# GSC heuristics
STRIKING_MIN_POSITION = 8.0
STRIKING_MAX_POSITION = 20.0
STRIKING_MIN_IMPRESSIONS = 50  # over the 90-day window
TOP_N_STRIKING = 10
RANKING_DECAY_DELTA = 2.0  # positions worse over the comparison window
RANKING_DECAY_RECENT_WEEKS = 4
RANKING_DECAY_PRIOR_WEEKS = 4
RANKING_DECAY_MIN_IMPRESSIONS = 30  # per-week floor
TOP_N_RANKING_DECAY = 5
SURPRISE_MAX_POSITION = 10.0
SURPRISE_MIN_IMPRESSIONS = 30
TOP_N_SURPRISE = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id (default: current week)")
    return parser.parse_args()


def safe_load(storage: Storage, key: str) -> dict | None:
    if not storage.exists(key):
        return None
    return storage.read_json(key)


def url_pathname(url: str) -> str:
    p = urlparse(url).path or "/"
    if p != "/" and p.endswith("/"):
        p = p.rstrip("/")
    return p


def weekly_pv(weekly: list[dict]) -> dict[str, int]:
    """Map week_id → pageviews from a Fathom by_page entry."""
    return {w["week"]: int(w.get("pageviews", 0)) for w in weekly}


def pct_change(current: int, prior: int) -> float | None:
    if prior <= 0:
        return None
    return round((current - prior) / prior, 3)


def compute_headline(fathom: dict | None, kit: dict | None, gsc: dict | None, week: str) -> dict:
    prev = previous_week_id(week)
    pv_current = pv_prior = 0

    if fathom:
        for path, payload in fathom.get("by_page", {}).items():
            week_map = weekly_pv(payload.get("weekly", []))
            pv_current += week_map.get(week, 0)
            pv_prior += week_map.get(prev, 0)

    headline: dict = {
        "pageviews_this_week": pv_current,
        "pageviews_last_week": pv_prior,
        "pageviews_wow_pct": pct_change(pv_current, pv_prior),
    }

    if gsc:
        clicks_current = clicks_prior = 0
        impr_current = impr_prior = 0
        for url, payload in gsc.get("by_page", {}).items():
            for w in payload.get("weekly", []):
                if w["week"] == week:
                    clicks_current += w.get("clicks", 0)
                    impr_current += w.get("impressions", 0)
                elif w["week"] == prev:
                    clicks_prior += w.get("clicks", 0)
                    impr_prior += w.get("impressions", 0)
        headline["clicks_this_week"] = clicks_current
        headline["clicks_last_week"] = clicks_prior
        headline["clicks_wow_pct"] = pct_change(clicks_current, clicks_prior)
        headline["impressions_this_week"] = impr_current
        headline["impressions_last_week"] = impr_prior
    else:
        headline["clicks_this_week"] = None
        headline["clicks_last_week"] = None
        headline["clicks_wow_pct"] = None

    if kit:
        sub = kit.get("subscribers", {})
        headline["signups_this_week"] = sub.get("this_week_new", 0)
        headline["signups_last_week"] = sub.get("last_week_new", 0)
        headline["signups_wow_pct"] = pct_change(
            sub.get("this_week_new", 0), sub.get("last_week_new", 0)
        )
    else:
        headline["signups_this_week"] = None
        headline["signups_last_week"] = None
        headline["signups_wow_pct"] = None

    return headline


def aggregate_query_window(weekly: list[dict]) -> dict:
    """Sum a 90d weekly history into one window-totals row."""
    impr = sum(w.get("impressions", 0) for w in weekly)
    clicks = sum(w.get("clicks", 0) for w in weekly)
    pos_weighted = sum(w.get("position", 0) * w.get("impressions", 0) for w in weekly)
    return {
        "impressions": impr,
        "clicks": clicks,
        "avg_position": round(pos_weighted / impr, 2) if impr else 0.0,
        "ctr": round(clicks / impr, 4) if impr else 0.0,
    }


def compute_striking_distance(gsc: dict, posts_by_path: dict[str, dict]) -> list[dict]:
    """Queries ranking position 8–20 with meaningful impressions, sorted by opportunity."""
    candidates: list[dict] = []
    for url, payload in gsc.get("by_page", {}).items():
        path = url_pathname(url)
        post = posts_by_path.get(path)
        for q in payload.get("top_queries", []):
            position = float(q.get("position", 0))
            impressions = int(q.get("impressions", 0))
            if not (STRIKING_MIN_POSITION <= position <= STRIKING_MAX_POSITION):
                continue
            if impressions < STRIKING_MIN_IMPRESSIONS:
                continue
            # Opportunity score: more impressions and closer-to-page-1 wins.
            opportunity = impressions * (1.0 / position)
            candidates.append(
                {
                    "query": q["query"],
                    "page": path,
                    "slug": post["slug"] if post else None,
                    "title": post["title"] if post else None,
                    "current_position": round(position, 1),
                    "impressions": impressions,
                    "current_clicks": int(q.get("clicks", 0)),
                    "current_ctr": q.get("ctr", 0),
                    "opportunity_score": round(opportunity, 2),
                }
            )
    candidates.sort(key=lambda c: c["opportunity_score"], reverse=True)
    return candidates[:TOP_N_STRIKING]


def compute_ranking_decay(gsc: dict, posts_by_path: dict[str, dict]) -> list[dict]:
    """Pages whose impressions-weighted avg position has worsened materially."""
    candidates: list[dict] = []
    needed = RANKING_DECAY_RECENT_WEEKS + RANKING_DECAY_PRIOR_WEEKS
    for url, payload in gsc.get("by_page", {}).items():
        weekly = payload.get("weekly", [])
        if len(weekly) < needed:
            continue
        # Use the most recent N weeks vs. the N before that.
        recent = weekly[-RANKING_DECAY_RECENT_WEEKS:]
        prior = weekly[-needed:-RANKING_DECAY_RECENT_WEEKS]
        recent_pos = _avg_position(recent)
        prior_pos = _avg_position(prior)
        if recent_pos is None or prior_pos is None:
            continue
        delta = recent_pos - prior_pos  # higher = worse
        if delta < RANKING_DECAY_DELTA:
            continue
        # Avoid flagging pages with negligible recent volume.
        recent_impr = sum(w.get("impressions", 0) for w in recent) / max(len(recent), 1)
        if recent_impr < RANKING_DECAY_MIN_IMPRESSIONS:
            continue
        path = url_pathname(url)
        post = posts_by_path.get(path)
        candidates.append(
            {
                "page": path,
                "slug": post["slug"] if post else None,
                "title": post["title"] if post else None,
                "recent_avg_position": round(recent_pos, 1),
                "prior_avg_position": round(prior_pos, 1),
                "position_delta": round(delta, 1),
                "recent_avg_impressions_per_week": round(recent_impr, 1),
            }
        )
    candidates.sort(key=lambda c: c["position_delta"], reverse=True)
    return candidates[:TOP_N_RANKING_DECAY]


def _avg_position(weekly: list[dict]) -> float | None:
    """Impressions-weighted average position across a list of weekly entries."""
    impr = sum(w.get("impressions", 0) for w in weekly)
    if impr == 0:
        return None
    weighted = sum(w.get("position", 0) * w.get("impressions", 0) for w in weekly)
    return weighted / impr


def compute_surprise_winners(gsc: dict, posts_by_path: dict[str, dict]) -> list[dict]:
    """Posts ranking well for queries they didn't declare in frontmatter."""
    candidates: list[dict] = []
    for url, payload in gsc.get("by_page", {}).items():
        path = url_pathname(url)
        post = posts_by_path.get(path)
        if not post:
            continue
        declared = {
            (post.get("target_keyword") or "").strip().lower(),
            *(s.strip().lower() for s in (post.get("secondary_keywords") or [])),
        }
        declared.discard("")
        for q in payload.get("top_queries", []):
            position = float(q.get("position", 0))
            impressions = int(q.get("impressions", 0))
            if position == 0 or position > SURPRISE_MAX_POSITION:
                continue
            if impressions < SURPRISE_MIN_IMPRESSIONS:
                continue
            qtext = q["query"].strip().lower()
            if qtext in declared:
                continue
            # Treat a query as "covered" if any declared keyword is a substring (loose match).
            if any(d and d in qtext for d in declared):
                continue
            candidates.append(
                {
                    "query": q["query"],
                    "page": path,
                    "slug": post["slug"],
                    "title": post.get("title"),
                    "declared_target": post.get("target_keyword"),
                    "current_position": round(position, 1),
                    "impressions": impressions,
                    "clicks": int(q.get("clicks", 0)),
                }
            )
    candidates.sort(key=lambda c: (c["impressions"], -c["current_position"]), reverse=True)
    return candidates[:TOP_N_SURPRISE]


def compute_winners(fathom: dict, posts_by_path: dict[str, dict], week: str) -> list[dict]:
    """Top posts ranked by absolute pageview increase WoW."""
    prev = previous_week_id(week)
    candidates: list[dict] = []
    for path, payload in fathom.get("by_page", {}).items():
        week_map = weekly_pv(payload.get("weekly", []))
        cur = week_map.get(week, 0)
        prior = week_map.get(prev, 0)
        if cur < PAGEVIEW_FLOOR_FOR_WOW:
            continue
        delta = cur - prior
        if delta <= 0:
            continue
        post = posts_by_path.get(path)
        candidates.append(
            {
                "path": path,
                "slug": post["slug"] if post else None,
                "title": post["title"] if post else None,
                "pageviews_this_week": cur,
                "pageviews_last_week": prior,
                "absolute_delta": delta,
                "wow_pct": pct_change(cur, prior),
            }
        )
    candidates.sort(key=lambda c: c["absolute_delta"], reverse=True)
    return candidates[:TOP_N_WINNERS]


def compute_decay(fathom: dict, posts_by_path: dict[str, dict], week: str) -> list[dict]:
    """Posts where current week is materially below their recent trailing average."""
    prev = previous_week_id(week)
    candidates: list[dict] = []
    for path, payload in fathom.get("by_page", {}).items():
        weekly = payload.get("weekly", [])
        if len(weekly) < DECAY_TRAILING_WEEKS + 1:
            continue
        week_map = weekly_pv(weekly)
        cur = week_map.get(week, 0)
        # Trailing window: 4 weeks ending the week BEFORE current
        trailing_weeks = []
        w = prev
        for _ in range(DECAY_TRAILING_WEEKS):
            trailing_weeks.append(week_map.get(w, 0))
            w = previous_week_id(w)
        trailing_avg = sum(trailing_weeks) / DECAY_TRAILING_WEEKS
        if trailing_avg < DECAY_AVG_FLOOR:
            continue
        if cur >= trailing_avg * DECAY_RATIO_THRESHOLD:
            continue
        post = posts_by_path.get(path)
        candidates.append(
            {
                "path": path,
                "slug": post["slug"] if post else None,
                "title": post["title"] if post else None,
                "pageviews_this_week": cur,
                "trailing_4w_avg": round(trailing_avg, 1),
                "ratio_vs_avg": round(cur / trailing_avg, 3) if trailing_avg else None,
            }
        )
    candidates.sort(key=lambda c: c["ratio_vs_avg"] or 0)
    return candidates[:TOP_N_DECAY]


def compute_signup_attribution(kit: dict | None, posts_by_slug: dict[str, dict]) -> list[dict]:
    if not kit:
        return []
    rows: list[dict] = []
    for entry in kit.get("by_post", []):
        slug = entry.get("slug")
        post = posts_by_slug.get(slug)
        rows.append(
            {
                "slug": slug,
                "title": post["title"] if post else None,
                "kit_tag_id": entry.get("kit_tag_id"),
                "total_subscribers": entry.get("total"),
                "this_week_new": entry.get("this_week_new"),
            }
        )
    rows.sort(key=lambda r: r["this_week_new"] or 0, reverse=True)
    return rows


def main() -> int:
    load_dotenv()
    args = parse_args()
    week = args.week or report_week_id()
    storage = get_storage()

    posts_doc = safe_load(storage, f"reports/{week}/posts.json")
    if not posts_doc:
        print(f"error: missing posts.json for {week}", file=sys.stderr)
        return 1

    fathom = safe_load(storage, f"reports/{week}/fathom.json")
    kit = safe_load(storage, f"reports/{week}/kit.json")
    gsc = safe_load(storage, f"reports/{week}/search-console.json")

    posts_by_path = {url_pathname(p["url"]): p for p in posts_doc["posts"]}
    posts_by_slug = {p["slug"]: p for p in posts_doc["posts"]}

    available = [name for name, doc in (("fathom", fathom), ("kit", kit), ("search-console", gsc)) if doc]
    missing = [name for name, doc in (("fathom", fathom), ("kit", kit), ("search-console", gsc)) if not doc]

    headline = compute_headline(fathom, kit, gsc, week)
    winners = compute_winners(fathom, posts_by_path, week) if fathom else []
    decay = compute_decay(fathom, posts_by_path, week) if fathom else []
    attribution = compute_signup_attribution(kit, posts_by_slug)
    striking = compute_striking_distance(gsc, posts_by_path) if gsc else []
    ranking_decay = compute_ranking_decay(gsc, posts_by_path) if gsc else []
    surprises = compute_surprise_winners(gsc, posts_by_path) if gsc else []

    missing_sections: list[str] = []
    if not gsc:
        missing_sections.extend(["striking_distance_keywords", "surprise_winners", "ranking_decay"])

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "data_sources_available": available,
        "data_sources_missing": missing,
        "missing_sections": missing_sections,
        "headline": headline,
        "winners_this_week": winners,
        "decaying_posts": decay,
        "signup_attribution": attribution,
        "striking_distance_keywords": striking,
        "ranking_decay": ranking_decay,
        "surprise_winners": surprises,
    }

    out_key = f"reports/{week}/insights.content-perf.json"
    storage.write_json(out_key, output)

    print(f"wrote {out_key}")
    print(f"  data sources: {available} | missing: {missing}")
    print(
        f"  headline: pv {headline['pageviews_this_week']} (was {headline['pageviews_last_week']}), "
        f"signups {headline['signups_this_week']} (was {headline['signups_last_week']})"
    )
    print(f"  winners: {len(winners)}, decaying: {len(decay)}, attributed posts: {len(attribution)}")
    if gsc:
        print(
            f"  GSC: striking-distance={len(striking)}, ranking-decay={len(ranking_decay)}, "
            f"surprises={len(surprises)} | clicks {headline['clicks_this_week']} "
            f"(was {headline['clicks_last_week']})"
        )
    if missing_sections:
        print(f"  skipped sections (need search-console): {missing_sections}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
