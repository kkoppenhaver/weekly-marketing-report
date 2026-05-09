"""Top-level orchestrator for the weekly site report.

Runs the 11 skills in dependency order. Single entry point used by the
Claude Code Routine each Sunday. Exits non-zero on first hard failure.

Each step is a separate Python process. We deliberately don't import
the scripts as modules — they each define their own argparse and the
isolation makes failure modes easier to reason about.

Usage:
    python scripts/run_pipeline.py [--week 2026-W18] [--skip-pagespeed] [--skip-dataforseo]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def bootstrap_ssl_bundle() -> None:
    """Merge the system CA bundle into certifi's bundle so HTTPS works through
    a TLS-inspecting proxy. Idempotent — safe to call when run.sh already did this.

    Routine containers commonly have a TLS-inspecting proxy whose root CA is in
    /etc/ssl/certs/ca-certificates.crt but not in certifi's bundled trust store.
    Without this merge every external HTTPS call fails with
    'self-signed certificate in certificate chain'.
    """
    try:
        import certifi  # noqa: PLC0415
    except ImportError:
        return  # certifi not installed yet; pip install hasn't run
    cert_path = Path(certifi.where())
    system_bundle = Path("/etc/ssl/certs/ca-certificates.crt")
    if system_bundle.exists():
        existing = cert_path.read_text() if cert_path.exists() else ""
        marker = "# === merged system CAs ==="
        if marker not in existing:
            cert_path.write_text(existing + f"\n{marker}\n" + system_bundle.read_text())
            print(f"  bootstrap: appended system CAs to {cert_path}")
    os.environ["SSL_CERT_FILE"] = str(cert_path)
    os.environ["REQUESTS_CA_BUNDLE"] = str(cert_path)

# (step_name, relative path to script, list of extra args)
PIPELINE = [
    ("fetch-post-manifest", ".claude/skills/fetch-post-manifest/scripts/fetch.py", []),
    ("fetch-search-console", ".claude/skills/fetch-search-console/scripts/fetch.py", []),
    ("fetch-fathom", ".claude/skills/fetch-fathom/scripts/fetch.py", []),
    ("fetch-kit", ".claude/skills/fetch-kit/scripts/fetch.py", []),
    ("fetch-pagespeed", ".claude/skills/fetch-pagespeed/scripts/fetch.py", []),
    ("fetch-dataforseo", ".claude/skills/fetch-dataforseo/scripts/fetch.py", []),
    ("analyze-content-performance", ".claude/skills/analyze-content-performance/scripts/analyze.py", []),
    ("analyze-seo-health", ".claude/skills/analyze-seo-health/scripts/analyze.py", []),
    ("analyze-keyword-opportunities", ".claude/skills/analyze-keyword-opportunities/scripts/analyze.py", []),
    ("compose-weekly-report", ".claude/skills/compose-weekly-report/scripts/compose.py", []),
    ("compose-email-summary", ".claude/skills/compose-email-summary/scripts/send.py", []),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week id (default: previous full week)")
    parser.add_argument("--skip", action="append", default=[], help="Skip a step by name (repeatable)")
    parser.add_argument("--dry-run-email", action="store_true", help="Don't actually send the email")
    return parser.parse_args()


def run_step(name: str, script: Path, extra_args: list[str]) -> bool:
    print(f"\n━━━ {name} ━━━")
    cmd = [sys.executable, str(script), *extra_args]
    started = time.monotonic()
    result = subprocess.run(cmd, cwd=ROOT)
    elapsed = time.monotonic() - started
    status = "ok" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    print(f"━━━ {name} {status} in {elapsed:.1f}s ━━━")
    return result.returncode == 0


def main() -> int:
    args = parse_args()
    extra_common: list[str] = []
    if args.week:
        extra_common += ["--week", args.week]

    # Defensive bootstrap — fixes SSL trust if run.sh wasn't invoked.
    bootstrap_ssl_bundle()

    # Print env-var diagnostics if the helper is available. Always useful at
    # the top of a Routine run.
    check_env_script = ROOT / "scripts" / "check_env.py"
    if check_env_script.exists():
        print("━━━ env check ━━━")
        subprocess.run([sys.executable, str(check_env_script)], cwd=ROOT)
        print("━━━ env check: complete ━━━\n")

    failures: list[str] = []
    for name, rel_path, step_args in PIPELINE:
        if name in args.skip:
            print(f"\n━━━ {name} (skipped) ━━━")
            continue
        script = ROOT / rel_path
        if not script.exists():
            print(f"error: {script} not found", file=sys.stderr)
            return 2

        full_args = list(step_args) + extra_common
        if name == "compose-email-summary" and args.dry_run_email:
            full_args.append("--dry-run")

        ok = run_step(name, script, full_args)
        if not ok:
            failures.append(name)
            # Decide whether to continue. Fetchers/analyzers can degrade; composers can't run without upstream data.
            if name.startswith("compose-"):
                print("compose step failed — aborting pipeline.", file=sys.stderr)
                return 1
            print(f"warning: {name} failed; continuing — downstream steps may produce thinner output.", file=sys.stderr)

    if failures:
        print(f"\nPipeline finished with {len(failures)} failure(s): {failures}", file=sys.stderr)
        return 1
    print("\nPipeline finished cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
