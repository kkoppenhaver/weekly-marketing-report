---
name: fetch-fathom
description: Pull 90-day pageviews, uniques, top referrers, and tracked events from Fathom Analytics, aggregated by ISO week and by page path.
---

# fetch-fathom

Hits the Fathom Analytics API for traffic data and tracked events. Writes `reports/{week}/fathom.json`.

## How to invoke

```bash
python .claude/skills/fetch-fathom/scripts/fetch.py [--week 2026-W18]
```

## Inputs

- `FATHOM_API_KEY`
- `FATHOM_SITE_ID`
- `posts.json` (used to filter `by_page` to known post URLs)

## Output

`reports/{week}/fathom.json` — see `plan.md §3.3`. Sections:

- `by_page` — weekly pageviews / uniques / avg_time per post path
- `top_referrers_this_week` — current week only
- `events` — counts per tracked event name (e.g. `email_signup`), this_week vs. last_week. Each event also includes `this_week_by_pathname`: a list of `{pathname, conversions}` showing which pages the event fired on this week (sorted desc, pathnames normalized — no trailing slash, root stays `/`).

## Notes

- Fathom does not expose user-level cohorts. Don't try to compute funnels here.
- Use the aggregations endpoint with `date_grouping=week` if available; otherwise pull daily and aggregate.
- Email signup attribution by page comes from `aggregations` with `entity=event` + `field_grouping=pathname`. Pathnames here can be cross-referenced against `kit.json → signup_urls_this_week.by_pathname` (same normalization) to compare Fathom's where-event-fired vs. Kit's SIGNUP_URL custom field.
