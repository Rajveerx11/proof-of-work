"""Shared data contract. Every module speaks these types; the verdict trusts only facts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    BLOCK = "block"   # a cheat signal — fails the verdict on its own
    WARN = "warn"     # suspicious — surfaced, but does not fail alone
    INFO = "info"


@dataclass
class Finding:
    rule: str                 # stable id, e.g. "deleted-test", "fake-pass:sys-exit"
    severity: Severity
    message: str
    file: str = ""
    line: int = 0
    evidence: str = ""

    def as_dict(self) -> dict:
        return {
            "rule": self.rule, "severity": self.severity.value,
            "message": self.message, "file": self.file,
            "line": self.line, "evidence": self.evidence,
        }


# --- parsed git diff (produced by core.gitdiff, consumed by detector checks) ---
@dataclass
class DiffFile:
    path: str
    status: str                        # "A" added | "M" modified | "D" deleted | "R" renamed
    old_path: str = ""                 # populated for renames
    added: list[str] = field(default_factory=list)     # added source lines (text, no +)
    removed: list[str] = field(default_factory=list)    # removed source lines (text, no -)
    is_test: bool = False
    language: str = ""                 # "python" | "js" | "ts" | ""


@dataclass
class Diff:
    files: list[DiffFile] = field(default_factory=list)
    base_ref: str = "HEAD"

    def languages(self) -> set[str]:
        return {f.language for f in self.files if f.language}

    def text(self) -> str:
        """Flat unified-ish text — for the advisory judge only, never for the verdict."""
        out = []
        for f in self.files:
            out.append(f"# {f.status} {f.path}")
            out += [f"-{ln}" for ln in f.removed]
            out += [f"+{ln}" for ln in f.added]
        return "\n".join(out)


# --- facts from executing the real suite (produced by core.runner) ---
@dataclass
class TestResult:
    __test__ = False  # not a pytest test class despite the Test* name
    ran: bool = False
    passed: bool | None = None         # None = could not determine
    coverage: float | None = None      # percent 0..100
    framework: str = ""                # pytest | vitest | jest
    raw: str = ""                      # captured tail, for debugging


@dataclass
class MutationResult:
    ran: bool = False
    survived: int | None = None
    killed: int | None = None
    tool: str = ""                     # mutmut | stryker


@dataclass
class Verdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    tests: TestResult = field(default_factory=TestResult)
    mutation: MutationResult = field(default_factory=MutationResult)
    coverage_baseline: float | None = None
    judge: dict | None = None          # advisory metadata ONLY — never decides `passed`
    entry_hash: str = ""               # tamper-evident log row hash

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reasons": self.reasons,
            "findings": [f.as_dict() for f in self.findings],
            "tests": self.tests.__dict__,
            "mutation": self.mutation.__dict__,
            "coverage_baseline": self.coverage_baseline,
            "judge": self.judge,
            "entry_hash": self.entry_hash,
        }
