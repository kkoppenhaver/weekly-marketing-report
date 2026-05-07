---
name: analyze-seo-health
description: Surface technical, indexation, and on-page SEO issues as a prioritized fix list. Reads all fetcher snapshots; produces a sorted list of issues with severity, effort estimate, and recommended action.
---

# analyze-seo-health

Reads the current week's fetcher snapshots and emits `insights.seo_health` (written into `reports/{week}/insights.json` under that key by the orchestrator, or stored alongside if run standalone).

## How to invoke

```bash
python .claude/skills/analyze-seo-health/scripts/analyze.py [--week 2026-W18]
```

This skill is hybrid: deterministic Python computes the issue list, and Claude (via the SKILL invocation) writes the prose `recommended_action` for the top 5–10 issues where context-aware judgment is worth the tokens.

## Issue categories

- **Indexation:** posts not indexed, FAIL verdict, not crawled in >30 days
- **Technical (CWV):** LCP/CLS/INP failures or week-over-week regressions
- **On-page:** missing target_keyword, missing description, title length out of bounds (<30 or >65 chars)
- **Content gaps:** posts ranking 1-30 for a keyword they don't declare in frontmatter
- **Internal links:** orphan posts (no internal inbound links). See plan §6.1 — initial impl parses markdown source for links; v2 may crawl rendered site.

## Output

```json
{
  "summary": { "critical": 2, "warning": 7, "info": 14 },
  "issues": [
    {
      "severity": "critical|warning|info",
      "category": "indexation|cwv|on_page|content_gap|internal_links",
      "page": "/blog/...",
      "issue": "Short summary",
      "evidence": { ... },
      "recommended_action": "...",
      "effort": "S|M|L",
      "impact": "S|M|L"
    }
  ]
}
```

Cap at top 25 issues in the report; full list available in the JSON.

## Notes

- Prioritization heuristic: severity desc, then impact desc, then effort asc.
- Don't crash on missing fetcher data — degrade gracefully and emit an `info`-level issue noting the gap.
