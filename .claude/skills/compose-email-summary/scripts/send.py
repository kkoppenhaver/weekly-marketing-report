"""compose-email-summary: produce and (optionally) send the Sunday digest email.

Always writes summary.md to the snapshot store. Sends via Resend only when
RESEND_API_KEY + REPORT_TO_EMAIL + REPORT_FROM_EMAIL are configured.
Use --dry-run to print without sending even when creds are present.
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

WEEK_NUM_FOR_SUBJECT = "Week"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id (default: current week)")
    parser.add_argument("--dry-run", action="store_true", help="Print, do not send")
    return parser.parse_args()


def safe_load(storage: Storage, key: str) -> dict | None:
    if not storage.exists(key):
        return None
    return storage.read_json(key)


def safe_text(storage: Storage, key: str) -> str | None:
    if not storage.exists(key):
        return None
    return storage.read_text(key).strip()


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.0%}"


def headline_line(
    content_perf: dict | None,
    opportunities: dict | None = None,
    seo_health: dict | None = None,
) -> str:
    """Pick the highest-signal headline available."""
    # 0. Critical SEO issue (e.g., unindexed page) — most urgent, leads everything else.
    critical = [i for i in (seo_health or {}).get("issues", []) if i.get("severity") == "critical"]
    if critical:
        top = critical[0]
        page = top.get("page", "?")
        return f"{page}: {top.get('issue', 'critical issue')}"

    # 1. Big declared-target gap with DFS volume.
    gaps = (opportunities or {}).get("declared_target_gaps") or []
    if gaps:
        top = gaps[0]
        vol = top.get("search_volume")
        if vol:
            return f"{top['slug']} is missing {vol}/mo on \"{top['target_query']}\""

    # 2. Striking-distance opportunity
    striking = (content_perf or {}).get("striking_distance_keywords") or []
    if striking:
        top = striking[0]
        return f"{len(striking)} striking-distance keyword(s) — top: \"{top['query']}\" at pos {top['current_position']}"

    if not content_perf:
        return "no analysis available this week"
    h = content_perf["headline"]
    clicks = h.get("clicks_this_week")
    if clicks is not None:
        return f"{clicks} search clicks on {h.get('impressions_this_week', 0)} impressions"
    pv = h.get("pageviews_this_week")
    return f"{pv} pageviews"


def build_focus_bullets(
    content_perf: dict | None,
    opportunities: dict | None = None,
    seo_health: dict | None = None,
) -> list[str]:
    if not content_perf and not opportunities and not seo_health:
        return []
    bullets: list[str] = []
    cp = content_perf or {}
    striking = cp.get("striking_distance_keywords") or []
    surprises = cp.get("surprise_winners") or []
    winners = cp.get("winners_this_week") or []
    ranking_decay = cp.get("ranking_decay") or []
    refreshes = (opportunities or {}).get("post_refresh_suggestions") or []
    new_posts = (opportunities or {}).get("new_post_suggestions") or []
    used_queries: set[str] = set()
    used_slugs: set[str] = set()

    # 0. Critical SEO issues lead — usually indexation problems that block ranking.
    critical_issues = [i for i in (seo_health or {}).get("issues", []) if i.get("severity") == "critical"]
    if critical_issues:
        top = critical_issues[0]
        page = top.get("page", "?")
        bullets.append(
            f"FIX: {page} — {top.get('issue', 'critical issue')}. "
            f"{top.get('recommended_action', '')}"
        )
        used_slugs.add(page.split("/")[-1])

    # 1. Top declared-target gap — usually the highest-leverage action.
    declared_gap_refresh = next((r for r in refreshes if r.get("primary_signal") == "declared_target_gap"), None)
    if declared_gap_refresh:
        r = declared_gap_refresh
        used_slugs.add(r.get("slug") or "")
        used_queries.add((r.get("target_query") or "").lower())
        vol = r.get("search_volume")
        band = r.get("kd_band")
        kd_str = f", KD {r.get('keyword_difficulty')} ({band})" if band else ""
        bullets.append(
            f"Refresh \"{r.get('title') or r.get('slug')}\" — declared target \"{r.get('target_query')}\" "
            f"is {vol}/mo{kd_str} but the post isn't ranking. Highest-leverage move available."
        )

    if striking and len(bullets) < 3:
        # Skip striking distance if we already used the same page in declared-gap bullet.
        s_pick = next((s for s in striking if (s.get("slug") or "") not in used_slugs), None)
        if s_pick:
            s = s_pick
            used_queries.add(s["query"].lower())
            used_slugs.add(s.get("slug") or "")
            bullets.append(
                f"Push \"{s['query']}\" from pos {s['current_position']} to top 5 — "
                f"on {s.get('title') or s.get('slug')}, {s['impressions']} impressions / "
                f"{s['current_clicks']} clicks. Refresh + internal links."
            )
    # Pick the first surprise winner that isn't redundant with the striking bullet.
    surprise_pick = next((s for s in surprises if s["query"].lower() not in used_queries), None)
    if surprise_pick:
        s = surprise_pick
        used_queries.add(s["query"].lower())
        bullets.append(
            f"Consider a dedicated post on \"{s['query']}\" — already ranking pos "
            f"{s['current_position']} via {s.get('title') or s.get('slug')}."
        )
    if winners and len(bullets) < 3:
        top = winners[0]
        bullets.append(
            f"Investigate the pageview lift on \"{top.get('title') or top.get('slug') or top.get('path')}\" "
            f"(+{top['absolute_delta']} WoW)."
        )
    if ranking_decay and len(bullets) < 3:
        r = ranking_decay[0]
        bullets.append(
            f"Refresh \"{r.get('title') or r.get('slug')}\" — position drifted "
            f"{r['prior_avg_position']} → {r['recent_avg_position']}."
        )
    return bullets[:3]


def build_email(week: str, storage: Storage) -> tuple[str, str]:
    """Return (subject, body_markdown)."""
    content_perf = safe_load(storage, f"reports/{week}/insights.content-perf.json")
    opportunities = safe_load(storage, f"reports/{week}/insights.opportunities.json")
    seo_health = safe_load(storage, f"reports/{week}/insights.seo-health.json")
    kit = safe_load(storage, f"reports/{week}/kit.json")
    doc_url = safe_text(storage, f"reports/{week}/doc-url.txt")

    week_start, _ = week_bounds(week)
    week_iso_num = int(week.split("-W")[1])

    subject = f"CC4M site report — Week {week_iso_num} — {headline_line(content_perf, opportunities, seo_health)}"

    h = (content_perf or {}).get("headline", {})
    sub = (kit or {}).get("subscribers", {})

    lines: list[str] = []
    lines.append(f"Site report for the week of {week_start.strftime('%b %d, %Y')}.")
    lines.append("")
    lines.append("Highlights:")
    if content_perf and h.get("clicks_this_week") is not None:
        lines.append(
            f"• Search clicks {h.get('clicks_this_week')} on {h.get('impressions_this_week', 0)} impressions "
            f"({fmt_pct(h.get('clicks_wow_pct'))} WoW)"
        )
    if content_perf:
        pv_now = h.get("pageviews_this_week")
        pv_prior = h.get("pageviews_last_week")
        pv_pct = h.get("pageviews_wow_pct")
        if pv_pct is None or not pv_prior:
            lines.append(f"• Pageviews {pv_now} (no prior-week comparison available)")
        else:
            lines.append(f"• Pageviews {pv_now} ({fmt_pct(pv_pct)} WoW, was {pv_prior})")
    if kit:
        lines.append(
            f"• New signups {sub.get('this_week_new')} this week, {sub.get('last_week_new')} last week"
        )
        lines.append(f"• {sub.get('total')} total CC4M subscribers")
    seo_health = safe_load(storage, f"reports/{week}/insights.seo-health.json")
    if seo_health:
        s = seo_health.get("summary", {})
        lines.append(
            f"• SEO health: {s.get('critical', 0)} critical / {s.get('warning', 0)} warnings / {s.get('info', 0)} info"
        )
    missing = (content_perf or {}).get("data_sources_missing") or []
    if missing:
        lines.append(f"• Missing this week: {', '.join(missing)} (analyzers degrade gracefully)")
    lines.append("")

    focus = build_focus_bullets(content_perf, opportunities, seo_health)
    if focus:
        lines.append("Worth a look:")
        for i, bullet in enumerate(focus, start=1):
            lines.append(f"{i}. {bullet}")
        lines.append("")

    if doc_url:
        lines.append(f"Full report: {doc_url}")
    else:
        lines.append("Full report: (Drive upload not configured — see snapshots/reports/{}/report.md)".format(week))
    lines.append("")
    lines.append("— Sunday digest")
    body = "\n".join(lines)
    return subject, body


def maybe_send_resend(subject: str, body: str) -> bool:
    api_key = os.getenv("RESEND_API_KEY")
    to_email = os.getenv("REPORT_TO_EMAIL")
    from_email = os.getenv("REPORT_FROM_EMAIL")
    if not (api_key and to_email and from_email):
        return False
    import resend  # imported lazily so dev works without it installed

    resend.api_key = api_key
    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    response = resend.Emails.send(payload)
    print(f"sent via Resend (id: {response.get('id', '?')})")
    return True


def main() -> int:
    load_dotenv()
    args = parse_args()
    week = args.week or report_week_id()
    storage = get_storage()

    if not storage.exists(f"reports/{week}/insights.content-perf.json"):
        print(f"warning: no content-perf insights for {week} — email will be thin", file=sys.stderr)

    subject, body = build_email(week, storage)
    full = f"Subject: {subject}\n\n{body}\n"
    storage.write_text(f"reports/{week}/summary.md", full)

    if args.dry_run:
        print("=" * 60)
        print(full)
        print("=" * 60)
        return 0

    sent = maybe_send_resend(subject, body)
    if not sent:
        print("(Resend not configured — email saved to snapshot store only)")
        print("\n" + "=" * 60)
        print(full)
        print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
