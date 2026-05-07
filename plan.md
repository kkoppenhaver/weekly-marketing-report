# Weekly Site Intelligence Report — Build Plan

**Site:** claudecodeformarketers.com
**Trigger:** Sunday morning (Routines, weekly cron)
**Delivery:** Email (Resend) with link to a Google Doc
**Architecture:** 11 skills + R2 snapshot store + Routine

---

## 1. Architecture Overview

```
                   ┌─────────────────────────────────────────────────┐
                   │  Claude Code Routine — Sunday 6am                │
                   │  Invokes: compose-weekly-report                  │
                   └─────────────────────────────────────────────────┘
                                          │
            ┌─────────────────────────────┼─────────────────────────────┐
            │                             │                             │
            ▼                             ▼                             ▼
    ┌──────────────────┐      ┌──────────────────────┐     ┌──────────────────────┐
    │  FETCHERS (6)     │      │  ANALYZERS (3)        │     │  COMPOSERS (2)        │
    │  Pull raw data    │ ───▶ │  Read snapshots,      │ ──▶ │  Build doc + email    │
    │  Write to R2      │      │  produce insights     │     │                       │
    └──────────────────┘      └──────────────────────┘     └──────────────────────┘
            │                             │                             │
            ▼                             ▼                             ▼
    ┌─────────────────────────────────────────────────────────────────────────────┐
    │  R2 SNAPSHOT STORE                                                            │
    │  reports/2026-W18/posts.json                                                  │
    │  reports/2026-W18/search-console.json                                         │
    │  reports/2026-W18/fathom.json                                                 │
    │  reports/2026-W18/kit.json                                                    │
    │  reports/2026-W18/pagespeed.json                                              │
    │  reports/2026-W18/dataforseo.json                                             │
    │  reports/2026-W18/insights.json    ← analyzer output                          │
    │  reports/2026-W18/report.md        ← full report markdown                     │
    │  reports/2026-W18/summary.md       ← email body                               │
    │  reports/2026-W18/doc-url.txt      ← Google Doc URL                           │
    └─────────────────────────────────────────────────────────────────────────────┘
```

**Key architectural principles:**

- **Each fetcher is dumb.** Hits one API, normalizes the response, writes one JSON file. No analysis logic.
- **Each analyzer is pure.** Reads the current week's snapshots + the previous week's snapshots from R2. Writes one insights JSON. No API calls.
- **Composers orchestrate.** They invoke fetchers, then analyzers, then build the final outputs.
- **State lives in R2.** Skills are stateless and idempotent — re-running a skill overwrites that week's snapshot. Safe to retry.
- **Schemas documented in SKILL.md, not enforced.** Pragmatic middle ground (Option C from earlier).
- **Week IDs use ISO week format.** `2026-W18` for week 18 of 2026.

---

## 2. Skill Inventory

| # | Skill | Type | Reads | Writes |
|---|-------|------|-------|--------|
| 1 | `fetch-post-manifest` | Fetcher | GitHub repo (Astro content) | `posts.json` |
| 2 | `fetch-search-console` | Fetcher | GSC API + `posts.json` | `search-console.json` |
| 3 | `fetch-fathom` | Fetcher | Fathom API + `posts.json` | `fathom.json` |
| 4 | `fetch-kit` | Fetcher | Kit API | `kit.json` |
| 5 | `fetch-pagespeed` | Fetcher | PSI API + `posts.json` | `pagespeed.json` |
| 6 | `fetch-dataforseo` | Fetcher | DataForSEO API + GSC discovered keywords | `dataforseo.json` |
| 7 | `analyze-seo-health` | Analyzer | All fetcher outputs | `insights.seo_health` |
| 8 | `analyze-content-performance` | Analyzer | GSC + Fathom + Kit, current + prior week | `insights.content_perf` |
| 9 | `analyze-keyword-opportunities` | Analyzer | GSC + DataForSEO + posts | `insights.opportunities` |
| 10 | `compose-weekly-report` | Composer | All insights → Google Drive | `report.md`, `doc-url.txt` |
| 11 | `compose-email-summary` | Composer | Insights + doc URL → Resend | `summary.md` (sends email) |

---

## 3. Skill Specs

### 3.1 `fetch-post-manifest`

**Purpose:** Read the canonical list of posts from the GitHub repo. This is the source of truth every other skill joins against.

**Inputs:**
- GitHub repo URL (env: `SITE_REPO`)
- GitHub token (env: `GITHUB_TOKEN`)
- Path to content collection in repo (env: `CONTENT_PATH`, default `src/content/posts`)

**Output schema (`posts.json`):**
```json
{
  "generated_at": "2026-05-04T06:00:00Z",
  "week": "2026-W18",
  "post_count": 32,
  "posts": [
    {
      "slug": "claude-code-for-content-marketers",
      "url": "https://claudecodeformarketers.com/posts/claude-code-for-content-marketers",
      "title": "Claude Code for Content Marketers",
      "description": "...",
      "published_at": "2026-02-14",
      "last_updated": "2026-04-22",
      "target_keyword": "claude code for marketers",
      "secondary_keywords": ["claude code marketing"],
      "category": "guide",
      "frontmatter_complete": true,
      "missing_fields": []
    }
  ],
  "manifest_issues": [
    {
      "slug": "old-post",
      "issue": "missing target_keyword",
      "severity": "warn"
    }
  ]
}
```

**Implementation notes:**
- Use GitHub Contents API or clone the repo into the Routine's workspace.
- Parse frontmatter from each `.md` file using `gray-matter` or equivalent.
- Flag posts missing required frontmatter (especially `target_keyword`) into `manifest_issues`. Don't fail the skill — just report.
- This skill runs first; everything depends on it.

**Complexity:** Small. ~60 lines of code.

---

### 3.2 `fetch-search-console`

**Purpose:** Pull GSC performance data per page over a 90-day rolling window so analyzers can detect trends.

**Inputs:**
- Service account credentials for GSC API
- Property URL (env: `GSC_PROPERTY`)
- `posts.json` from R2

**Output schema (`search-console.json`):**
```json
{
  "generated_at": "2026-05-04T06:00:00Z",
  "week": "2026-W18",
  "window": { "start": "2026-02-03", "end": "2026-05-03" },
  "by_page": {
    "https://claudecodeformarketers.com/posts/...": {
      "weekly": [
        { "week": "2026-W14", "clicks": 12, "impressions": 340, "ctr": 0.035, "position": 14.2 },
        { "week": "2026-W15", "clicks": 18, "impressions": 410, "ctr": 0.044, "position": 12.8 }
      ],
      "top_queries": [
        { "query": "claude code for marketers", "clicks": 8, "impressions": 120, "ctr": 0.067, "position": 9.4 }
      ]
    }
  },
  "by_query": {
    "claude code automation": {
      "weekly": [...],
      "top_pages": [...]
    }
  },
  "indexation": {
    "https://claudecodeformarketers.com/posts/...": {
      "indexed": true,
      "last_crawled": "2026-04-29",
      "verdict": "PASS",
      "mobile_usable": true
    }
  }
}
```

**Implementation notes:**
- Use the GSC Search Analytics API for per-page/query/date data.
- Use the URL Inspection API for indexation status (rate limited to ~2000/day, plenty for 32 posts).
- Aggregate by ISO week so analyzers can do clean WoW math.
- Only include URLs that are in `posts.json` (filter out home/about/etc. for the per-page data; include them in indexation).

**Complexity:** Medium. GSC API has quirks (date dimensions, batching), but well-documented.

**Secrets needed:** Google service account JSON (vault).

---

### 3.3 `fetch-fathom`

**Purpose:** Pull pageviews, referrers, top content, events.

**Inputs:**
- Fathom API key (env: `FATHOM_API_KEY`)
- Site ID (env: `FATHOM_SITE_ID`)
- `posts.json`

**Output schema (`fathom.json`):**
```json
{
  "generated_at": "2026-05-04T06:00:00Z",
  "window": { "start": "2026-02-03", "end": "2026-05-03" },
  "by_page": {
    "/posts/claude-code-for-content-marketers": {
      "weekly": [
        { "week": "2026-W18", "pageviews": 240, "uniques": 180, "avg_time": 145 }
      ]
    }
  },
  "top_referrers_this_week": [
    { "source": "twitter.com", "pageviews": 88 },
    { "source": "google.com", "pageviews": 412 }
  ],
  "events": {
    "email_signup": { "this_week": 12, "last_week": 8 }
  }
}
```

**Implementation notes:**
- Fathom's API is REST + simple. Use the aggregation endpoints with `date_grouping=week` if available, otherwise pull daily and aggregate.
- If you have Fathom event tracking on the email signup form, capture it here. Cross-reference against Kit's data later.

**Complexity:** Small.

**Secrets needed:** Fathom API key.

---

### 3.4 `fetch-kit`

**Purpose:** Pull email signup data.

**Inputs:**
- Kit API key (env: `KIT_API_KEY`)
- Form/sequence IDs of interest (env: `KIT_FORM_IDS` — comma-separated)

**Output schema (`kit.json`):**
```json
{
  "generated_at": "2026-05-04T06:00:00Z",
  "window": { "start": "2026-02-03", "end": "2026-05-03" },
  "subscribers": {
    "total": 847,
    "active": 812,
    "this_week_new": 23,
    "last_week_new": 19,
    "weekly_history": [
      { "week": "2026-W14", "new": 14, "unsubscribed": 2 }
    ]
  },
  "by_form": {
    "form_id_1": {
      "name": "7-Part Email Course",
      "this_week_new": 18,
      "last_week_new": 15
    }
  },
  "course_progress": {
    "7-part-email-course": {
      "active_in_sequence": 142,
      "completion_rate": 0.34
    }
  }
}
```

**Implementation notes:**
- Kit API v4. Pull subscriber data filtered by `created_at` to get this-week vs. last-week splits.
- If forms have referrer/source tracking enabled, capture it. Later, the analyzer can join "this post drove N signups."
- Course progress is a v2 nice-to-have; skip if it complicates v1.

**Complexity:** Small to medium (Kit's API has pagination quirks).

**Secrets needed:** Kit API key.

---

### 3.5 `fetch-pagespeed`

**Purpose:** Run PageSpeed Insights against every post + the homepage.

**Inputs:**
- PSI API key (env: `PSI_API_KEY` — free, generous quota)
- `posts.json`

**Output schema (`pagespeed.json`):**
```json
{
  "generated_at": "2026-05-04T06:00:00Z",
  "by_url": {
    "https://claudecodeformarketers.com/": {
      "mobile": {
        "performance": 0.87,
        "lcp": 2.1,
        "cls": 0.04,
        "inp": 180,
        "issues": ["unused-javascript", "render-blocking-resources"]
      },
      "desktop": { "performance": 0.96, "lcp": 1.2, "cls": 0.02, "inp": 90, "issues": [] }
    }
  }
}
```

**Implementation notes:**
- PSI API rate limit is 25K queries/day, way more than needed.
- Run mobile + desktop per URL. Time-budget it: with 32 posts, ~64 calls, maybe 5-10 minutes total. Parallelize with concurrency limit of 5.
- Capture the audit IDs of failing audits, not the full audit text. The analyzer can map audit IDs to human-readable issues.

**Complexity:** Small.

**Secrets needed:** PSI API key.

---

### 3.6 `fetch-dataforseo`

**Purpose:** Get keyword volume, difficulty, SERP composition for tracked + discovered keywords.

**Inputs:**
- DataForSEO credentials (env: `DFS_LOGIN`, `DFS_PASSWORD`)
- Tracked keywords from `posts.json` (target + secondary)
- Discovered keywords from `search-console.json` (anything with ≥20 impressions where you rank 1-30)

**Output schema (`dataforseo.json`):**
```json
{
  "generated_at": "2026-05-04T06:00:00Z",
  "keywords": {
    "claude code for marketers": {
      "search_volume": 480,
      "monthly_history": [...],
      "keyword_difficulty": 28,
      "cpc": 4.20,
      "intent": "informational",
      "serp": {
        "your_position": 7,
        "top_10_domains": ["...", "..."],
        "features": ["people_also_ask", "video"]
      },
      "related_keywords": [
        { "keyword": "claude code marketing automation", "volume": 90, "difficulty": 22 }
      ]
    }
  }
}
```

**Implementation notes:**
- DataForSEO charges per call. Be careful: don't send the same keyword twice in a week. Cache aggressively.
- Use the **Keyword Data API** for volume/difficulty and the **SERP API** sparingly (only for top opportunity candidates, maybe 20 keywords/week max).
- Estimated weekly cost: ~$1-3 if scoped properly.
- This is the most cost-sensitive skill. Add a hard cap on number of API calls per run as a config.

**Complexity:** Medium. Cost discipline matters.

**Secrets needed:** DataForSEO login + password (vault).

---

### 3.7 `analyze-seo-health`

**Purpose:** Surface technical, indexation, and on-page issues as a prioritized fix list.

**Inputs:** All fetcher outputs from current week.

**Output schema (`insights.seo_health`):**
```json
{
  "summary": {
    "critical": 2,
    "warning": 7,
    "info": 14
  },
  "issues": [
    {
      "severity": "critical",
      "category": "indexation",
      "page": "/posts/old-guide",
      "issue": "Not indexed since 2026-03-15",
      "evidence": { "last_crawled": "2026-03-15", "verdict": "FAIL" },
      "recommended_action": "Submit for re-indexing; investigate why dropped",
      "effort": "S",
      "impact": "M"
    },
    {
      "severity": "warning",
      "category": "core_web_vitals",
      "page": "/posts/heavy-post",
      "issue": "Mobile LCP regressed from 2.4s to 3.9s",
      "evidence": {...},
      "recommended_action": "...",
      "effort": "M",
      "impact": "M"
    }
  ]
}
```

**Issue categories the analyzer covers:**
- **Indexation:** posts not indexed, posts with FAIL verdict, posts not crawled in >30 days
- **Technical:** Core Web Vitals failures or regressions, mobile usability issues
- **On-page from frontmatter:** missing target_keyword, missing description, title length issues
- **Content gaps:** posts ranking 1-30 for a keyword they don't declare (frontmatter mismatch)
- **Internal:** posts with no inbound internal links (orphans) — requires a small crawl, see open question

**Implementation notes:**
- Each issue includes effort (S/M/L) and impact (S/M/L) for triage.
- Sort by `severity` then `impact`.
- Cap at top 25 issues in the output to keep the report digestible. Full list available in the JSON.

**Complexity:** Medium. The hard part is good prioritization heuristics, not the data plumbing.

---

### 3.8 `analyze-content-performance`

**Purpose:** What's working, what's slipping, what's surprising.

**Inputs:** GSC + Fathom + Kit, current week + previous week from R2.

**Output schema (`insights.content_perf`):**
```json
{
  "headline": {
    "clicks_wow_pct": 0.18,
    "signups_wow_pct": 0.21,
    "top_movers_count": 3
  },
  "winners_this_week": [
    {
      "page": "/posts/claude-code-routines",
      "clicks": 142,
      "wow_pct": 0.65,
      "why_interesting": "Up 65% WoW; ranking position improved 4.2 → 2.8 on 'claude code routines'"
    }
  ],
  "decaying_posts": [
    {
      "page": "/posts/older-guide",
      "clicks_60d_ago": 80,
      "clicks_this_week": 22,
      "trend": "declining 4 weeks in a row",
      "diagnosis": "Position dropped from 5.2 to 11.8; competitors adding fresher content?"
    }
  ],
  "striking_distance_keywords": [
    {
      "keyword": "claude code automation",
      "page": "/posts/...",
      "current_position": 11.4,
      "impressions": 340,
      "potential_clicks_at_p3": 120,
      "recommended_action": "Refresh post; add internal links from 3 high-authority pages"
    }
  ],
  "surprise_winners": [
    {
      "page": "/posts/x",
      "keyword": "ai for content calendar",
      "note": "Ranking #4 for keyword not declared as target. Worth a dedicated post?"
    }
  ],
  "signup_attribution": [
    { "page": "/posts/x", "signups_this_week": 8 }
  ]
}
```

**Implementation notes:**
- Striking distance: position 8-20, impressions ≥50/week. Sort by `impressions × (1 / current_position)`.
- Decay: clicks down ≥30% vs. 4-week average AND position worse by ≥2 vs. 4 weeks ago.
- WoW math should be clipped (e.g., a post going from 1 → 5 clicks isn't "+400%", flag as low-volume).

**Complexity:** Medium. Good heuristics matter.

---

### 3.9 `analyze-keyword-opportunities`

**Purpose:** What posts should I add to the backlog this week?

**Inputs:** GSC + DataForSEO + posts manifest.

**Output schema (`insights.opportunities`):**
```json
{
  "new_post_suggestions": [
    {
      "primary_keyword": "claude code for email marketers",
      "search_volume": 320,
      "difficulty": 18,
      "rationale": "Adjacent to your top-performing 'claude code for content marketers' post; you already rank #14 for it via that post — a dedicated post likely captures the click",
      "suggested_angle": "Practical workflows: list pruning, subject line testing, segmentation prompts",
      "related_keywords_to_cover": ["...", "..."],
      "internal_links_from": ["/posts/claude-code-for-content-marketers", "/posts/..."],
      "estimated_traffic": "30-80 monthly clicks within 90 days"
    }
  ],
  "post_refresh_suggestions": [
    {
      "page": "/posts/older-guide",
      "rationale": "Decaying + striking distance on 'X'",
      "recommended_changes": ["Update screenshots", "Add section on Y", "Strengthen internal linking"]
    }
  ]
}
```

**Implementation notes:**
- Cap at 3 new post suggestions and 3 refreshes per week. More than that becomes noise.
- The `rationale` field is critical — without it the suggestions feel like spam. Use Claude (the agent itself) to write these as part of the skill.

**Complexity:** Medium. This is where the analysis layer earns its keep.

---

### 3.10 `compose-weekly-report`

**Purpose:** Orchestrate fetchers and analyzers; produce a Google Doc.

**Flow:**
1. Determine current ISO week.
2. Run fetchers in dependency order:
   - `fetch-post-manifest` (sync, blocking)
   - `fetch-search-console`, `fetch-fathom`, `fetch-kit`, `fetch-pagespeed` in parallel
   - `fetch-dataforseo` (depends on GSC for discovered keywords)
3. Run analyzers in parallel:
   - `analyze-seo-health`, `analyze-content-performance`, `analyze-keyword-opportunities`
4. Assemble final markdown report (`report.md`).
5. Create a Google Doc in a configured folder with that content. Save the doc URL.
6. Write all artifacts to R2 under `reports/{week}/`.

**Report structure (markdown):**
```
# Weekly Site Report — Week of {date}

## TL;DR
- Clicks: {n} ({wow}% WoW)
- New signups: {n} ({wow}% WoW)
- {n} critical issues to address
- {n} content opportunities

## This Week's Focus
[Top 3-5 actionable items, ranked]

## SEO Health
[Critical and warning issues, by category]

## Content Performance
[Winners, decay, striking distance, surprises]

## Content Backlog Suggestions
[3 new posts, 3 refreshes, with rationale]

## Appendix: Numbers
[Tables of WoW data]

## Appendix: All Issues
[Full issue list, not just top 25]
```

**Complexity:** Large by line count, low by difficulty — mostly templating.

**Secrets needed:** Google Drive credentials (you already have the connector).

---

### 3.11 `compose-email-summary`

**Purpose:** The thing you'll actually read.

**Inputs:** All insights JSON + the doc URL.

**Email body structure:**
```
Subject: Site report — Week 18 — Up 18% WoW, 3 things to ship

Hi Keanan,

The headline:
• Clicks up 18% WoW (mainly from /posts/claude-code-routines climbing)
• 23 new signups (up 21%) — best week in 6
• 1 critical issue: /posts/old-guide dropped from index

This week, ship these three:
1. Refresh /posts/older-guide — striking distance on "claude code automation" (potential +120 clicks at position 3)
2. Investigate why /posts/old-guide dropped from index
3. New post: "Claude Code for Email Marketers" — vol 320, KD 18, you already rank #14 via adjacent post

Full report: {google_doc_url}

— Sunday digest
```

**Implementation notes:**
- This is the only thing you'll definitely read. Treat the writing as a craft step.
- Use Claude to actually write it from the insights JSON, with a tight system prompt enforcing brevity.
- Send via Resend.

**Complexity:** Small. The hard part is the prompt that produces good copy.

**Secrets needed:** Resend API key.

---

## 4. Shared Infrastructure

### 4.1 R2 snapshot store

**Bucket:** `claudecodeformarketers-reports` (private)

**Layout:**
```
reports/
  2026-W18/
    posts.json
    search-console.json
    fathom.json
    kit.json
    pagespeed.json
    dataforseo.json
    insights.json     # combined output of all 3 analyzers
    report.md
    summary.md
    doc-url.txt
  2026-W17/
    ...
```

**Access:** S3-compatible API. Skills use AWS SDK pointed at R2 endpoint.

**Lifecycle:** No deletion needed at this volume (each week is a few MB; 5 years = ~1GB). Can add lifecycle rule later.

### 4.2 Secrets inventory (Routines vault)

| Secret | Used by | Source |
|--------|---------|--------|
| `GITHUB_TOKEN` | `fetch-post-manifest` | GitHub PAT, repo:read scope |
| `GSC_SERVICE_ACCOUNT` | `fetch-search-console` | Google Cloud service account JSON |
| `FATHOM_API_KEY` | `fetch-fathom` | Fathom dashboard |
| `KIT_API_KEY` | `fetch-kit` | Kit dashboard |
| `PSI_API_KEY` | `fetch-pagespeed` | Google Cloud Console |
| `DFS_LOGIN`, `DFS_PASSWORD` | `fetch-dataforseo` | DataForSEO dashboard |
| `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT`, `R2_BUCKET` | All skills | Cloudflare R2 |
| `GOOGLE_DRIVE_FOLDER_ID` | `compose-weekly-report` | Drive folder where reports live |
| `RESEND_API_KEY`, `REPORT_TO_EMAIL`, `REPORT_FROM_EMAIL` | `compose-email-summary` | Resend |

### 4.3 Configuration constants

A small `config.json` shipped with the orchestrator skill:
```json
{
  "site_url": "https://claudecodeformarketers.com",
  "site_repo": "kheafield/claudecodeformarketers",
  "content_path": "src/content/posts",
  "striking_distance_min_position": 8,
  "striking_distance_max_position": 20,
  "striking_distance_min_impressions": 50,
  "decay_threshold_pct": 0.30,
  "max_dataforseo_keywords_per_run": 30,
  "max_issues_in_report": 25,
  "max_new_post_suggestions": 3,
  "max_refresh_suggestions": 3
}
```

---

## 5. Build Order

### Phase 1 — Foundation (Week 1)
1. **R2 setup.** Create bucket, generate credentials, test write/read with a CLI script.
2. **Secrets vault.** Provision all keys. Test fetching one secret from inside Claude Code.
3. **Frontmatter audit (one-time).** Use Claude Code interactively against the repo to audit all 32 posts for `target_keyword`, `description`, `published_at`, `last_updated`. Fix any missing fields. Standardize the schema.
4. **Skill scaffolding.** Create the 11 SKILL.md files with YAML frontmatter and stub content. Document each skill's input/output schema with a sample JSON.

### Phase 2 — Fetchers (Week 1-2)
Build in this order:
5. `fetch-post-manifest` — easiest, unblocks everything
6. `fetch-search-console` — the workhorse, most data
7. `fetch-fathom` — small, quick win
8. `fetch-kit` — small
9. `fetch-pagespeed` — small
10. `fetch-dataforseo` — last (cost-sensitive, design carefully)

After each fetcher: run it standalone, verify the output matches the documented schema, write a sample to R2.

### Phase 3 — Analyzers (Week 2-3)
11. `analyze-seo-health` — most mechanical
12. `analyze-content-performance` — needs WoW logic; requires at least 2 weeks of data
13. `analyze-keyword-opportunities` — most judgment-heavy

Run each analyzer against the live R2 data once both weeks of fetcher snapshots exist.

### Phase 4 — Composers (Week 3)
14. `compose-weekly-report` — orchestrator + Google Doc
15. `compose-email-summary` — final piece

### Phase 5 — Routine + first live run (Week 3)
16. Wire up the Routine in Claude Code: weekly cron, Sunday 6am, calls `compose-weekly-report`.
17. Manual run first. Inspect the doc and email. Fix issues.
18. Schedule. Watch the next 2-3 weekly runs closely.

---

## 6. Open Questions & Deferred Decisions

These are intentionally not blocking the build — flag during implementation:

1. **Internal link analysis.** Detecting orphan posts requires either (a) a small crawler in `analyze-seo-health` that walks the rendered site, or (b) parsing the markdown source for inter-post links. (b) is easier, (a) is more accurate. Defer to implementation.

2. **Fathom signup attribution.** If Fathom is tracking the signup event with a `path` parameter, signup-by-page attribution comes for free. If not, we'll need UTM-tagged links from each post or a Kit form per post. Check Fathom config first.

3. **Course progress in Kit.** If the 7-part email course completion data is hard to pull, defer to v2.

4. **Failure handling in the Routine.** What if PSI is down? Two options: skip that section in the report and note it, or retry once and fail the run. Recommend: graceful degradation — the report should always send, with missing sections marked as "data unavailable this week."

5. **Historical backfill.** WoW comparisons need at least 2 weeks of data. The first run will lack last-week's snapshots. Two options: (a) accept that week 1 has no WoW, (b) backfill 4 weeks of GSC/Fathom/Kit data on first run by writing fake "last week" snapshots from those APIs' historical data. Recommend (a) — simpler, and you only wait a week.

6. **Email design.** Plain text vs. minimal HTML. Plain text reads cleanly on every client. Recommend plain text for v1.

---

## 7. One-Time Setup Checklist

Before any skill development:

- [ ] Create R2 bucket and credentials
- [ ] Provision all API keys/credentials in Routines vault
- [ ] Create dedicated Google Drive folder for reports; note the folder ID
- [ ] Set up Resend account, verify sending domain, get API key
- [ ] Audit existing 32 posts for required frontmatter; fix gaps
- [ ] Confirm Fathom event tracking on email signup form (or set it up)
- [ ] Create GitHub PAT scoped to the repo

---

## 8. What Could Go Wrong (and what to watch)

- **API drift:** any of the 6 external APIs could change schema. Loose contracts (Option C) means analyzers should defensively handle missing fields. Log warnings, don't crash.
- **DataForSEO costs:** if the keyword set grows unbounded, costs creep. Hard-capped at `max_dataforseo_keywords_per_run` in config.
- **Routine timeout:** if the full pipeline exceeds Routines' execution window, split into two routines (one for fetchers running early Sunday, one for analysis + compose running an hour later, with the second one assuming the first succeeded).
- **R2 partial writes:** if a fetcher fails mid-week, the analyzer might read a stale or missing file. Each fetcher should write to a temp key first then rename atomically, or include a `status: "complete"` field that analyzers check.
- **Doc clutter in Drive:** consider a yearly subfolder (`2026/`) inside the reports folder.

---

## 9. After v1 — likely v2 candidates

- Slack alert for critical issues mid-week (not just Sunday)
- "Did the fixes I made last week move the needle?" — close-the-loop tracking
- Auto-create GitHub issues for top fixes (with the report agent following up)
- Multi-site support (when FloorboardAI.com gets serious)
- A dashboard view of historical reports at a private route on the site
- Per-post "scorecard" — if you visit a post URL with a query param, the agent surfaces its current data

---

*Document version: v1, drafted {date}. Intended to be edited as you build.*
