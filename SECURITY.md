# Security Policy

Proof-of-Work is a security tool, so we hold its own integrity to a high bar.

## Threat model (know this before reporting)

The v1 local log is **tamper-evident, not tamper-proof.** It is a hash-chained SQLite log
whose head is signed with a local Ed25519 key (`.proofofwork/log.key`). This detects *edits*
to history, but anyone who holds that local key can rewrite the whole chain. Removing that
limitation — keyless signing (OIDC) plus a public transparency log (Rekor) for
cross-repo, un-forgeable proof — is a v2 goal. Please frame reports against this model.

By design, the tool also:

- runs a repository's **real test suite** as a contained local subprocess. This is a
  trusted-local runner, not a security sandbox; reviewed commands can access the host and
  network. Do not treat temporary snapshots, hooks or subprocess cleanup as hostile-code
  isolation. The source's experimental Docker API has bounded live Linux evidence but
  is not wired into the gate; `check --sandbox docker` fails closed. Untrusted eval is
  also rejected. See [isolation boundaries](docs/isolation.md) before provisioning an
  external container/microVM for untrusted repositories, and
- never lets the optional LLM judge influence the signed verdict.

Trusted operators must protect the launcher, policy switches, reviewed base refs and
signing authority outside candidate control. A local pre-commit hook alone is not an
unskippable enforcement boundary. Required coverage and strict integrity are defaults
on current source, not features retroactively added to the published v0.2.0 artifact.
For the difference between a release and a source install, see
[installation](docs/installation.md) and [current hardening status](docs/production-hardening-status.md).

## Reporting a vulnerability

Please report privately — do **not** open a public issue:

1. **Preferred:** GitHub → the repo's **Security** tab → **Report a vulnerability**
   (private security advisory), or
2. Email **rajveer11vadnal@gmail.com** with steps to reproduce and impact.

We'll acknowledge your report, work with you on a fix, and credit you in the release notes
unless you prefer to stay anonymous. Please give us reasonable time to ship a fix before
public disclosure.

## Supported versions

Pre-1.0, security reports are accepted for the latest published release and current
`main`; these are different revisions. Report the exact package/source commit,
platform, execution mode and policy selections. Source hardening is unreleased and
is not a production-readiness certification. Once 1.0 lands, this section will list
the maintained lines.
