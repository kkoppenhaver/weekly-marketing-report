---
name: fetch-search-console
description: Pull 90-day rolling per-page Google Search Console data (clicks, impressions, CTR, position, top queries) plus URL Inspection indexation status. Aggregates by ISO week so analyzers can do clean WoW math.
---

# fetch-search-console

Hits the GSC Search Analytics API for per-page and per-query data over a rolling 90-day window, then the URL Inspection API for indexation status of every post in `posts.json`. Writes `reports/{week}/search-console.json`.

## How to invoke

```bash
python .claude/skills/fetch-search-console/scripts/fetch.py [--week 2026-W18]
```

Requires `posts.json` for the week to already exist.

## Inputs

- `GSC_PROPERTY` — verified property URL (e.g. `https://claudecodeformarketers.com/`)
- `GSC_SERVICE_ACCOUNT_JSON` — JSON credentials with `https://www.googleapis.com/auth/webmasters.readonly` scope. The service account email must be added as a user on the GSC property.
- `posts.json` from the snapshot store

## Output

`reports/{week}/search-console.json` — see `plan.md §3.2` for the full schema. Three top-level sections:

- `by_page` — weekly history + top queries per post URL
- `by_query` — for queries that meaningfully appear in the data, weekly history + top pages
- `indexation` — URL Inspection results per post (indexed?, last_crawled, verdict, mobile_usable)

## Notes

- URL Inspection API is rate-limited to ~2000/day. Pace at ~1/sec to stay safe.
- Aggregate daily GSC data into ISO weeks in this fetcher — analyzers should not redo that math.
- Filter `by_page` to URLs in `posts.json`; include the homepage and any non-post pages in `indexation` so the SEO health analyzer can flag site-wide issues.
- Weekly aggregation: a "week" is Monday–Sunday in the site's primary timezone (UTC for now).
