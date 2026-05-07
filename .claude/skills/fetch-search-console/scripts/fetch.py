"""fetch-search-console: 90-day per-page + per-query data from GSC,
aggregated into ISO weeks. Plus top queries per page for the current week.

Auth: OAuth 2.0 user credentials (Desktop client).
- Service-account path is broken by a confirmed Google bug (May 2026).
- First run pops a browser for one-time consent; refresh token is persisted
  to GSC_OAUTH_TOKEN_JSON for all subsequent runs.
- For long-lived tokens the OAuth consent screen must be PUBLISHED to
  Production (Testing mode tokens expire after 7 days).

If a refresh ever fails (invalid_grant), this script exits loudly with
instructions — silent failure on a weekly cron is the wrong default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from google.auth.exceptions import RefreshError  # noqa: E402
from google.auth.transport.requests import Request  # noqa: E402
from google.oauth2.credentials import Credentials  # noqa: E402
from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

from lib.credential import load_credentials_json  # noqa: E402
from lib.env import load_dotenv  # noqa: E402
from lib.storage import get_storage  # noqa: E402
from lib.week import current_week_id, report_week_id  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
WINDOW_DAYS = 90
ROW_LIMIT = 25000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id (default: current week)")
    parser.add_argument(
        "--reauth",
        action="store_true",
        help="Force re-running the consent flow (use after invalid_grant errors)",
    )
    return parser.parse_args()


def load_credentials(client_value: str, token_value: str, force_reauth: bool) -> Credentials:
    """Build OAuth credentials from env vars that hold either file paths or inline JSON.

    In local dev the values point at files in ~/.config/cc4m-report/.
    In the Routine, the values are the JSON content inlined directly.
    Token persistence (refreshed access_token written back) only applies in the
    file-path case; in the inline case we just refresh in memory each run.
    """
    token_path = _maybe_path(token_value)

    if force_reauth and token_path and token_path.exists():
        print(f"--reauth: removing {token_path}")
        token_path.unlink()

    # Try to load existing token (file or inline)
    creds: Credentials | None = None
    try:
        token_info = load_credentials_json(token_value)
        creds = Credentials.from_authorized_user_info(token_info, SCOPES)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        # Token missing or malformed — fall through to consent flow if we have a path.
        if token_path:
            print(f"note: token at {token_path} not loadable ({e}); will run consent flow", file=sys.stderr)
        else:
            # Inline JSON was supposed to be there. Hard fail rather than try interactive consent.
            print(f"error: GSC_OAUTH_TOKEN_JSON is invalid ({e})", file=sys.stderr)
            sys.exit(1)

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            if token_path:
                _save_token(creds, token_path)
            return creds
        except RefreshError as e:
            _fail_loudly(token_path or "<inline>", e)

    # Need consent flow — only meaningful when we have a file path to write to.
    if not token_path:
        print("error: no token loadable and no file path to persist a new one. Re-run locally to mint a token.", file=sys.stderr)
        sys.exit(1)
    client_path = _maybe_path(client_value)
    if not client_path or not client_path.exists():
        print(f"error: GSC_OAUTH_CLIENT_JSON not loadable as a file ({client_value})", file=sys.stderr)
        sys.exit(1)

    print("opening browser for one-time GSC consent…")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
    login_hint = os.getenv("GSC_OAUTH_LOGIN_HINT")
    extra: dict = {"prompt": "consent", "access_type": "offline"}
    if login_hint:
        extra["login_hint"] = login_hint
    creds = flow.run_local_server(port=0, open_browser=True, **extra)
    _save_token(creds, token_path)
    print(f"saved refresh token to {token_path}")
    return creds


def _maybe_path(value: str) -> Path | None:
    """Return a Path if `value` looks like a path (not inline JSON), else None."""
    s = (value or "").strip()
    if not s or s.startswith("{"):
        return None
    return Path(s).expanduser()


def _save_token(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    # Tighten perms — refresh tokens are ~equivalent to a long-lived password.
    os.chmod(token_path, 0o600)


def _fail_loudly(token_path: Path, error: Exception) -> None:
    print("=" * 60, file=sys.stderr)
    print("GSC refresh token is no longer valid.", file=sys.stderr)
    print(f"Cause: {error}", file=sys.stderr)
    print("Likely reasons:", file=sys.stderr)
    print("  - 7 days passed (consent screen still in Testing mode — publish to Production)", file=sys.stderr)
    print("  - 6 months of inactivity", file=sys.stderr)
    print("  - You changed your Google password / revoked access", file=sys.stderr)
    print(f"Fix: delete {token_path} and re-run with --reauth", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    sys.exit(2)


def query_search_analytics(
    service,
    property_url: str,
    start: date,
    end: date,
    dimensions: list[str],
) -> list[dict]:
    """Pull all rows for a Search Analytics query, paginating until empty."""
    rows: list[dict] = []
    start_row = 0
    while True:
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": dimensions,
            "rowLimit": ROW_LIMIT,
            "startRow": start_row,
        }
        resp = service.searchanalytics().query(siteUrl=property_url, body=body).execute()
        page = resp.get("rows", [])
        rows.extend(page)
        if len(page) < ROW_LIMIT:
            break
        start_row += ROW_LIMIT
    return rows


def iso_week(date_str: str) -> str:
    d = date.fromisoformat(date_str)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def aggregate_weekly(
    rows: list[dict],
    keys: tuple[int, ...],
    date_index: int,
) -> dict[tuple, dict[str, dict]]:
    """Aggregate flat rows → {key_tuple: {week_id: {clicks, impressions, ...}}}.

    `keys` are positional indexes into row['keys'] for the grouping.
    `date_index` is the position of the date dimension in row['keys'].
    """
    out: dict[tuple, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {
        "clicks": 0,
        "impressions": 0,
        "_position_weighted": 0.0,  # for impressions-weighted avg position
    }))
    for row in rows:
        row_keys = row["keys"]
        date_str = row_keys[date_index]
        week_id = iso_week(date_str)
        group_key = tuple(row_keys[k] for k in keys)
        bucket = out[group_key][week_id]
        clicks = int(row.get("clicks") or 0)
        impressions = int(row.get("impressions") or 0)
        position = float(row.get("position") or 0)
        bucket["clicks"] += clicks
        bucket["impressions"] += impressions
        bucket["_position_weighted"] += position * impressions
    # Finalize: avg position, ctr.
    final: dict[tuple, dict[str, dict]] = {}
    for k, weeks in out.items():
        final[k] = {}
        for week_id, b in weeks.items():
            impr = b["impressions"]
            avg_pos = (b["_position_weighted"] / impr) if impr else 0
            final[k][week_id] = {
                "clicks": b["clicks"],
                "impressions": impr,
                "ctr": round(b["clicks"] / impr, 4) if impr else 0,
                "position": round(avg_pos, 2),
            }
    return final


def top_queries_per_page(rows: list[dict], top_n: int = 10) -> dict[str, list[dict]]:
    """Aggregate dim=['page', 'query'] rows → {page: [top_queries...]}."""
    by_page: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        page, query = row["keys"]
        by_page[page].append(
            {
                "query": query,
                "clicks": int(row.get("clicks") or 0),
                "impressions": int(row.get("impressions") or 0),
                "ctr": round(float(row.get("ctr") or 0), 4),
                "position": round(float(row.get("position") or 0), 2),
            }
        )
    for page in by_page:
        by_page[page].sort(key=lambda q: (q["clicks"], q["impressions"]), reverse=True)
        by_page[page] = by_page[page][:top_n]
    return dict(by_page)


def main() -> int:
    load_dotenv()
    args = parse_args()
    week = args.week or report_week_id()

    property_url = os.getenv("GSC_PROPERTY")
    client_json = os.getenv("GSC_OAUTH_CLIENT_JSON")
    token_json = os.getenv("GSC_OAUTH_TOKEN_JSON")
    if not property_url or not client_json or not token_json:
        print("error: GSC_PROPERTY, GSC_OAUTH_CLIENT_JSON, GSC_OAUTH_TOKEN_JSON required", file=sys.stderr)
        return 1

    creds = load_credentials(client_json, token_json, args.reauth)
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    end = date.today() - timedelta(days=2)  # GSC has ~2 day data lag
    start = end - timedelta(days=WINDOW_DAYS - 1)
    print(f"window: {start} → {end} ({WINDOW_DAYS} days)")

    print("query 1/3: dimensions=['date', 'page'] (per-page weekly)")
    rows_dp = query_search_analytics(service, property_url, start, end, ["date", "page"])
    by_page_weekly = aggregate_weekly(rows_dp, keys=(1,), date_index=0)
    print(f"  {len(rows_dp)} rows → {len(by_page_weekly)} pages")

    print("query 2/3: dimensions=['date', 'query'] (per-query weekly)")
    rows_dq = query_search_analytics(service, property_url, start, end, ["date", "query"])
    by_query_weekly = aggregate_weekly(rows_dq, keys=(1,), date_index=0)
    print(f"  {len(rows_dq)} rows → {len(by_query_weekly)} queries")

    print("query 3/3: dimensions=['page', 'query'] (top queries per page)")
    rows_pq = query_search_analytics(service, property_url, start, end, ["page", "query"])
    top_q_per_page = top_queries_per_page(rows_pq)
    print(f"  {len(rows_pq)} rows → top queries assembled for {len(top_q_per_page)} pages")

    # Reshape into the documented output schema.
    by_page: dict[str, dict] = {}
    for (page,), weeks in by_page_weekly.items():
        by_page[page] = {
            "weekly": [
                {"week": w, **vals}
                for w, vals in sorted(weeks.items())
            ],
            "top_queries": top_q_per_page.get(page, []),
        }

    by_query: dict[str, dict] = {}
    for (query,), weeks in by_query_weekly.items():
        by_query[query] = {
            "weekly": [
                {"week": w, **vals}
                for w, vals in sorted(weeks.items())
            ]
        }

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "property": property_url,
        "by_page": by_page,
        "by_query": by_query,
    }

    storage = get_storage()
    out_key = f"reports/{week}/search-console.json"
    storage.write_json(out_key, output)
    print(f"\nwrote {out_key}: {len(by_page)} page(s), {len(by_query)} query(ies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
