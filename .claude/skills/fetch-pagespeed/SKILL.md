---
name: fetch-pagespeed
description: Run Google PageSpeed Insights against every post URL plus the homepage, capturing Core Web Vitals (LCP, CLS, INP) and failing audit IDs for both mobile and desktop.
---

# fetch-pagespeed

Runs PSI per URL × strategy (mobile + desktop). Writes `reports/{week}/pagespeed.json`.

## How to invoke

```bash
python .claude/skills/fetch-pagespeed/scripts/fetch.py [--week 2026-W18]
```

## Inputs

- `PSI_API_KEY` — free, generous quota (25K queries/day)
- `posts.json`

## Output

`reports/{week}/pagespeed.json` keyed by URL, with `mobile` and `desktop` sub-objects each containing `performance`, `lcp`, `cls`, `inp`, and an `issues` list of failing audit IDs.

## Notes

- ~32 posts × 2 strategies = ~64 calls. Parallelize with concurrency limit of 5; expect 5–10 min total.
- Capture audit IDs (e.g. `unused-javascript`), not full audit text. The analyzer maps IDs to human-readable issues.
- Always run both mobile and desktop — Google ranks on mobile but desktop regressions still matter for UX.
