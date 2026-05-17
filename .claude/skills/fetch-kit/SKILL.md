---
name: fetch-kit
description: Pull primary-tag audience size, weekly new-signup history, and per-post attribution counts from Kit (ConvertKit) v4. CC4M stores subscribers by tag, not form, so this skill is tag-driven.
---

# fetch-kit

Hits the Kit v4 API. Writes `reports/{week}/kit.json`.

## How to invoke

```bash
python .claude/skills/fetch-kit/scripts/fetch.py [--week 2026-W18]
```

## Inputs

- `KIT_API_KEY` — Kit V4 API Key (sent as `X-Kit-Api-Key` header)
- `KIT_PRIMARY_TAG_ID` — the master CC4M tag (e.g. `14154457`); every signup gets this tag
- `posts.json` — used for per-post attribution against post-specific `kit_tag_id` values

## Output

```json
{
  "generated_at": "...",
  "week": "2026-W19",
  "subscribers": {
    "primary_tag_id": "14154457",
    "total": 247,
    "this_week_new": 12,
    "last_week_new": 9,
    "weekly_history": [
      { "week": "2026-W12", "new": 6 },
      ...
    ]
  },
  "by_post": [
    {
      "slug": "...",
      "kit_tag_id": "...",
      "total": 34,
      "this_week_new": 2
    }
  ],
  "signup_urls_this_week": {
    "subscribers_scanned": 12,
    "missing_signup_url": 1,
    "by_pathname": [
      { "pathname": "/some-post", "new": 7 },
      { "pathname": "/", "new": 4 }
    ]
  }
}
```

## Notes

- Uses `include_total_count=true` with `per_page=1` to get counts cheaply, no pagination walk.
- Weekly history fetched as 1 call per week × 8 weeks = 8 calls. Plus 1 for total + 1 per post with a custom tag. Stays well under any rate limit.
- `tagged_after` / `tagged_before` filter on when the tag was applied, which matches "new signups" semantics (vs. `created_at`, which is when the subscriber first existed in Kit — they may have been tagged later).
- Per-post attribution depends on posts setting `kitTagId` in frontmatter. Posts without it just don't appear in `by_post` — that's expected, not an error.
- `signup_urls_this_week` pages through all subscribers tagged in the week window and tallies the `SIGNUP_URL` custom field (set as page metadata at form-submit time). Pathnames are normalized (no host, query, fragment; trailing slash stripped) so they line up with Fathom's `by_page` keys for cross-checking. Subscribers without a `SIGNUP_URL` value land in `missing_signup_url`.
