"""fetch-pagespeed: run PageSpeed Insights against every post URL + homepage.

Reads posts.json from the snapshot store, hits the PSI v5 API for each
URL × {mobile, desktop} with bounded concurrency, extracts performance
score, lab Core Web Vitals (LCP/CLS/TBT), field metrics (LCP/CLS/INP)
when CrUX data is available, and the IDs of failing performance audits.
Writes pagespeed.json.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make ./lib importable when running this script directly.
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from lib.env import load_dotenv  # noqa: E402
from lib.storage import get_storage  # noqa: E402
from lib.week import current_week_id, report_week_id  # noqa: E402

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
STRATEGIES = ("mobile", "desktop")
CONCURRENCY = 5
PER_REQUEST_TIMEOUT = 90.0  # PSI runs Lighthouse server-side; can be slow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id (default: current week)")
    parser.add_argument("--limit", type=int, help="Cap URL count (debugging)")
    parser.add_argument(
        "--no-homepage",
        action="store_true",
        help="Skip the site homepage; only audit post URLs",
    )
    return parser.parse_args()


def collect_urls(posts: list[dict], site_url: str, include_home: bool) -> list[str]:
    urls: list[str] = []
    if include_home:
        urls.append(site_url.rstrip("/") + "/")
    urls.extend(p["url"] for p in posts if not p.get("draft", False))
    # Deduplicate while preserving order.
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


async def fetch_one(
    client: httpx.AsyncClient,
    url: str,
    strategy: str,
    api_key: str,
) -> tuple[str, str, dict | None, str | None]:
    """Return (url, strategy, parsed_result_or_None, error_or_None).

    PSI's Lighthouse backend regularly returns 500 ("Something went wrong") for
    individual URLs even when the URL is fine. We retry once on 5xx and on
    timeouts/connection errors; that recovers most of the ~7% of calls that
    flake on a typical run. 4xx errors don't retry — those are real config bugs.
    """
    params = {
        "url": url,
        "strategy": strategy,
        "category": "performance",
        "key": api_key,
    }
    last_error: str | None = None
    for attempt in range(2):  # original try + 1 retry
        try:
            resp = await client.get(PSI_ENDPOINT, params=params, timeout=PER_REQUEST_TIMEOUT)
            resp.raise_for_status()
            return url, strategy, parse_psi(resp.json()), None
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            last_error = f"HTTP {e.response.status_code}: {body}"
            if 500 <= e.response.status_code < 600 and attempt == 0:
                await asyncio.sleep(3)
                continue
            return url, strategy, None, last_error
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt == 0:
                await asyncio.sleep(3)
                continue
            return url, strategy, None, last_error
    return url, strategy, None, last_error


def parse_psi(payload: dict[str, Any]) -> dict[str, Any]:
    lh = payload.get("lighthouseResult", {})
    audits = lh.get("audits", {})
    perf_cat = lh.get("categories", {}).get("performance", {})

    # Failing perf audits: score is 0-1 or null. Treat <0.9 as "issue worth surfacing."
    perf_audit_ids = {
        ref["id"]
        for ref in perf_cat.get("auditRefs", [])
        if ref.get("group") in {"metrics", "diagnostics", "load-opportunities"}
    }
    issues: list[str] = []
    for audit_id in perf_audit_ids:
        audit = audits.get(audit_id)
        if not audit:
            continue
        score = audit.get("score")
        if score is None or score >= 0.9:
            continue
        issues.append(audit_id)
    issues.sort()

    lab = {
        "lcp_ms": _audit_numeric(audits, "largest-contentful-paint"),
        "cls": _audit_numeric(audits, "cumulative-layout-shift"),
        "tbt_ms": _audit_numeric(audits, "total-blocking-time"),
        "fcp_ms": _audit_numeric(audits, "first-contentful-paint"),
        "speed_index_ms": _audit_numeric(audits, "speed-index"),
    }

    field = parse_field_metrics(payload.get("loadingExperience", {}))

    return {
        "performance": perf_cat.get("score"),
        "lab": lab,
        "field": field,
        "issues": issues,
    }


def _audit_numeric(audits: dict, key: str) -> float | None:
    audit = audits.get(key) or {}
    val = audit.get("numericValue")
    return round(val, 3) if isinstance(val, int | float) else None


def parse_field_metrics(loading: dict[str, Any]) -> dict[str, Any] | None:
    """Real-user CrUX data; only present for sites with enough traffic."""
    metrics = loading.get("metrics") or {}
    if not metrics:
        return None
    out: dict[str, Any] = {}
    if (m := metrics.get("LARGEST_CONTENTFUL_PAINT_MS")):
        out["lcp_ms"] = m.get("percentile")
    if (m := metrics.get("CUMULATIVE_LAYOUT_SHIFT_SCORE")):
        # CLS is reported × 100 in the field metrics.
        p = m.get("percentile")
        out["cls"] = round(p / 100, 3) if isinstance(p, int | float) else None
    if (m := metrics.get("INTERACTION_TO_NEXT_PAINT")):
        out["inp_ms"] = m.get("percentile")
    if (m := metrics.get("FIRST_CONTENTFUL_PAINT_MS")):
        out["fcp_ms"] = m.get("percentile")
    return out or None


async def run(urls: list[str], api_key: str) -> tuple[dict[str, dict], list[dict]]:
    sem = asyncio.Semaphore(CONCURRENCY)
    by_url: dict[str, dict] = {url: {} for url in urls}
    errors: list[dict] = []

    async with httpx.AsyncClient() as client:

        async def bounded(url: str, strategy: str) -> None:
            async with sem:
                u, s, parsed, err = await fetch_one(client, url, strategy, api_key)
                if err:
                    errors.append({"url": u, "strategy": s, "error": err})
                    print(f"  ✗ {s:7s} {u}\n      {err}", file=sys.stderr)
                else:
                    by_url[u][s] = parsed
                    perf = parsed["performance"]
                    perf_str = f"{perf:.2f}" if isinstance(perf, int | float) else "n/a"
                    print(f"  ✓ {s:7s} {u}  perf={perf_str}  issues={len(parsed['issues'])}")

        tasks = [bounded(u, s) for u in urls for s in STRATEGIES]
        await asyncio.gather(*tasks)

    return by_url, errors


def main() -> int:
    load_dotenv()
    args = parse_args()
    week = args.week or report_week_id()

    api_key = os.getenv("PSI_API_KEY")
    if not api_key:
        print("error: PSI_API_KEY is required (set in .env)", file=sys.stderr)
        return 1

    site_url = os.getenv("SITE_URL", "https://claudecodeformarketers.com")
    storage = get_storage()
    manifest_key = f"reports/{week}/posts.json"
    if not storage.exists(manifest_key):
        print(f"error: missing {manifest_key} — run fetch-post-manifest first", file=sys.stderr)
        return 1

    manifest = storage.read_json(manifest_key)
    urls = collect_urls(manifest["posts"], site_url, include_home=not args.no_homepage)
    if args.limit:
        urls = urls[: args.limit]

    print(f"running PSI on {len(urls)} URL(s) × {len(STRATEGIES)} strategies = {len(urls) * len(STRATEGIES)} call(s)")

    by_url, errors = asyncio.run(run(urls, api_key))

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "url_count": len(urls),
        "by_url": by_url,
        "errors": errors,
    }

    out_key = f"reports/{week}/pagespeed.json"
    storage.write_json(out_key, output)
    print(f"\nwrote {out_key}: {len(by_url)} URL(s), {len(errors)} error(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
