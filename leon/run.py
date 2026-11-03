"""
One command to get the whole thing running.

    python run.py            build the database if needed, then serve
    python run.py --rebuild  regenerate the raw feeds and rebuild from scratch
    python run.py --check    build, run every check, print the report, exit
    python run.py --port 8080

Then open http://localhost:8000 for the app, or /docs for the API.

Deliberately does the setup for you. A project that needs four commands in the
right order before it shows anything is a project that fails in front of an
audience.
"""

from __future__ import annotations

import argparse
import sys

import app.console  # noqa: F401  — makes Windows console output safe
from app.config import settings
from app.db import build, run_quality_checks
from app.feeds import write_all
from app.generate import generate_cohort
from app.pipeline import run as run_pipeline


def build_everything(*, verbose: bool = True) -> bool:
    """Generate → write messy feeds → conform → load → check.

    Returns False if any data-quality check failed, so `--check` can be used in
    a CI step or a pre-flight before a demo.
    """
    settings.ensure_dirs()

    say = print if verbose else (lambda *a, **k: None)

    say("1. generating the synthetic cohort")
    members = generate_cohort()
    say(f"   {len(members)} members, {sum(m.attempt_count for m in members)} attempts")

    say("\n2. writing source extracts to data/raw")
    for name, count in write_all(members).items():
        say(f"   {name:<22}{count:>6,} rows")

    say("\n3. running the pipeline over those extracts")
    conformed, report = run_pipeline()
    say(report.render())

    say("\n4. loading the conformed cohort into SQLite")
    counts = build(conformed)
    for key, value in counts.items():
        say(f"   {key:<16}{value:>6,}")

    say("\n5. data-quality checks")
    ok = True
    for check in run_quality_checks():
        mark = "PASS" if check["passed"] else "FAIL"
        ok &= check["passed"]
        say(f"   [{mark}] {check['check']:<40} observed={check['observed']}")

    say(f"\n   {'all checks passed' if ok else '*** CHECKS FAILED ***'}")
    say(f"   database: {settings.db_path}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="force a full rebuild")
    parser.add_argument("--check", action="store_true", help="build and verify, then exit")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    if args.check or args.rebuild or not settings.db_path.exists():
        if not settings.db_path.exists() and not args.check:
            print("no database found - building it first\n")
        ok = build_everything()
        if args.check:
            return 0 if ok else 1
        if not ok:
            print("\nrefusing to serve on data that failed its own checks.", file=sys.stderr)
            return 1
        print()

    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed - run: pip install fastapi uvicorn", file=sys.stderr)
        return 1

    print(f"  app   http://{args.host}:{args.port}")
    print(f"  api   http://{args.host}:{args.port}/docs")
    print(f"  model {'configured (' + settings.llm_provider + ')' if settings.llm_available else 'not configured - using the deterministic engines'}\n")
    uvicorn.run("app.api:app", host=args.host, port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
