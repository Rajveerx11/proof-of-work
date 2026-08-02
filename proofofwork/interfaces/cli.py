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


def _cmd_learn(args: argparse.Namespace) -> int:
    from ..learn import loop

    res = loop.run(rules_path=args.rules, write=not args.dry_run)
    if args.json:
        print(json.dumps(res.as_dict(), indent=2))
        return 0

    verb = "would promote" if args.dry_run else "promoted"
    print(f"{verb} {len(res.promoted)} rule(s); skipped {len(res.skipped)}")
    for event in res.events:
        if event.status == "promoted":
            rule = event.rule or {}
            print(f"  + {rule['id']}  [{rule['severity']}]  /{rule['pattern']}/")
            print(f"    {event.cheat}: {event.reason}; fp={event.false_positives}")
        elif event.status == "rejected":
            rule = event.rule or {}
            print(f"  ! {event.cheat}: rejected {rule.get('id', '<no-rule>')} — {event.reason}")
        else:
            print(f"  - {event.cheat}: {event.reason}")
    return 0


def _cmd_verify_log(args: argparse.Namespace) -> int:
    from .. import log

    db = args.db or os.path.join(args.root, ".proofofwork", "log.db")
    if log.verify_chain(db):
        print("log intact ✓")
        return 0
    print("LOG TAMPERED ✗")
    return 1


def _cmd_eval_run(args: argparse.Namespace) -> int:
    from ..eval import HistoryError, TaskValidationError, load_task, record_run, run_task

    try:
        agent_argv = json.loads(args.agent_argv_json)
        task = load_task(args.task)
        result = run_task(task, agent_argv, agent_timeout_seconds=args.agent_timeout)
    except (TaskValidationError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"eval configuration error: {exc}")
        return 2

    data = result.as_dict()
    history_error = None
    if args.no_record:
        data["history"] = {"recorded": False}
    else:
        try:
            run_id = record_run(result, args.db)
        except HistoryError as exc:
            history_error = str(exc)
            data["history"] = {
                "recorded": False,
                "database": args.db,
                "error": history_error,
            }
        else:
            data["history"] = {
                "recorded": True,
                "database": args.db,
                "run_id": run_id,
            }
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print("PASS" if result.passed else "FAIL")
        print(f"  task: {result.task_id}")
        print(f"  agent: exit={result.agent.exit_code}, {result.agent.duration_seconds:.2f}s")
        print(f"  outcome: exit={result.outcome.exit_code}, {result.outcome.duration_seconds:.2f}s")
        if result.usage is not None:
            print(
                f"  usage: {result.usage.total_tokens} tokens "
                f"(input={result.usage.input_tokens}, output={result.usage.output_tokens}), "
                f"${result.usage.cost_usd:.6f} USD"
            )
        elif result.usage_error is not None:
            print(f"  usage: unavailable ({result.usage_error})")
        else:
            print("  usage: not reported")
        if result.gate is not None:
            print(f"  gate: {'PASS' if result.gate.passed else 'FAIL'}")
            for reason in result.gate.reasons:
                print(f"    - {reason}")
        if args.no_record:
            print("  history: not recorded")
        elif history_error is not None:
            print(f"  history: ERROR: {history_error}")
        else:
            print(f"  history: run #{run_id} in {args.db}")
        print("  verification: outcome command + deterministic Proof-of-Work gate")
        print("  security: trusted local inputs required")
    if history_error is not None:
        return 2
    return 0 if result.passed else 1


def _cmd_eval_report(args: argparse.Namespace) -> int:
    from ..eval import HistoryError, build_report

    try:
        report = build_report(
            args.db,
            task_id=args.task_id,
            window=args.window,
            limit=args.limit,
        )
    except (HistoryError, ValueError) as exc:
        print(f"eval history error: {exc}")
        return 2

    data = {"database": args.db, **report.as_dict()}
    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    totals = report.totals
    scope = f"task {args.task_id}" if args.task_id else "all tasks"
    print(f"EVAL HISTORY ({scope})")
    print(f"  database: {args.db}")
    if totals.runs == 0:
        print("  no runs recorded")
        return 0
    print(
        f"  runs: {totals.runs} "
        f"({totals.passed} passed, {totals.failed} failed; "
        f"{_percentage(totals.pass_rate)})"
    )
    print(
        "  average duration: "
        f"agent={totals.average_agent_duration_seconds:.2f}s, "
        f"outcome={totals.average_outcome_duration_seconds:.2f}s"
    )
    if totals.usage_runs:
        print(
            f"  usage: {totals.total_tokens} tokens, "
            f"${totals.total_cost_usd_micros / 1_000_000:.6f} USD "
            f"across {totals.usage_runs}/{totals.runs} runs"
        )
    else:
        print(f"  usage: not reported (0/{totals.runs} runs)")
    trend = report.trend
    if trend.window_size == 0:
        print("  trend: need at least 2 runs")
    else:
        print(f"  trend: latest {trend.window_size} vs previous {trend.window_size}")
        print(
            "    pass rate: "
            f"{_percentage(trend.recent.pass_rate)} vs "
            f"{_percentage(trend.previous.pass_rate)} "
            f"({_signed(trend.pass_rate_delta_percentage_points)} pp)"
        )
        print(
            "    agent duration: "
            f"{trend.recent.average_agent_duration_seconds:.2f}s vs "
            f"{trend.previous.average_agent_duration_seconds:.2f}s "
            f"({_signed(trend.average_agent_duration_delta_seconds)}s)"
        )
        print(
            "    outcome duration: "
            f"{trend.recent.average_outcome_duration_seconds:.2f}s vs "
            f"{trend.previous.average_outcome_duration_seconds:.2f}s "
            f"({_signed(trend.average_outcome_duration_delta_seconds)}s)"
        )
    print("  recent runs:")
    for run in report.runs:
        status = "PASS" if run.passed else "FAIL"
        usage_tokens = (
            run.input_tokens + run.output_tokens
            if run.input_tokens is not None and run.output_tokens is not None
            else "n/a"
        )
        usage_cost = (
            f"${run.cost_usd_micros / 1_000_000:.6f}"
            if run.cost_usd_micros is not None
            else "n/a"
        )
        print(
            f"    #{run.id} {run.recorded_at} {run.task_id}: {status} "
            f"(agent={run.agent_duration_seconds:.2f}s, "
            f"outcome={run.outcome_duration_seconds:.2f}s, "
            f"usage={usage_tokens} tokens, cost={usage_cost})"
        )
    return 0


def _percentage(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def _report_bound(value: str) -> int:
    from ..eval.history import MAX_REPORT_ROWS

    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 1 <= parsed <= MAX_REPORT_ROWS:
        raise argparse.ArgumentTypeError(f"must be from 1 to {MAX_REPORT_ROWS}")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    from ..eval import DEFAULT_HISTORY_DB

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

    lp = sub.add_parser("learn", help="grow the ruleset from the frozen cheat corpus (gated)")
    lp.add_argument("--rules", default=None, help="path to a rules file (default: shipped)")
    lp.add_argument("--dry-run", action="store_true", help="propose + gate, but don't write")
    lp.add_argument("--json", action="store_true", help="emit proposed rules and gate results as JSON")
    lp.set_defaults(func=_cmd_learn)

    h = sub.add_parser("install-hook", help="install the pre-commit gate")
    h.add_argument("--root", default=".")
    h.set_defaults(func=_cmd_install_hook)

    v = sub.add_parser("verify-log", help="check the tamper-evident log chain")
    v.add_argument("--db", default=None)
    v.add_argument("--root", default=".")
    v.set_defaults(func=_cmd_verify_log)

    e = sub.add_parser("eval", help="run a trusted local coding-agent benchmark")
    e_sub = e.add_subparsers(dest="eval_cmd", required=True)
    er = e_sub.add_parser("run", help="run one YAML task in a disposable workspace")
    er.add_argument("task", help="path to a version-1 task YAML file")
    er.add_argument(
        "--agent-argv-json",
        required=True,
        help=(
            'trusted command JSON argv; include "{workspace}" and optionally "{usage}" '
            "for wrapper-emitted token/cost JSON"
        ),
    )
    er.add_argument("--agent-timeout", type=int, default=600)
    er.add_argument("--db", default=DEFAULT_HISTORY_DB, help="SQLite eval history path")
    er.add_argument("--no-record", action="store_true", help="do not persist this run")
    er.add_argument("--json", action="store_true")
    er.set_defaults(func=_cmd_eval_run)
    ep = e_sub.add_parser("report", help="show persistent eval history and trends")
    ep.add_argument("--db", default=DEFAULT_HISTORY_DB, help="SQLite eval history path")
    ep.add_argument("--task-id", default=None, help="limit report to one task id")
    ep.add_argument("--window", type=_report_bound, default=10,
                    help="maximum runs in each trend window (default: 10)")
    ep.add_argument("--limit", type=_report_bound, default=20,
                    help="maximum recent runs to list (default: 20)")
    ep.add_argument("--json", action="store_true")
    ep.set_defaults(func=_cmd_eval_report)

    return p


_SUBCOMMANDS = {"check", "learn", "install-hook", "verify-log", "eval"}


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
