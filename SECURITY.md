# Security Policy

Proof-of-Work is a security tool, so we hold its own integrity to a high bar.

## Threat model (know this before reporting)

The v1 local log is **tamper-evident, not tamper-proof.** It is a hash-chained SQLite log
whose head is signed with a local Ed25519 key (`.proofofwork/log.key`). This detects *edits*
to history, but anyone who holds that local key can rewrite the whole chain. Removing that
limitation — keyless signing (OIDC) plus a public transparency log (Rekor) for
cross-repo, un-forgeable proof — is a v2 goal. Please frame reports against this model.

By design, the tool also:

- runs a repository's **real test suite** in a sandbox (v1: local subprocess) — treat
  untrusted repositories accordingly, and
- never lets the optional LLM judge influence the signed verdict.

## Reporting a vulnerability

Please report privately — do **not** open a public issue:

1. **Preferred:** GitHub → the repo's **Security** tab → **Report a vulnerability**
   (private security advisory), or
2. Email **rajveer11vadnal@gmail.com** with steps to reproduce and impact.

We'll acknowledge your report, work with you on a fix, and credit you in the release notes
unless you prefer to stay anonymous. Please give us reasonable time to ship a fix before
public disclosure.

## Supported versions

Pre-1.0, only the latest release / `main` is supported. Once 1.0 lands, this section will
list the maintained lines.
