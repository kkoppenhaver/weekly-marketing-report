# Weekly Marketing Report

Automation that builds a weekly intelligence report for [claudecodeformarketers.com](https://claudecodeformarketers.com).

Pulls data from Google Search Console, Fathom Analytics, Kit, PageSpeed Insights, and DataForSEO. Produces a Google Doc with prioritized SEO fixes, content performance analysis, and a backlog of new posts to consider. Emails the summary via Resend.

Run manually on Sunday mornings:

```bash
scripts/run.sh
```

See [`plan.md`](./plan.md) for the full architecture and skill specs.

## Layout

```
.claude/skills/      # 11 Claude Code skills (6 fetchers, 3 analyzers, 2 composers)
lib/                 # Shared Python helpers (storage, ISO weeks, env loading)
snapshots/           # Local snapshot store used in dev (gitignored)
plan.md              # Architecture and skill specs
```

Each skill is a directory with a `SKILL.md` (instructions for Claude) and, for fetchers, a `scripts/` subdir with deterministic Python.

## Setup (one-time)

```bash
uv sync                          # or: python -m venv .venv && pip install -e .
cp .env.example .env             # fill in credentials — see plan.md §2 for the full list
```

You also need a local checkout of the [site repo](https://github.com/kkoppenhaver/claude-code-for-marketers) and `SITE_REPO_LOCAL` in `.env` pointing at it. `fetch-post-manifest` reads frontmatter directly from `src/content/blog/`.

## Running the weekly report

```bash
scripts/run.sh                   # full pipeline, fetch → analyze → compose → email
```

`run.sh` creates a fresh `.venv`, installs deps, runs `scripts/check_env.py` for credential diagnostics, then invokes `scripts/run_pipeline.py`. The pipeline runs the 11 skills in dependency order and is sequential — fetcher failures don't abort the run (only compose failures do), so you'll see partial output if a single fetcher errors.

The Google Doc URL is written to the snapshot store at `reports/{week}/doc-url.txt`. The email goes out via Resend at the end.

To run an individual skill (debugging a single fetcher, etc.):

```bash
python .claude/skills/fetch-fathom/scripts/fetch.py [--week 2026-W18]
```

Skills write to `./snapshots/{week}/` when `STORAGE=local`. Switch to `STORAGE=r2` to read/write Cloudflare R2 instead.

## Routine deployment (alternate)

The pipeline was originally designed to run inside a Claude Code Routine on a weekly cron. That path is documented in [`ROUTINE_SETUP.md`](./ROUTINE_SETUP.md) but is currently not in use — TLS-inspecting proxies in the Routine container caused enough operational pain that manual local runs are the supported path. The Routine code path (cert-bundle merging, etc.) is still present in `scripts/run.sh` and `scripts/run_pipeline.py` so re-enabling it later is just a config exercise, not a code one.
