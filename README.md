# Weekly Marketing Report

Automation that builds a weekly intelligence report for [claudecodeformarketers.com](https://claudecodeformarketers.com).

Runs every Sunday morning. Pulls data from Google Search Console, Fathom Analytics, Kit, PageSpeed Insights, and DataForSEO. Produces a Google Doc with prioritized SEO fixes, content performance analysis, and a backlog of new posts to consider. Emails the summary via Resend.

See [`plan.md`](./plan.md) for the full architecture and skill specs.

## Layout

```
.claude/skills/      # 11 Claude Code skills (6 fetchers, 3 analyzers, 2 composers)
lib/                 # Shared Python helpers (storage, ISO weeks, env loading)
snapshots/           # Local snapshot store used in dev (gitignored)
plan.md              # Architecture and skill specs
```

Each skill is a directory with a `SKILL.md` (instructions for Claude) and, for fetchers, a `scripts/` subdir with deterministic Python.

## Local development

```bash
uv sync                          # or: python -m venv .venv && pip install -e .
cp .env.example .env             # fill in what's available
python .claude/skills/fetch-post-manifest/scripts/fetch.py
```

Skills write to `./snapshots/{week}/` when `STORAGE=local`. Switch to `STORAGE=r2` for the production Routine.

## Production

The skills run inside a Claude Code Routine on a weekly cron. The Routine is connected to this repo and the [site repo](https://github.com/kkoppenhaver/claude-code-for-marketers).
