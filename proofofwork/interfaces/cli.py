"""Command-line surface for Proof-of-Work.

The exit code IS the enforcement contract: 0 = passed, 1 = failed. The agent
that called "done" cannot reinterpret a nonzero exit — the hook/CI blocks on it.
"""
from __future__ import annotations

import argparse
import json
import os


def _cmd_check(args: argparse.Namespace) -> int:
    from .. import engine

    verdict = engine.check(
        root=args.root,
        base_ref=args.base,
        staged=args.staged,
        run_tests=not args.no_tests,
        run_mutation=args.mutation,
        use_judge=args.judge,
        update_baseline=args.update_baseline,
        db_path=args.db,
    )

    if args.json:
        print(json.dumps(verdict.as_dict(), indent=2))
    else:
        print("\033[1mPASS\033[0m" if verdict.passed else "\033[1mFAIL\033[0m")
        for r in verdict.reasons:
            print(f"  - {r}")
        for f in verdict.findings:
            print(f"  [{f.severity.value}] {f.rule}: {f.message} ({f.file}:{f.line})")

    return 0 if verdict.passed else 1


def _cmd_install_hook(args: argparse.Namespace) -> int:
    from . import precommit

    print(precommit.install(args.root))
    return 0


def _cmd_verify_log(args: argparse.Namespace) -> int:
    from .. import log

    db = args.db or os.path.join(args.root, ".proofofwork", "log.db")
    if log.verify_chain(db):
        print("log intact ✓")
        return 0
    print("LOG TAMPERED ✗")
    return 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="proof-of-work",
                                description="Re-check an AI agent's 'done' against facts.")
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("check", help="run the gate on a changeset")
    c.add_argument("--root", default=".")
    c.add_argument("--base", default="HEAD")
    c.add_argument("--staged", action="store_true")
    c.add_argument("--no-tests", action="store_true", help="skip re-running the suite")
    c.add_argument("--mutation", action="store_true")
    c.add_argument("--judge", action="store_true", help="add advisory LLM hints (BYO key)")
    c.add_argument("--update-baseline", action="store_true")
    c.add_argument("--db", default=None)
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=_cmd_check)

    h = sub.add_parser("install-hook", help="install the pre-commit gate")
    h.add_argument("--root", default=".")
    h.set_defaults(func=_cmd_install_hook)

    v = sub.add_parser("verify-log", help="check the tamper-evident log chain")
    v.add_argument("--db", default=None)
    v.add_argument("--root", default=".")
    v.set_defaults(func=_cmd_verify_log)

    return p


_SUBCOMMANDS = {"check", "install-hook", "verify-log"}


def main(argv: list[str] | None = None) -> int:
    import sys

    # Windows consoles default to cp1252 and crash on non-ASCII output (✓, →).
    # Force UTF-8 once, here, so every command's output is safe.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = _build_parser()
    if argv is None:
        argv = sys.argv[1:]
    # ponytail: default to `check` when no subcommand given — the common case.
    if not argv or (argv[0] not in _SUBCOMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["check", *argv]
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
