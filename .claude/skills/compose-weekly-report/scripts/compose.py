"""compose-weekly-report: assemble the markdown report from all available insights.

Phase 1 (today): templated markdown only. Writes report.md to the snapshot store.
Phase 2 (when GOOGLE_DRIVE_* env is set): uploads the markdown to a new Google Doc
in the configured Drive folder and returns the URL.

Missing analyzer outputs are surfaced as "data unavailable this week" rather than
crashing — the report should always render, even if sections are thin.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from lib.env import load_dotenv  # noqa: E402
from lib.storage import Storage, get_storage  # noqa: E402
from lib.week import current_week_id, report_week_id, week_bounds  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id (default: current week)")
    parser.add_argument("--print", action="store_true", help="Also print the report to stdout")
    return parser.parse_args()


def safe_load(storage: Storage, key: str) -> dict | None:
    if not storage.exists(key):
        return None
    return storage.read_json(key)


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.0%}"


def fmt_int(value) -> str:
    return f"{value:,}" if isinstance(value, int) else str(value)


def render_tldr(content_perf: dict | None, kit: dict | None) -> str:
    if not content_perf:
        return "_TL;DR unavailable — analyze-content-performance has not run yet._\n"
    h = content_perf["headline"]
    pv_pct = fmt_pct(h.get("pageviews_wow_pct"))
    su_pct = fmt_pct(h.get("signups_wow_pct"))
    total_subs = (kit or {}).get("subscribers", {}).get("total")
    lines = []
    if h.get("clicks_this_week") is not None:
        cl_pct = fmt_pct(h.get("clicks_wow_pct"))
        lines.append(
            f"- **Search clicks:** {h['clicks_this_week']} this week "
            f"({cl_pct} WoW, was {h['clicks_last_week']}) on "
            f"{h.get('impressions_this_week', 0)} impressions"
        )
    lines.append(
        f"- **Pageviews:** {h.get('pageviews_this_week')} this week ({pv_pct} WoW, was {h.get('pageviews_last_week')})"
    )
    lines.append(
        f"- **New signups:** {h.get('signups_this_week')} this week ({su_pct} WoW, was {h.get('signups_last_week')})"
    )
    if total_subs is not None:
        lines.append(f"- **Total CC4M subscribers:** {fmt_int(total_subs)}")
    if h.get("clicks_this_week") is not None:
        lines.append(
            "- _Note: GSC has a ~2 day data lag, so the current week is partial. WoW deltas on clicks/impressions skew low mid-week — read the absolute numbers and the appendix._"
        )
    return "\n".join(lines) + "\n"


def render_focus(content_perf: dict | None) -> str:
    """Best-effort 'this week worth a look' list, prioritizing striking-distance > pageview lifts > decay."""
    if not content_perf:
        return "_Focus list unavailable._\n"
    items: list[str] = []
    striking = content_perf.get("striking_distance_keywords") or []
    winners = content_perf.get("winners_this_week") or []
    decay = content_perf.get("decaying_posts") or []
    ranking_decay = content_perf.get("ranking_decay") or []
    surprises = content_perf.get("surprise_winners") or []

    # 1. Best striking-distance opportunity (highest leverage — refresh + internal links)
    if striking:
        top = striking[0]
        page_label = top.get("title") or top.get("slug")
        items.append(
            f"**Push `\"{top['query']}\"` from position {top['current_position']} to top 5.** "
            f"On *{page_label}* — {top['impressions']} impressions, only {top['current_clicks']} clicks. "
            "Refresh the post, add internal links, retitle the H1 if it doesn't match the query."
        )

    # 2. Surprise win → consider a dedicated post
    if surprises:
        s = surprises[0]
        items.append(
            f"**Consider a dedicated post on `\"{s['query']}\"`.** "
            f"You're already ranking position {s['current_position']} via *{s.get('title') or s.get('slug')}* "
            f"(declared target: `{s['declared_target']}`). A focused post likely captures the click."
        )

    # 3. Pageview lift worth investigating
    if winners:
        top = winners[0]
        title = top.get("title") or top.get("slug") or top.get("path")
        items.append(
            f"**Investigate the lift on `{title}`.** +{top['absolute_delta']} pageviews WoW "
            f"({top['pageviews_last_week']} → {top['pageviews_this_week']}). "
            "If something promoted it, lean in."
        )

    # 4. Decay (ranking decay preferred over pageview decay because it's more diagnostic)
    if ranking_decay:
        r = ranking_decay[0]
        items.append(
            f"**Refresh `{r.get('title') or r.get('slug')}`.** Position drifted "
            f"{r['prior_avg_position']} → {r['recent_avg_position']} (+{r['position_delta']}). "
            "Check for stale claims, missing internal links, or competitors with fresher coverage."
        )
    elif decay:
        worst = decay[0]
        title = worst.get("title") or worst.get("slug") or worst.get("path")
        items.append(
            f"**Refresh `{title}`.** This week ran {worst['pageviews_this_week']} pv vs. "
            f"a 4-week average of {worst['trailing_4w_avg']}."
        )

    if not items:
        items.append("_No clear focus items this week. Either everything is healthy or data is too thin. Re-run next Sunday._")
    return "\n".join(f"{i + 1}. {text}" for i, text in enumerate(items[:5])) + "\n"


def render_seo_health(seo: dict | None) -> str:
    if not seo:
        return "_SEO health analyzer has not run yet (analyze-seo-health pending). Once wired, this section will list prioritized indexation, Core Web Vitals, and on-page issues._\n"
    summary = seo.get("summary") or {}
    issues = seo.get("issues") or []
    out = [
        f"- **Critical:** {summary.get('critical', 0)} | "
        f"**Warning:** {summary.get('warning', 0)} | "
        f"**Info:** {summary.get('info', 0)}",
        "",
    ]
    for issue in issues[:10]:
        out.append(
            f"- **[{issue['severity'].upper()}]** "
            f"`{issue.get('page', '?')}` — {issue.get('issue', '')}"
        )
        if issue.get("recommended_action"):
            out.append(f"  - _Action:_ {issue['recommended_action']} (effort: {issue.get('effort', '?')}, impact: {issue.get('impact', '?')})")
    return "\n".join(out) + "\n"


def render_content_perf(content_perf: dict | None) -> str:
    if not content_perf:
        return "_Content performance analyzer has not run yet._\n"
    parts: list[str] = []

    winners = content_perf.get("winners_this_week") or []
    parts.append("### Winners this week\n")
    if winners:
        for w in winners:
            title = w.get("title") or w.get("slug") or w.get("path")
            parts.append(
                f"- **{title}** — {w['pageviews_this_week']} pv "
                f"(was {w['pageviews_last_week']}, +{w['absolute_delta']})"
            )
    else:
        parts.append("_No qualifying winners this week._")
    parts.append("")

    decay = content_perf.get("decaying_posts") or []
    parts.append("### Decaying posts\n")
    if decay:
        for d in decay:
            title = d.get("title") or d.get("slug")
            parts.append(
                f"- **{title}** — {d['pageviews_this_week']} pv "
                f"(4-week avg: {d['trailing_4w_avg']}, ratio: {d['ratio_vs_avg']:.2f})"
            )
    else:
        parts.append("_None flagged. Decay detection needs ≥5 weeks of trailing data; the Fathom integration is recent so this section will populate over the coming weeks._")
    parts.append("")

    attribution = content_perf.get("signup_attribution") or []
    parts.append("### Signup attribution\n")
    if attribution:
        for a in attribution:
            title = a.get("title") or a.get("slug")
            parts.append(
                f"- **{title}** — total {a['total_subscribers']}, this week +{a['this_week_new']}"
            )
    else:
        parts.append("_No posts with unique Kit tags. Add `kitTagId` to high-intent posts to attribute signups per page._")
    parts.append("")

    striking = content_perf.get("striking_distance_keywords") or []
    parts.append("### Striking-distance keywords\n")
    if striking:
        for s in striking:
            page_label = s.get("title") or s.get("slug") or s.get("page")
            parts.append(
                f"- **\"{s['query']}\"** — position {s['current_position']}, "
                f"{s['impressions']} impressions, only {s['current_clicks']} clicks. "
                f"On *{page_label}*."
            )
    else:
        parts.append("_Nothing in the 8–20 position band with ≥50 impressions. Either everything is on page 1 or volume is below the floor._")
    parts.append("")

    ranking_decay = content_perf.get("ranking_decay") or []
    parts.append("### Ranking decay\n")
    if ranking_decay:
        for r in ranking_decay:
            page_label = r.get("title") or r.get("slug")
            parts.append(
                f"- **{page_label}** — position drifted from {r['prior_avg_position']} → "
                f"{r['recent_avg_position']} (Δ +{r['position_delta']}) "
                f"on ~{r['recent_avg_impressions_per_week']} impressions/week."
            )
    else:
        parts.append("_No pages have lost ≥2 positions over the recent 4 weeks. Healthy._")
    parts.append("")

    surprises = content_perf.get("surprise_winners") or []
    parts.append("### Surprise wins\n")
    if surprises:
        for s in surprises:
            page_label = s.get("title") or s.get("slug")
            parts.append(
                f"- **\"{s['query']}\"** — position {s['current_position']}, "
                f"{s['impressions']} impressions. Ranking via *{page_label}* "
                f"which targets `{s['declared_target']}`. "
                "Worth a dedicated post or stronger declared targeting?"
            )
    else:
        parts.append("_Posts are ranking for the keywords they declared. No accidental wins this week._")
    parts.append("")

    return "\n".join(parts) + "\n"


def render_backlog(opportunities: dict | None) -> str:
    if not opportunities:
        return "_Backlog suggestions unavailable — `analyze-keyword-opportunities` not yet wired._\n"
    parts: list[str] = []

    # Declared-target gaps section — the highest-leverage finding when DFS is wired.
    gaps = opportunities.get("declared_target_gaps") or []
    if gaps:
        parts.append("### Declared targets the site isn't ranking for\n")
        parts.append("_Posts that explicitly declared a high-volume target keyword in frontmatter, but Google isn't surfacing them in the top 10. Highest-leverage refresh signal._")
        parts.append("")
        for g in gaps[:8]:
            vol = g.get("search_volume")
            kd = g.get("keyword_difficulty")
            band = g.get("kd_band")
            kd_str = f"KD {kd} ({band})" if band else (f"KD {kd}" if kd is not None else "KD ?")
            pos = g.get("current_position")
            pos_str = f"current pos {pos:.0f}" if pos else "not in top queries"
            parts.append(
                f"- **`{g['target_query']}`** — {vol}/mo, {kd_str}, {pos_str}. Targeted by *{g['title']}*."
            )
        parts.append("")

    refreshes = opportunities.get("post_refresh_suggestions") or []
    parts.append("### Top 3 refreshes this week\n")
    if refreshes:
        for r in refreshes:
            signal = r.get("primary_signal", "?")
            title = r.get("title") or r.get("slug")
            parts.append(f"- **{title}** _(signal: {signal})_")
            parts.append(f"  - {r.get('rationale', '')}")
            recs = r.get("recommended_changes") or []
            if recs:
                parts.append(f"  - Suggested: {recs[0]}")
        parts.append("")
    else:
        parts.append("_No refresh suggestions this week._")
        parts.append("")

    new_posts = opportunities.get("new_post_suggestions") or []
    parts.append("### New post suggestions\n")
    if new_posts:
        for p in new_posts:
            vol = p.get("search_volume")
            kd = p.get("keyword_difficulty")
            band = p.get("kd_band")
            specs: list[str] = []
            if vol is not None:
                specs.append(f"{vol}/mo")
            if band:
                specs.append(f"KD {kd} ({band})")
            elif kd is not None:
                specs.append(f"KD {kd}")
            spec_str = f" ({', '.join(specs)})" if specs else ""
            parts.append(f"- **{p['primary_keyword']}**{spec_str}")
            if p.get("rationale"):
                parts.append(f"  - {p['rationale']}")
    else:
        parts.append("_No new post suggestions this week._")
    return "\n".join(parts) + "\n"


def render_appendix(posts_doc: dict | None, fathom: dict | None, kit: dict | None) -> str:
    parts: list[str] = []

    if kit:
        sub = kit.get("subscribers", {})
        parts.append("### Subscriber history (last 8 weeks)\n")
        parts.append("| Week | New |")
        parts.append("|------|-----|")
        for w in sub.get("weekly_history", []):
            parts.append(f"| {w['week']} | {w['new']} |")
        parts.append("")

    if fathom:
        referrers = fathom.get("top_referrers_this_week") or []
        if referrers:
            parts.append("### Top referrers this week\n")
            parts.append("| Source | Pageviews | Uniques |")
            parts.append("|--------|-----------|---------|")
            for r in referrers[:10]:
                source = r.get("referrer_hostname") or "(direct)"
                parts.append(f"| {source} | {r.get('pageviews', 0)} | {r.get('uniques', 0)} |")
            parts.append("")

    if posts_doc:
        issues = posts_doc.get("manifest_issues") or []
        if issues:
            parts.append("### Frontmatter issues\n")
            for i in issues:
                parts.append(f"- `{i['slug']}`: {i['issue']}")
            parts.append("")

    return "\n".join(parts) if parts else "_No appendix data yet._\n"


def build_report(week: str, storage: Storage) -> str:
    posts_doc = safe_load(storage, f"reports/{week}/posts.json")
    fathom = safe_load(storage, f"reports/{week}/fathom.json")
    kit = safe_load(storage, f"reports/{week}/kit.json")
    pagespeed = safe_load(storage, f"reports/{week}/pagespeed.json")  # noqa: F841 — wired when seo-health analyzer lands
    content_perf = safe_load(storage, f"reports/{week}/insights.content-perf.json")
    seo_health = safe_load(storage, f"reports/{week}/insights.seo-health.json")
    opportunities = safe_load(storage, f"reports/{week}/insights.opportunities.json")

    week_start, week_end = week_bounds(week)
    title = f"Weekly Site Report — Week of {week_start.strftime('%b %d, %Y')}"
    generated = datetime.now(timezone.utc).isoformat(timespec="minutes")

    sections = [
        f"# {title}",
        "",
        f"_{week} ({week_start} → {week_end}). Generated {generated}._",
        "",
        "## TL;DR",
        "",
        render_tldr(content_perf, kit),
        "## This week's focus",
        "",
        render_focus(content_perf),
        "## SEO health",
        "",
        render_seo_health(seo_health),
        "## Content performance",
        "",
        render_content_perf(content_perf),
        "## Content backlog",
        "",
        render_backlog(opportunities),
        "## Appendix",
        "",
        render_appendix(posts_doc, fathom, kit),
    ]
    return "\n".join(sections)


def maybe_upload_to_drive(week: str, title: str, markdown_content: str) -> str | None:
    """Upload the markdown report to Google Drive as a Google Doc.

    Drive's create-with-conversion endpoint converts text/markdown automatically
    when mimeType=application/vnd.google-apps.document is set on creation.
    Returns the doc's webViewLink, or None if not configured / on failure.
    """
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    creds_path = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON")
    if not folder_id or not creds_path:
        return None

    from google.oauth2 import service_account  # noqa: PLC0415
    from googleapiclient.discovery import build  # noqa: PLC0415
    from googleapiclient.http import MediaInMemoryUpload  # noqa: PLC0415

    creds = service_account.Credentials.from_service_account_file(
        creds_path,
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [folder_id],
    }
    media = MediaInMemoryUpload(
        markdown_content.encode("utf-8"),
        mimetype="text/markdown",
        resumable=False,
    )
    # Drive's upload endpoint occasionally returns transient 5xx; retry up to 3 times.
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            file = service.files().create(
                body=metadata,
                media_body=media,
                fields="id,webViewLink",
                supportsAllDrives=True,
            ).execute()
            return file.get("webViewLink")
        except Exception as e:
            last_error = e
            status = getattr(getattr(e, "resp", None), "status", None)
            if status and 500 <= status < 600 and attempt < 2:
                import time  # noqa: PLC0415
                wait = 2 ** attempt
                print(f"Drive upload {status}, retry in {wait}s ({attempt + 1}/3)", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    if last_error:
        raise last_error
    return None


def main() -> int:
    load_dotenv()
    args = parse_args()
    week = args.week or report_week_id()
    storage = get_storage()

    if not storage.exists(f"reports/{week}/posts.json"):
        print(f"error: missing reports/{week}/posts.json — run fetchers first", file=sys.stderr)
        return 1

    report = build_report(week, storage)
    storage.write_text(f"reports/{week}/report.md", report)

    week_start, _ = week_bounds(week)
    doc_title = f"Weekly Site Report — Week of {week_start.strftime('%b %d, %Y')}"
    try:
        doc_url = maybe_upload_to_drive(week, doc_title, report)
    except Exception as e:
        print(f"warning: Drive upload failed ({e}); markdown was still saved", file=sys.stderr)
        doc_url = None
    if doc_url:
        storage.write_text(f"reports/{week}/doc-url.txt", doc_url)
        print(f"uploaded to Google Doc: {doc_url}")

    print(f"wrote reports/{week}/report.md ({len(report)} chars)")
    if args.print:
        print("\n" + "=" * 60 + "\n")
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
