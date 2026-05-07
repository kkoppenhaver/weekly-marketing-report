"""One-shot: inject target_keyword + secondary_keywords into the 27 CC4M posts.

Inserts the new fields right after the `description:` line, preserving all
other formatting. Skips files that already have target_keyword.

Run from this repo's root:
    .venv/bin/python scripts/apply_keywords.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path("~/code/claude-code-for-marketers").expanduser()
BLOG_DIR = REPO / "src/content/blog"

KEYWORDS: dict[str, str] = {
    "automate-kit-broadcasts-with-skills": "automate kit broadcasts",
    "build-a-landing-page-with-claude-code": "build a landing page with claude code",
    "claude-code-insights-command": "claude code insights command",
    "claude-code-tricks-i-wish-id-known-sooner": "claude code tricks",
    "claude-code-vs-chatgpt-for-marketing": "claude code vs chatgpt",
    "claude-code-vs-claude-cowork": "claude code vs claude cowork",
    "competitor-changelog-digest-automation": "automate competitor monitoring",
    "create-a-brand-guide-with-devtools-mcp": "ai brand guide generator",
    "deep-research-competitor-analysis": "ai competitor analysis",
    "dont-be-scared-of-the-terminal": "terminal for marketers",
    "getting-better-output-without-prompt-engineering": "improve claude code output",
    "giving-claude-code-superpowers-with-mcp-servers": "claude code mcp servers",
    "hooking-claude-code-up-to-google-docs": "claude code google docs",
    "hooking-claude-code-up-to-wordpress": "claude code wordpress",
    "installing-claude-code": "install claude code",
    "is-claude-max-worth-it-for-marketers": "is claude max worth it",
    "non-technical-marketers-claude-code-faq": "claude code for marketers",
    "obsidian-as-your-second-brain": "obsidian claude code",
    "repurpose-call-transcripts-social-media": "repurpose call transcripts",
    "the-claude-md-masterclass": "claude md file",
    "turn-one-piece-of-content-into-ten-social-posts": "repurpose content into social posts",
    "vibe-coding-for-marketers": "vibe coding for marketers",
    "what-are-skills": "claude code skills",
    "what-is-dangerously-skip-permissions": "dangerously skip permissions",
    "what-is-vibe-marketing": "vibe marketing",
    "why-i-stopped-using-ai-image-generators-for-infographics": "ai infographic generator",
    "keep-articles-up-to-date-with-claude-code": "content refresh with ai",
}


def inject(path: Path, keyword: str) -> str:
    """Return new file text with target_keyword + secondary_keywords inserted."""
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise RuntimeError(f"{path}: missing frontmatter opener")

    # Find description line within frontmatter.
    end_idx = None
    desc_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
        if line.startswith("description:"):
            desc_idx = i
        if line.startswith("target_keyword:"):
            return text  # already present, leave alone

    if end_idx is None:
        raise RuntimeError(f"{path}: missing frontmatter closer")
    if desc_idx is None:
        raise RuntimeError(f"{path}: no description line found")

    # description may span multiple lines if it uses YAML block scalars (rare here).
    # Insert after the description line; if next line is indented, walk forward.
    insert_at = desc_idx + 1
    while insert_at < end_idx and lines[insert_at].startswith((" ", "\t")):
        insert_at += 1

    new = [
        f'target_keyword: "{keyword}"\n',
        "secondary_keywords: []\n",
    ]
    lines[insert_at:insert_at] = new
    return "".join(lines)


def main() -> int:
    missing: list[str] = []
    written = 0
    skipped = 0
    for slug, keyword in KEYWORDS.items():
        path = BLOG_DIR / f"{slug}.md"
        if not path.exists():
            path = BLOG_DIR / f"{slug}.mdx"
        if not path.exists():
            missing.append(slug)
            continue

        original = path.read_text()
        updated = inject(path, keyword)
        if updated == original:
            skipped += 1
            continue
        path.write_text(updated)
        written += 1
        print(f"  ✓ {slug} → {keyword}")

    print(f"\nwrote {written} file(s), skipped {skipped} (already had target_keyword)")
    if missing:
        print(f"missing files: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
