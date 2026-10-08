import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def test_composite_action_installs_the_checked_out_action_source():
    action_path = Path(__file__).parents[1] / "proofofwork" / "interfaces" / "action.yml"
    action = yaml.safe_load(action_path.read_text(encoding="utf-8"))
    install = next(
        step
        for step in action["runs"]["steps"]
        if step.get("name") == "Install proof-of-work"
    )

    assert install["env"]["ACTION_ROOT"] == "${{ github.action_path }}/../.."
    assert install["run"] == 'python -m pip install "$ACTION_ROOT"'


def test_composite_action_uses_verified_event_base_not_shallow_checkout_history():
    action_path = Path(__file__).parents[1] / "proofofwork" / "interfaces" / "action.yml"
    action = yaml.safe_load(action_path.read_text(encoding="utf-8"))
    gate = next(step for step in action["runs"]["steps"] if step.get("name") == "Run gate")
    run = gate["run"]

    assert gate["env"]["PR_BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert gate["env"]["PUSH_BASE_SHA"] == "${{ github.event.before }}"
    assert 'pull_request) base="$PR_BASE_SHA"' in run
    assert 'push) base="$PUSH_BASE_SHA"' in run
    assert '*) echo "Unsupported event:' in run
    assert '[[ ! "$base" =~ ^[0-9a-fA-F]{40}$ ]] || [[ "$base" =~ ^0+$ ]]' in run
    assert 'git fetch --no-tags --depth=1 origin "$base"' in run
    assert 'git cat-file -e "$base^{commit}"' in run
    assert 'proof-of-work check --base "$base"' in run
    assert 'HEAD~1' not in run
    assert 'origin/$' not in run


# Windows' System32 bash shim may exist without any WSL distribution. The Action
# uses Git Bash on Windows runners, so exercise that same executable explicitly.
_BASH = shutil.which("bash")
if os.name == "nt" and (git := shutil.which("git")):
    git_bash = Path(git).resolve().parent.parent / "bin" / "bash.exe"
    if git_bash.is_file():
        _BASH = str(git_bash)


@pytest.mark.skipif(_BASH is None, reason="Action requires bash")
@pytest.mark.parametrize("policy,expected_code,expected_flag", [
    ("true", 0, True), ("false", 0, False), ("invalid", 1, False),
    ("true; echo injected", 1, False),
])
def test_action_strict_policy_fixture(policy, expected_code, expected_flag):
    action_path = Path(__file__).parents[1] / "proofofwork" / "interfaces" / "action.yml"
    action = yaml.safe_load(action_path.read_text(encoding="utf-8"))
    assert action["inputs"]["strict-integrity"]["default"] == "false"
    gate = next(step for step in action["runs"]["steps"] if step.get("name") == "Run gate")
    assert gate["env"]["STRICT_INTEGRITY"] == "${{ inputs.strict-integrity }}"
    script = gate["run"].replace("${{ inputs.mutation }}", "false")
    # Platform fixture: git succeeds, the CLI echoes the exact trusted arguments.
    prefix = 'git() { return 0; }; proof-of-work() { printf "ARG:%s\\n" "$@"; };\n'
    # Assign in-shell as Windows bash may be WSL and not inherit Windows env vars.
    config = ("GITHUB_EVENT_NAME=pull_request\nPR_BASE_SHA=" + "a" * 40
              + "\nSTRICT_INTEGRITY=" + shlex.quote(policy) + "\n")
    result = subprocess.run(
        [_BASH, "-s"], input=(prefix + config + script).encode("utf-8"),
        capture_output=True, check=False,
    )
    assert result.returncode == expected_code
    assert (b"ARG:--strict-integrity" in result.stdout) == expected_flag
    if expected_code:
        assert b"ARG:" not in result.stdout
        assert b"Invalid strict-integrity" in result.stderr


def test_release_attaches_evidence_matching_the_release_tag():
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "release.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "reports/v0.2.0" not in workflow
    assert '"reports/${GITHUB_REF_NAME}/README.md"' in workflow
    assert '"reports/${GITHUB_REF_NAME}/index.html"' in workflow
    assert '"reports/${GITHUB_REF_NAME}/results.json"' in workflow
    validation = workflow.index("Verify matching release evidence")
    publication = workflow.index("Publish to PyPI")
    assert validation < publication
    assert 'test -f "reports/${GITHUB_REF_NAME}/README.md"' in workflow
    assert 'test -f "reports/${GITHUB_REF_NAME}/index.html"' in workflow
    assert 'test -f "reports/${GITHUB_REF_NAME}/results.json"' in workflow
