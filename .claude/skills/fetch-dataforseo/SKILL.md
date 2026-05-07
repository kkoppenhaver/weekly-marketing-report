---
name: fetch-dataforseo
description: Get keyword volume, difficulty, and SERP composition from DataForSEO for the union of tracked keywords (from post frontmatter) and discovered keywords (from GSC, ranking 1-30 with meaningful impressions). Cost-disciplined.
---

# fetch-dataforseo

Hits the DataForSEO Keyword Data + SERP APIs. Writes `reports/{week}/dataforseo.json`.

## How to invoke

```bash
python .claude/skills/fetch-dataforseo/scripts/fetch.py [--week 2026-W18]
```

Requires `posts.json` and `search-console.json` for the week.

## Inputs

- `DFS_LOGIN`, `DFS_PASSWORD`
- `posts.json` (tracked: post `target_keyword` + `secondary_keywords`)
- `search-console.json` (discovered: queries with ≥20 impressions where you rank 1-30)

## Output

`reports/{week}/dataforseo.json` — keyword → `{ search_volume, keyword_difficulty, cpc, intent, monthly_history, serp: { your_position, top_10_domains, features }, related_keywords }`.

## Notes

- **Cost discipline matters.** Hard cap at `max_dataforseo_keywords_per_run` (default 30). Estimated ~$1–3/week at that cap.
- Use Keyword Data API for volume/difficulty (cheap). Use SERP API only for the top opportunity candidates (~20).
- Cache the same keyword across weeks — if a keyword was queried <14 days ago, reuse the result. Volume changes slowly.
- This is the most cost-sensitive skill. Log every API call and total cost per run.
