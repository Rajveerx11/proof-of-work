# v0.2.0 evaluation evidence

This is a one-shot run of the complete v0.2.0 corpus. Every task was attempted once,
in filename order, against implementation commit `99842d9`.

## Result

| Fact | Recorded value |
|---|---:|
| Tasks | 20 |
| Passed | 20 |
| Failed | 0 |
| Pass rate | 100.0% |
| Average agent time | 39.200 s |
| Total evaluation wall time | 816.763 s |
| Usage coverage | 0/20 |

Passing required all three checks for every task: successful agent exit, successful
protected outcome verifier, and a clean deterministic anti-tampering gate. The report
omits equal-window comparison because its rows are different tasks, not repeated
measurements of the same task population.

## Environment

- Date: 2026-08-03
- OS: Microsoft Windows 11 Home Single Language, version 10.0.26200, build 26200
- CPU: 13th Gen Intel Core i7-13650HX
- Python: 3.14.3
- Agent: Codex CLI 0.146.0
- Model label passed to CLI: `gpt-5.6-sol`
- Execution mode: `[trusted-unrestricted]`
- Per-task agent timeout: 240 seconds

The built-in adapter requested ephemeral, clean configuration/rules modes and changed
the agent working root to each disposable fixture. Windows Codex workspace-write mode
did not permit writes in this environment, so the run explicitly used
`--trusted-unrestricted`. That bypass disables Codex permission and sandbox checks; the
agent could access the host and network. These are trusted fixtures, but this is not a
security-isolated benchmark.

The subscription CLI did not provide token or cost data to the harness. Usage is therefore
unknown for every run; aggregate zero fields in JSON are sums over zero metered runs, not a
claim of zero tokens or zero cost.

Claude Code 2.1.220 was preflighted but excluded: its CLI returned that the organization
had disabled Claude subscription access for Claude Code. No Claude score was recorded or
inferred.

## Artifacts

- [Static HTML report](index.html)
- [Machine-readable JSON](results.json)

SHA-256:

```text
258e7c49a9de47b0cd55b1d98ea2972282a5cd9525d6759f164aa416c016cb46  index.html
9fe5043e9518edb78892c5b658f4586cd4544bbdbaacdd82ee8c796209039fd2  results.json
```

The generated files contain summarized run facts only. They do not contain agent stdout,
stderr, workspaces, prompts, credentials, or secrets. Git records artifact integrity; the
underlying local SQLite evaluation history is not itself tamper-evident.

## Reproduce

```bash
status=0
for task in tasks/*.yaml; do
  uv run proof-of-work eval run "$task" \
    --agent codex \
    --model gpt-5.6-sol \
    --agent-label 'Codex CLI 0.146.0 on Windows 11' \
    --trusted-unrestricted \
    --agent-timeout 240 \
    --db .proofofwork/v0.2.0-codex-final.db || status=1
done

uv run proof-of-work eval report \
  --db .proofofwork/v0.2.0-codex-final.db \
  --limit 20 --no-comparison --format html \
  --output reports/v0.2.0/index.html

uv run proof-of-work eval report \
  --db .proofofwork/v0.2.0-codex-final.db \
  --limit 20 --no-comparison --format json \
  --output reports/v0.2.0/results.json

exit "$status"
```

This result describes this corpus, agent invocation, model label, and environment only. It
is not a general agent ranking, a repeated performance study, or an isolated security test.
