"""analyze-seo-health: surface technical (CWV), on-page, and search-quality
issues as a prioritized fix list with severity, effort, and impact.

Pure analyzer — reads from the snapshot store, writes one JSON. No API calls.

Sources currently wired:
  - pagespeed.json   → Core Web Vitals issues (mobile-first)
  - posts.json       → on-page frontmatter issues (title length, description
                       length, target_keyword presence)
  - search-console.json → low-CTR pages, content gaps

Deferred to v2:
  - Indexation status (needs URL Inspection API; would need OAuth scope expand)
  - Orphan posts (needs markdown source link parsing)
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
from lib.week import current_week_id, report_week_id  # noqa: E402

# Mobile is what Google ranks on, so all CWV thresholds use the mobile strategy.
# Thresholds match Google's "Good / Needs Improvement / Poor" bands.
LCP_GOOD_MS = 2500
LCP_POOR_MS = 4000
CLS_GOOD = 0.1
CLS_POOR = 0.25
TBT_GOOD_MS = 200
TBT_POOR_MS = 600
PERF_GOOD = 0.9
PERF_POOR = 0.5

# CC4M's published rule (site repo CLAUDE.md): meta descriptions 150–160 chars.
DESC_MIN = 150
DESC_MAX = 160
TITLE_MIN = 30
TITLE_MAX = 65

# Search-quality heuristics
LOW_CTR_MIN_IMPRESSIONS = 200  # over the 90d window
LOW_CTR_THRESHOLD = 0.01  # <1% CTR despite meaningful impressions
LOW_CTR_MAX_POSITION = 15.0  # don't flag CTR for things ranking on page 3+


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id (default: current week)")
    parser.add_argument("--max-issues", type=int, default=25, help="Cap issues in summary output (default 25)")
    return parser.parse_args()


def safe_load(storage: Storage, key: str) -> dict | None:
    if not storage.exists(key):
        return None
    return storage.read_json(key)


def url_to_pathname(url: str) -> str:
    p = urlparse(url).path or "/"
    if p != "/" and p.endswith("/"):
        p = p.rstrip("/")
    return p


def issue(
    *,
    severity: str,
    category: str,
    page: str,
    summary: str,
    evidence: dict,
    action: str,
    effort: str,
    impact: str,
) -> dict:
    return {
        "severity": severity,
        "category": category,
        "page": page,
        "issue": summary,
        "evidence": evidence,
        "recommended_action": action,
        "effort": effort,
        "impact": impact,
    }


def cwv_issues(pagespeed: dict | None) -> list[dict]:
    if not pagespeed:
        return []
    out: list[dict] = []
    for url, data in (pagespeed.get("by_url") or {}).items():
        mobile = data.get("mobile") or {}
        if not mobile:
            continue
        path = url_to_pathname(url)
        perf = mobile.get("performance")
        lab = mobile.get("lab") or {}
        lcp_ms = lab.get("lcp_ms")
        cls = lab.get("cls")
        tbt_ms = lab.get("tbt_ms")
        failing = mobile.get("issues") or []

        # Performance score
        if perf is not None:
            if perf < PERF_POOR:
                out.append(issue(
                    severity="critical",
                    category="cwv",
                    page=path,
                    summary=f"Mobile performance score {perf:.2f} (poor)",
                    evidence={"performance": perf, "failing_audits": failing},
                    action="Investigate failing audits — likely image/JS optimization opportunities.",
                    effort="M", impact="L",
                ))
            elif perf < PERF_GOOD:
                out.append(issue(
                    severity="warning",
                    category="cwv",
                    page=path,
                    summary=f"Mobile performance score {perf:.2f} (needs improvement)",
                    evidence={"performance": perf, "failing_audits": failing},
                    action="Review failing audits; usually image sizing or render-blocking JS.",
                    effort="M", impact="M",
                ))

        # LCP (lab — field metrics aren't populated yet on this site)
        if lcp_ms is not None:
            if lcp_ms > LCP_POOR_MS:
                out.append(issue(
                    severity="critical",
                    category="cwv",
                    page=path,
                    summary=f"Mobile LCP {lcp_ms/1000:.1f}s (poor — should be <2.5s)",
                    evidence={"lcp_ms": lcp_ms},
                    action="Largest contentful element is loading slowly. Check hero image size, font loading, server TTFB.",
                    effort="M", impact="L",
                ))
            elif lcp_ms > LCP_GOOD_MS:
                out.append(issue(
                    severity="warning",
                    category="cwv",
                    page=path,
                    summary=f"Mobile LCP {lcp_ms/1000:.1f}s (needs improvement)",
                    evidence={"lcp_ms": lcp_ms},
                    action="Optimize hero image (WebP, smaller dimensions); consider preloading the LCP element.",
                    effort="S", impact="M",
                ))

        # CLS
        if cls is not None and cls > CLS_POOR:
            out.append(issue(
                severity="critical",
                category="cwv",
                page=path,
                summary=f"Mobile CLS {cls:.3f} (poor — should be <0.1)",
                evidence={"cls": cls},
                action="Layout is shifting after load. Set explicit width/height on images, reserve space for embeds.",
                effort="M", impact="M",
            ))
        elif cls is not None and cls > CLS_GOOD:
            out.append(issue(
                severity="warning",
                category="cwv",
                page=path,
                summary=f"Mobile CLS {cls:.3f} (needs improvement)",
                evidence={"cls": cls},
                action="Minor layout shift on load — usually a missing image dimension.",
                effort="S", impact="S",
            ))

        # TBT (proxy for INP since INP requires CrUX field data)
        if tbt_ms is not None and tbt_ms > TBT_POOR_MS:
            out.append(issue(
                severity="warning",
                category="cwv",
                page=path,
                summary=f"Mobile Total Blocking Time {tbt_ms}ms (interactive feel will lag)",
                evidence={"tbt_ms": tbt_ms},
                action="Long JS tasks blocking the main thread. Defer non-critical scripts.",
                effort="M", impact="M",
            ))

    return out


def on_page_issues(posts_doc: dict | None) -> list[dict]:
    if not posts_doc:
        return []
    out: list[dict] = []
    for post in posts_doc.get("posts") or []:
        if post.get("draft"):
            continue
        path = urlparse(post["url"]).path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        title = (post.get("title") or "").strip()
        desc = (post.get("description") or "").strip()
        target = post.get("target_keyword")

        # target_keyword missing
        if not target:
            out.append(issue(
                severity="warning",
                category="on_page",
                page=path,
                summary="Missing `target_keyword` in frontmatter",
                evidence={"slug": post["slug"]},
                action="Add a `target_keyword` to declare what this post is optimized to rank for.",
                effort="S", impact="M",
            ))

        # Title length
        if title:
            if len(title) > TITLE_MAX:
                out.append(issue(
                    severity="warning",
                    category="on_page",
                    page=path,
                    summary=f"Title {len(title)} chars (truncates in SERP — keep ≤{TITLE_MAX})",
                    evidence={"title": title, "length": len(title)},
                    action="Tighten the title — Google truncates around 65 chars on most devices.",
                    effort="S", impact="S",
                ))
            elif len(title) < TITLE_MIN:
                out.append(issue(
                    severity="info",
                    category="on_page",
                    page=path,
                    summary=f"Title only {len(title)} chars (could be more descriptive)",
                    evidence={"title": title, "length": len(title)},
                    action="Short titles often miss long-tail variants. Aim for 40–60 chars.",
                    effort="S", impact="S",
                ))

        # Description length (CC4M rule: 150-160)
        if desc:
            if len(desc) < DESC_MIN:
                out.append(issue(
                    severity="info",
                    category="on_page",
                    page=path,
                    summary=f"Description {len(desc)} chars (CC4M rule: {DESC_MIN}–{DESC_MAX})",
                    evidence={"description": desc, "length": len(desc)},
                    action="Pad to 150–160 chars — short descriptions look thin in search results.",
                    effort="S", impact="S",
                ))
            elif len(desc) > DESC_MAX + 5:  # small grace beyond the upper bound
                out.append(issue(
                    severity="info",
                    category="on_page",
                    page=path,
                    summary=f"Description {len(desc)} chars (CC4M rule: {DESC_MIN}–{DESC_MAX})",
                    evidence={"description": desc, "length": len(desc)},
                    action="Tighten — Google truncates at ~160. Last few words are wasted pixels.",
                    effort="S", impact="S",
                ))
        else:
            out.append(issue(
                severity="warning",
                category="on_page",
                page=path,
                summary="Missing meta description",
                evidence={},
                action="Write a 150-160 char description summarizing the post's value.",
                effort="S", impact="M",
            ))

    return out


def search_quality_issues(gsc: dict | None) -> list[dict]:
    """Pages with high impressions but anemic CTR — usually a weak title/description."""
    if not gsc:
        return []
    out: list[dict] = []
    for url, payload in (gsc.get("by_page") or {}).items():
        weekly = payload.get("weekly") or []
        if not weekly:
            continue
        impressions = sum(w.get("impressions", 0) for w in weekly)
        clicks = sum(w.get("clicks", 0) for w in weekly)
        if impressions < LOW_CTR_MIN_IMPRESSIONS:
            continue
        ctr = clicks / impressions
        if ctr >= LOW_CTR_THRESHOLD:
            continue
        # Avg position — only flag if the page is actually findable.
        pos_weighted = sum(w.get("position", 0) * w.get("impressions", 0) for w in weekly)
        avg_pos = pos_weighted / impressions if impressions else 0
        if avg_pos > LOW_CTR_MAX_POSITION:
            continue
        path = url_to_pathname(url)
        out.append(issue(
            severity="warning",
            category="search_quality",
            page=path,
            summary=f"Low CTR {ctr:.2%} on {impressions} impressions (avg pos {avg_pos:.1f})",
            evidence={
                "impressions": impressions,
                "clicks": clicks,
                "ctr": round(ctr, 4),
                "avg_position": round(avg_pos, 1),
            },
            action="Title and description aren't earning the click. Rewrite to match search intent — concrete promise, specific number, or contrarian angle.",
            effort="S", impact="M",
        ))
    return out


def indexation_issues(gsc: dict | None) -> list[dict]:
    """Surface unindexed pages, stale crawls, and mobile usability failures from URL Inspection."""
    if not gsc:
        return []
    indexation = gsc.get("indexation") or {}
    if not indexation:
        return []
    out: list[dict] = []
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    stale_threshold = _dt.now(_tz.utc) - _td(days=30)

    for url, info in indexation.items():
        path = url_to_pathname(url)
        if "error" in info:
            out.append(issue(
                severity="info",
                category="indexation",
                page=path,
                summary=f"URL Inspection failed: {info['error'][:100]}",
                evidence=info,
                action="Re-run fetch-search-console; transient API errors are common.",
                effort="S", impact="S",
            ))
            continue

        # Not indexed — the highest-impact signal we can produce.
        if not info.get("indexed"):
            coverage = info.get("coverage_state") or "unknown"
            out.append(issue(
                severity="critical",
                category="indexation",
                page=path,
                summary=f"Not indexed: {coverage}",
                evidence={
                    "verdict": info.get("verdict"),
                    "coverage_state": coverage,
                    "last_crawled": info.get("last_crawled"),
                    "page_fetch_state": info.get("page_fetch_state"),
                    "google_canonical": info.get("google_canonical"),
                    "user_canonical": info.get("user_canonical"),
                    "inspection_link": info.get("inspection_link"),
                },
                action=(
                    "Open the inspection_link in GSC and click 'Request Indexing'. "
                    "If coverage says 'Crawled - currently not indexed', Google judged the page low quality — review thin content, internal linking, and freshness."
                ),
                effort="S", impact="L",
            ))
            continue

        # Stale crawl (>30 days)
        last_crawled = info.get("last_crawled")
        if last_crawled:
            try:
                lc_dt = _dt.fromisoformat(last_crawled.replace("Z", "+00:00"))
                if lc_dt < stale_threshold:
                    days = (_dt.now(_tz.utc) - lc_dt).days
                    out.append(issue(
                        severity="warning",
                        category="indexation",
                        page=path,
                        summary=f"Last crawled {days} days ago ({last_crawled[:10]})",
                        evidence={
                            "last_crawled": last_crawled,
                            "inspection_link": info.get("inspection_link"),
                        },
                        action="Submit for re-indexing via GSC. Refresh the post — adding new content or internal links typically nudges Google to re-crawl.",
                        effort="S", impact="M",
                    ))
            except (ValueError, AttributeError):
                pass

        # Mobile usability failures
        if info.get("mobile_verdict") == "FAIL":
            out.append(issue(
                severity="warning",
                category="indexation",
                page=path,
                summary="Mobile usability issues",
                evidence={"mobile_issues": info.get("mobile_issues") or []},
                action="Review mobile_issues in the snapshot — typically tap targets too close, font too small, or content wider than viewport.",
                effort="M", impact="M",
            ))

    return out


SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
IMPACT_RANK = {"L": 0, "M": 1, "S": 2}
EFFORT_RANK = {"S": 0, "M": 1, "L": 2}


def sort_key(i: dict) -> tuple:
    return (
        SEVERITY_RANK.get(i["severity"], 9),
        IMPACT_RANK.get(i["impact"], 9),
        EFFORT_RANK.get(i["effort"], 9),
    )


def main() -> int:
    load_dotenv()
    args = parse_args()
    week = args.week or report_week_id()
    storage = get_storage()

    posts_doc = safe_load(storage, f"reports/{week}/posts.json")
    pagespeed = safe_load(storage, f"reports/{week}/pagespeed.json")
    gsc = safe_load(storage, f"reports/{week}/search-console.json")

    if not posts_doc:
        print(f"error: missing posts.json for {week}", file=sys.stderr)
        return 1

    issues_all: list[dict] = []
    issues_all.extend(cwv_issues(pagespeed))
    issues_all.extend(on_page_issues(posts_doc))
    issues_all.extend(search_quality_issues(gsc))
    issues_all.extend(indexation_issues(gsc))

    issues_all.sort(key=sort_key)

    summary = {
        "critical": sum(1 for i in issues_all if i["severity"] == "critical"),
        "warning": sum(1 for i in issues_all if i["severity"] == "warning"),
        "info": sum(1 for i in issues_all if i["severity"] == "info"),
    }

    sources_available = []
    if pagespeed:
        sources_available.append("pagespeed")
    if gsc:
        sources_available.append("search-console")
    sources_available.append("posts")

    pagespeed_coverage = (pagespeed or {}).get("url_count", 0)
    coverage_note = None
    expected = posts_doc["post_count"] + 1  # +1 for homepage
    if pagespeed and pagespeed_coverage < expected:
        coverage_note = (
            f"PSI data covers {pagespeed_coverage} of {expected} URLs. "
            "Re-run fetch-pagespeed without --limit for full CWV coverage."
        )

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "data_sources_available": sources_available,
        "coverage_note": coverage_note,
        "summary": summary,
        "issues": issues_all[: args.max_issues],
        "issues_omitted": max(0, len(issues_all) - args.max_issues),
    }

    out_key = f"reports/{week}/insights.seo-health.json"
    storage.write_json(out_key, output)

    print(f"wrote {out_key}")
    print(f"  data sources: {sources_available}")
    print(f"  issues: critical={summary['critical']} warning={summary['warning']} info={summary['info']} total={len(issues_all)}")
    if coverage_note:
        print(f"  note: {coverage_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
