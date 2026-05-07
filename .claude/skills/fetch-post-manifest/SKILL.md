---
name: fetch-post-manifest
description: Read the canonical list of posts from the claudecodeformarketers.com repo, normalize frontmatter, flag missing fields. Source of truth every other skill joins against. Run first in the weekly pipeline.
---

# fetch-post-manifest

Reads the Astro content collection at `src/content/blog/` in the site repo, parses frontmatter from every `.md` / `.mdx` file, and writes a normalized `posts.json` to the snapshot store at `reports/{week}/posts.json`.

## When to invoke

Run this skill first in the weekly pipeline. Every other fetcher and analyzer joins against `posts.json` by `slug` or `url`.

## How to invoke

```bash
python .claude/skills/fetch-post-manifest/scripts/fetch.py [--week 2026-W18]
```

Defaults to the current ISO week if `--week` is omitted.

## Inputs

- `SITE_URL` — base URL of the site (e.g. `https://claudecodeformarketers.com`)
- `SITE_REPO_LOCAL` — local checkout path of the site repo (used in dev)
- `SITE_REPO` + `GITHUB_TOKEN` — used in the Routine to clone fresh
- `CONTENT_PATH` — relative path to the content collection (default `src/content/blog`)

## Output

Writes `reports/{week}/posts.json` with the schema below. Posts missing `target_keyword` or `description` are flagged in `manifest_issues` but not excluded — analyzers decide what to do with them.

```json
{
  "generated_at": "2026-05-04T06:00:00Z",
  "week": "2026-W18",
  "post_count": 27,
  "posts": [
    {
      "slug": "what-is-vibe-marketing",
      "url": "https://claudecodeformarketers.com/blog/what-is-vibe-marketing",
      "title": "...",
      "description": "...",
      "pub_date": "2026-04-21",
      "updated_date": null,
      "target_keyword": null,
      "secondary_keywords": [],
      "categories": ["Concepts"],
      "tags": ["workflows", "prompting"],
      "draft": false,
      "frontmatter_complete": false,
      "missing_fields": ["target_keyword"]
    }
  ],
  "manifest_issues": [
    { "slug": "what-is-vibe-marketing", "issue": "missing target_keyword", "severity": "warn" }
  ]
}
```

## Notes

- Drafts are included in the output but flagged via the `draft` field. Downstream skills filter them out as needed.
- Slug is the filename without extension. URL assumes the Astro route `/blog/{slug}` — adjust if the site changes.
- `target_keyword` and `secondary_keywords` are not yet defined in the site's Zod schema. Until they are, every post will land in `manifest_issues`. That's expected; the report uses this to drive the frontmatter audit.
