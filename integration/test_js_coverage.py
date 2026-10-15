"""Real installed providers; mandatory in the dedicated JS coverage CI job.

Run this file explicitly. Exact direct versions are selected below; npm verifies the
registry artifact integrity and records a fresh fixture lockfile. No global install.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from proofofwork import engine
from proofofwork.core import runner
from proofofwork.core.sandbox.local import LocalSandbox
from tests.test_production_policies import git


@pytest.fixture(scope='module', params=['jest', 'vitest'])
def project(request, tmp_path_factory):
    """Install real providers once per framework in a disposable fixture repository."""
    npm = shutil.which('npm')
    assert npm, 'Node/npm required for real JS provider validation'
    root = tmp_path_factory.mktemp(request.param)
    framework = request.param
    deps = ({'jest': '30.5.2'} if framework == 'jest' else
            {'vitest': '5.0.4', '@vitest/coverage-v8': '5.0.4'})
    package = {'private': True, 'devDependencies': deps}
    if framework == 'jest':
        package['jest'] = {'maxWorkers': 1, 'testFailureExitCode': 0, 'passWithNoTests': True}
    (root / 'package.json').write_text(json.dumps(package))
    if framework == 'vitest':
        (root / 'vitest.config.mjs').write_text(
            "export default {test: {maxWorkers: 1, passWithNoTests: true}};\n")
    installed = subprocess.run([npm, 'install', '--ignore-scripts', '--no-audit', '--no-fund'],
                               cwd=root, capture_output=True, text=True, timeout=240, check=False)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    (root / '.gitignore').write_text('node_modules/\n.proofofwork/\n')
    (root / 'app.cjs').write_text('exports.value = () => 1;\n')
    test = ("const {test, expect} = require('@jest/globals');\n" if framework == 'jest' else
            "import {test, expect} from 'vitest';\n")
    test += "import app from './app.cjs';\n" if framework == 'vitest' else "const app = require('./app.cjs');\n"
    test += "test('value', () => { expect(app.value()).toBe(1); });\n"
    (root / 'app.test.js').write_text(test)
    git(root, 'init', '-q')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    git(root, 'config', 'user.name', 'fixture')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'reviewed provider fixture')
    return root, framework, test


def test_real_js_reports_and_frozen_base(project):
    root, _framework, _ = project
    before = git(root, 'worktree', 'list', '--porcelain')
    positive = runner._run_js(LocalSandbox(), str(root))
    assert positive.ran and positive.passed and positive.coverage == 100, positive.raw
    plain = runner._run_js(LocalSandbox(), str(root), collect_coverage=False)
    assert plain.ran and plain.passed and plain.coverage is None, plain.raw
    measured = engine._measure_base(str(root), git(root, 'rev-parse', 'HEAD'), {'js'})
    assert measured.ran and measured.passed and measured.coverage == 100, measured.raw
    assert git(root, 'worktree', 'list', '--porcelain') == before


@pytest.mark.parametrize('mode', ['empty', 'skipped', 'failed'])
def test_real_js_nonexecution_and_failure_cannot_pass(project, mode):
    root, framework, original = project
    path = root / 'app.test.js'
    try:
        if mode == 'empty':
            path.unlink()
        elif mode == 'skipped':
            path.write_text(original.replace("test('value'", "test.skip('value'"))
        else:
            path.write_text(original.replace('toBe(1)', 'toBe(2)'))
        for collect in (True, False):
            result = runner._run_js(LocalSandbox(), str(root), collect_coverage=collect)
            assert result.passed is False, (framework, mode, result.raw)
    finally:
        path.write_text(original)


def test_real_js_changed_lockfile_never_runs_base(project, monkeypatch):
    root, _, _ = project
    lock = root / 'package-lock.json'
    original = lock.read_bytes()
    try:
        lock.write_bytes(original + b'\n')
        monkeypatch.setattr(runner, 'run_tests', lambda *a, **k: pytest.fail('must reject before run'))
        with pytest.raises(ValueError, match='dependency inputs differ'):
            engine._measure_base(str(root), git(root, 'rev-parse', 'HEAD'), {'js'})
    finally:
        lock.write_bytes(original)
