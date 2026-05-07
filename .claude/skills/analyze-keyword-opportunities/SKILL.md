---
name: analyze-keyword-opportunities
description: Recommend new posts to add to the content backlog and existing posts to refresh, based on GSC + DataForSEO + posts manifest. Caps suggestions to keep signal high.
---

# analyze-keyword-opportunities

Reads `posts.json`, `search-console.json`, `dataforseo.json`. Emits `insights.opportunities`.

## How to invoke

```bash
python .claude/skills/analyze-keyword-opportunities/scripts/analyze.py [--week 2026-W18]
```

Hybrid skill: Python identifies and scores candidate keywords; Claude writes the `rationale`, `suggested_angle`, and `recommended_changes` prose for the top picks.

## Output

```json
{
  "new_post_suggestions": [ /* up to 3 */ ],
  "post_refresh_suggestions": [ /* up to 3 */ ]
}
```

Schema per `plan.md §3.9`. Both arrays include a `rationale` field — the prose is the whole point. Without strong rationale, suggestions feel like spam.

## Scoring

- New post candidates: discovered keywords (impressions ≥100 over 90 days, your_position 11–40) where no existing post declares this keyword as primary or secondary. Score by `volume × (1 / difficulty) × (1 / position)`.
- Refresh candidates: existing posts with declining position trend AND a striking-distance keyword they could plausibly capture with updates.

## Notes

- 3 + 3 cap is intentional. More than that becomes noise and you stop reading.
- Internal linking suggestions for new posts come from `posts.json` — surface 2-3 high-authority existing posts that semantically relate to the new keyword.
