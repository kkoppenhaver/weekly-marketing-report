"""analyze-keyword-opportunities: surface new-post candidates and refresh
candidates from current GSC data + the existing post manifest.

Pure analyzer. Without DataForSEO it can't estimate volume/KD; the rationale
substitutes GSC impressions and ranking position as the empirical signal.
DataForSEO would add: monthly search volume, keyword difficulty, SERP
features, intent classification, related keywords. Adding that later
sharpens this analyzer's output but doesn't change its shape.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from lib.env import load_dotenv  # noqa: E402
from lib.storage import Storage, get_storage  # noqa: E402
from lib.week import current_week_id, report_week_id  # noqa: E402

# New post heuristics. Floors are calibrated for a small site (~100 subscribers,
# ~30 posts). Once the site has more search volume, raise NEW_POST_MIN_IMPRESSIONS
# to 100+ to filter the noise.
NEW_POST_MIN_IMPRESSIONS = 25   # over the 90-day window
NEW_POST_MIN_POSITION = 11.0    # exclude page-1 keywords (already targeted)
NEW_POST_MAX_POSITION = 40.0    # exclude irrelevant impressions
TOP_N_NEW_POSTS = 3

# Refresh heuristics — built primarily from existing content-perf signals
TOP_N_REFRESH = 3

# DFS-driven heuristics for declared-but-not-ranking detection
DECLARED_NOT_RANKING_MIN_VOLUME = 200  # only flag if the keyword has meaningful volume
DECLARED_NOT_RANKING_MIN_POSITION = 20.0  # treat positions worse than this (or no data) as "not ranking"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id (default: current week)")
    return parser.parse_args()


def safe_load(storage: Storage, key: str) -> dict | None:
    if not storage.exists(key):
        return None
    return storage.read_json(key)


def aggregate_query_window(weekly: list[dict]) -> tuple[int, int, float]:
    """Return (impressions, clicks, impressions-weighted avg position) over the window."""
    impr = sum(w.get("impressions", 0) for w in weekly)
    clicks = sum(w.get("clicks", 0) for w in weekly)
    if impr == 0:
        return 0, 0, 0.0
    weighted = sum(w.get("position", 0) * w.get("impressions", 0) for w in weekly)
    return impr, clicks, weighted / impr


def declared_keywords(posts: list[dict]) -> set[str]:
    declared: set[str] = set()
    for p in posts:
        if t := (p.get("target_keyword") or "").strip().lower():
            declared.add(t)
        for s in p.get("secondary_keywords") or []:
            if s and s.strip():
                declared.add(s.strip().lower())
    return declared


def query_overlaps_declared(query: str, declared: set[str]) -> bool:
    """Loose match: query is covered if a declared keyword is a substring (either direction)."""
    q = query.strip().lower()
    if q in declared:
        return True
    for d in declared:
        if d and (d in q or q in d):
            return True
    return False


def find_anchor_post(query: str, gsc: dict, posts_by_url: dict[str, dict]) -> dict | None:
    """Best existing post for internal linking — the one whose top_queries list contains this query."""
    q = query.strip().lower()
    best: dict | None = None
    best_position: float = 999.0
    for url, payload in (gsc.get("by_page") or {}).items():
        for tq in payload.get("top_queries") or []:
            if tq.get("query", "").strip().lower() != q:
                continue
            position = float(tq.get("position", 999))
            if position < best_position:
                best_position = position
                post = posts_by_url.get(url)
                if post:
                    best = {
                        "slug": post["slug"],
                        "title": post["title"],
                        "url": url,
                        "current_position_for_query": round(position, 1),
                    }
    return best


def dfs_lookup(dfs: dict | None, keyword: str) -> dict:
    """Return the DataForSEO record for `keyword` (lowercased), or {} if unavailable."""
    if not dfs:
        return {}
    return (dfs.get("keywords") or {}).get(keyword.strip().lower(), {}) or {}


def kd_band(kd: int | float | None) -> str | None:
    if kd is None:
        return None
    if kd < 20:
        return "easy"
    if kd < 40:
        return "medium"
    if kd < 60:
        return "hard"
    return "very hard"


def compute_new_post_suggestions(gsc: dict, posts_doc: dict, dfs: dict | None) -> list[dict]:
    posts = posts_doc.get("posts") or []
    declared = declared_keywords(posts)
    posts_by_url = {p["url"]: p for p in posts}

    candidates: list[dict] = []
    for query, payload in (gsc.get("by_query") or {}).items():
        impressions, clicks, avg_pos = aggregate_query_window(payload.get("weekly") or [])
        if impressions < NEW_POST_MIN_IMPRESSIONS:
            continue
        if not (NEW_POST_MIN_POSITION <= avg_pos <= NEW_POST_MAX_POSITION):
            continue
        if query_overlaps_declared(query, declared):
            continue

        dfs_data = dfs_lookup(dfs, query)
        volume = dfs_data.get("search_volume")
        kd = dfs_data.get("keyword_difficulty")
        intent = dfs_data.get("intent")

        # Score: prefer DFS volume when available; fall back to GSC impressions.
        # Volume is monthly (vs GSC over 90d) so multiply by 3 to compare.
        volume_signal = (volume * 3) if volume else impressions
        opportunity = volume_signal * (1.0 / avg_pos)
        anchor = find_anchor_post(query, gsc, posts_by_url)

        rationale_parts: list[str] = []
        if volume is not None:
            band = kd_band(kd)
            kd_str = f", KD {kd} ({band})" if band else (f", KD {kd}" if kd is not None else "")
            rationale_parts.append(
                f"Monthly volume ~{volume}{kd_str}. "
                f"GSC shows {impressions} impressions over 90d at avg position {avg_pos:.1f}, but no post targets this directly."
            )
        else:
            rationale_parts.append(
                f"{impressions} impressions over 90 days at avg position {avg_pos:.1f} (DFS reports no volume — niche or recent query). No post targets this directly."
            )
        if anchor:
            rationale_parts.append(
                f"Currently ranks via *{anchor['title']}* at position "
                f"{anchor['current_position_for_query']} — a dedicated post would likely outrank it."
            )

        candidates.append(
            {
                "primary_keyword": query,
                "window_impressions": impressions,
                "window_clicks": clicks,
                "current_position": round(avg_pos, 1),
                "estimated_monthly_impressions": int(impressions / 3),
                "search_volume": volume,
                "keyword_difficulty": kd,
                "kd_band": kd_band(kd),
                "intent": intent,
                "opportunity_score": round(opportunity, 2),
                "best_existing_anchor": anchor,
                "rationale": " ".join(rationale_parts),
                "suggested_internal_links_from": [anchor["slug"]] if anchor else [],
                "data_source": "gsc+dfs" if dfs_data else "gsc_only",
            }
        )

    candidates.sort(key=lambda c: c["opportunity_score"], reverse=True)
    return candidates[:TOP_N_NEW_POSTS]


def compute_declared_target_gaps(posts_doc: dict, gsc: dict | None, dfs: dict | None) -> list[dict]:
    """Posts whose declared target_keyword has DFS volume but the post is NOT ranking.

    This is the highest-leverage refresh signal: you've explicitly targeted a
    query with real volume, and the post is missing the audience.
    """
    if not dfs:
        return []
    posts = posts_doc.get("posts") or []
    candidates: list[dict] = []

    for post in posts:
        if post.get("draft"):
            continue
        target = (post.get("target_keyword") or "").strip().lower()
        if not target:
            continue
        dfs_data = dfs_lookup(dfs, target)
        volume = dfs_data.get("search_volume")
        if not volume or volume < DECLARED_NOT_RANKING_MIN_VOLUME:
            continue

        # Look up GSC position for this target on this post's URL
        gsc_position: float | None = None
        gsc_impressions = 0
        post_path = url_pathname(post["url"])
        for url, payload in (gsc.get("by_page") or {}).items() if gsc else []:
            if url_pathname(url) != post_path:
                continue
            for tq in payload.get("top_queries") or []:
                if tq.get("query", "").strip().lower() == target:
                    gsc_position = float(tq.get("position", 0))
                    gsc_impressions = int(tq.get("impressions", 0))
                    break

        # If the post is ranking well already, don't flag it.
        if gsc_position is not None and gsc_position <= 10:
            continue

        kd = dfs_data.get("keyword_difficulty")
        # Score: bigger volume + worse current position (or no position) = bigger gap.
        position_factor = 1.0 if gsc_position is None else min(1.0, gsc_position / 30.0)
        score = volume * position_factor

        if gsc_position is None:
            position_str = "no GSC impressions in the top queries"
        else:
            position_str = f"avg position {gsc_position:.1f} ({gsc_impressions} 90d impressions)"

        candidates.append({
            "page": post_path,
            "slug": post["slug"],
            "title": post["title"],
            "target_query": target,
            "search_volume": volume,
            "keyword_difficulty": kd,
            "kd_band": kd_band(kd),
            "intent": dfs_data.get("intent"),
            "current_position": gsc_position,
            "rationale": (
                f"Declared target `{target}` has ~{volume}/mo volume"
                f"{f', KD {kd} ({kd_band(kd)})' if kd_band(kd) else ''}, "
                f"but the post is currently {position_str}. "
                "This is the highest-leverage refresh available."
            ),
            "score": round(score, 1),
            "source": "declared_target_gap",
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def compute_refresh_suggestions(
    content_perf: dict | None,
    posts_by_path: dict[str, dict],
    declared_gaps: list[dict],
    dfs: dict | None,
) -> list[dict]:
    """Merge three refresh signals — declared-target gaps, striking distance, ranking decay —
    into a ranked, deduplicated list of per-page refresh candidates."""
    seen_pages: set[str] = set()
    candidates: list[dict] = []

    # 1. Declared-target gaps — usually the highest-leverage refresh available.
    for gap in declared_gaps:
        page = gap["page"]
        if page in seen_pages:
            continue
        seen_pages.add(page)
        candidates.append({
            "page": page,
            "slug": gap["slug"],
            "title": gap["title"],
            "target_query": gap["target_query"],
            "search_volume": gap.get("search_volume"),
            "keyword_difficulty": gap.get("keyword_difficulty"),
            "kd_band": gap.get("kd_band"),
            "current_position": gap.get("current_position"),
            "primary_signal": "declared_target_gap",
            "rationale": gap["rationale"],
            "recommended_changes": [
                "Audit the post against top-ranking competitors for this query",
                "Strengthen the H1 and intro paragraph to match the exact query intent",
                "Add internal links from 2-3 high-authority posts on adjacent topics",
            ],
        })

    if content_perf:
        decay_pages = {d.get("page"): d for d in content_perf.get("ranking_decay") or []}

        # 2. Striking-distance refreshes — pages with a query already on page 2 borderline.
        for s in content_perf.get("striking_distance_keywords") or []:
            page = s.get("page")
            if not page or page in seen_pages:
                continue
            seen_pages.add(page)
            post = posts_by_path.get(page)
            is_decaying = page in decay_pages

            dfs_data = dfs_lookup(dfs, s["query"])
            volume = dfs_data.get("search_volume")
            kd = dfs_data.get("keyword_difficulty")

            rationale = (
                f"Striking-distance keyword \"{s['query']}\" at position {s['current_position']} with "
                f"{s['impressions']} impressions but only {s['current_clicks']} clicks. "
            )
            if volume:
                kd_str = f", KD {kd} ({kd_band(kd)})" if kd_band(kd) else ""
                rationale += f"DFS reports ~{volume}/mo volume{kd_str}. "
            if is_decaying:
                d = decay_pages[page]
                rationale += (
                    f"Page is also showing ranking decay (avg position {d['prior_avg_position']} → "
                    f"{d['recent_avg_position']}). Refresh is overdue."
                )
            else:
                rationale += "A focused refresh likely pushes it to top 5."

            candidates.append({
                "page": page,
                "slug": post["slug"] if post else None,
                "title": post["title"] if post else None,
                "target_query": s["query"],
                "search_volume": volume,
                "keyword_difficulty": kd,
                "kd_band": kd_band(kd),
                "current_position": s["current_position"],
                "current_impressions": s["impressions"],
                "current_clicks": s["current_clicks"],
                "is_decaying": is_decaying,
                "primary_signal": "striking_distance",
                "rationale": rationale,
                "recommended_changes": [
                    "Refresh examples to match the latest Claude Code version",
                    "Add internal links from 2-3 high-authority posts",
                    "Retitle the post H1 to align with the search query if the wording is off",
                ],
            })

        # 3. Pure ranking-decay refreshes (no striking-distance, no declared-gap match).
        for page, d in decay_pages.items():
            if page in seen_pages:
                continue
            seen_pages.add(page)
            post = posts_by_path.get(page)
            candidates.append({
                "page": page,
                "slug": post["slug"] if post else None,
                "title": post["title"] if post else None,
                "target_query": None,
                "current_position": d["recent_avg_position"],
                "primary_signal": "ranking_decay",
                "rationale": (
                    f"Ranking decay: avg position drifted {d['prior_avg_position']} → "
                    f"{d['recent_avg_position']} (Δ +{d['position_delta']}) on "
                    f"~{d['recent_avg_impressions_per_week']} impressions/week."
                ),
                "recommended_changes": [
                    "Check for stale claims, broken examples",
                    "Look at competitors who may have published fresher coverage",
                    "Add internal links from related posts",
                ],
            })

    return candidates[:TOP_N_REFRESH]


def url_pathname(url: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url).path or "/"
    if p != "/" and p.endswith("/"):
        p = p.rstrip("/")
    return p


def main() -> int:
    load_dotenv()
    args = parse_args()
    week = args.week or report_week_id()
    storage = get_storage()

    posts_doc = safe_load(storage, f"reports/{week}/posts.json")
    gsc = safe_load(storage, f"reports/{week}/search-console.json")
    content_perf = safe_load(storage, f"reports/{week}/insights.content-perf.json")
    dfs = safe_load(storage, f"reports/{week}/dataforseo.json")

    if not posts_doc:
        print(f"error: missing posts.json for {week}", file=sys.stderr)
        return 1

    sources = ["posts"]
    sources_missing: list[str] = []
    if gsc:
        sources.append("search-console")
    else:
        sources_missing.append("search-console")
    if dfs:
        sources.append("dataforseo")
    else:
        sources_missing.append("dataforseo")

    posts_by_path = {url_pathname(p["url"]): p for p in posts_doc.get("posts") or []}

    declared_gaps = compute_declared_target_gaps(posts_doc, gsc, dfs)
    new_posts = compute_new_post_suggestions(gsc, posts_doc, dfs) if gsc else []
    refreshes = compute_refresh_suggestions(content_perf, posts_by_path, declared_gaps, dfs)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "data_sources_available": sources,
        "data_sources_missing": sources_missing,
        "new_post_suggestions": new_posts,
        "post_refresh_suggestions": refreshes,
        "declared_target_gaps": declared_gaps,  # full list (refresh suggestions cap at TOP_N)
    }

    out_key = f"reports/{week}/insights.opportunities.json"
    storage.write_json(out_key, output)

    print(f"wrote {out_key}")
    print(f"  data sources: {sources} | missing: {sources_missing}")
    print(f"  new post suggestions: {len(new_posts)}")
    print(f"  refresh suggestions: {len(refreshes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
