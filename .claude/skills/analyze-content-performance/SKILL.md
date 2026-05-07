---
name: analyze-content-performance
description: Detect winners, decaying posts, striking-distance keywords, and surprise wins. Computes week-over-week deltas from GSC + Fathom + Kit snapshots across the current and previous weeks.
---

# analyze-content-performance

Reads the current and previous week's `search-console.json`, `fathom.json`, `kit.json`, and `posts.json`. Emits `insights.content_perf`.

## How to invoke

```bash
python .claude/skills/analyze-content-performance/scripts/analyze.py [--week 2026-W18]
```

Requires at least two consecutive weeks of fetcher data for WoW analysis. First-run output is partial — note this in the report.

## Output

See `plan.md §3.8` for the full schema. Sections:

- `headline` — top-line WoW deltas (clicks, signups, mover count)
- `winners_this_week` — top movers up
- `decaying_posts` — posts trending down 4+ weeks
- `striking_distance_keywords` — keywords ranking position 8–20 with ≥50 weekly impressions
- `surprise_winners` — posts ranking well for keywords they didn't declare
- `signup_attribution` — signups by source page (when Fathom/Kit data permits)

## Heuristics

- Striking distance: position 8–20, impressions ≥50/week. Sort by `impressions × (1 / position)`.
- Decay: clicks down ≥30% vs. 4-week trailing average **and** position worse by ≥2 vs. 4 weeks ago.
- WoW math is clipped: a post going from 1 → 5 clicks isn't reported as "+400%". Floor at 10 weekly clicks before computing percentage deltas.
