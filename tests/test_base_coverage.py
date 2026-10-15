"""Frozen-base coverage preserves policy and rejects incompatible dependency reuse."""
from __future__ import annotations

import pytest

from proofofwork import engine
from proofofwork.core import runner
from proofofwork.log.envelope import build_envelope
from proofofwork.types import TestResult
from tests.test_production_policies import git, payload, put, repo, result, rules


def test_actual_frozen_base_positive_negative_and_cleanup(tmp_path):
    base = repo(tmp_path)
    before = git(tmp_path, 'worktree', 'list', '--porcelain')
    positive = engine.check(str(tmp_path), base_ref=base, measure_base_coverage=True)
    assert positive.passed, positive.reasons
    assert positive.coverage_baseline == 100
    assert positive.coverage_status == 'verified'
    assert positive.coverage_baseline_source == 'measured:' + base
    assert build_envelope('fixture', positive)['predicate']['coverage_baseline_source'] == 'measured:' + base
    assert not (tmp_path / '.proofofwork/baseline.json').exists()
    (tmp_path / 'app.py').write_text('value = 2\n\ndef uncovered():\n' +
                                     ''.join(f'    v{i} = {i}\n' for i in range(40)))
    negative = engine.check(str(tmp_path), base_ref=base, measure_base_coverage=True)
    assert negative.tests.passed and not negative.passed
    assert 'coverage-drop' in rules(negative)
    assert git(tmp_path, 'worktree', 'list', '--porcelain') == before


@pytest.mark.parametrize('baseline', [payload(90), {'coverage': 90}, 'invalid'])
def test_existing_baseline_never_replaced_by_measurement(monkeypatch, tmp_path, baseline):
    base = repo(tmp_path, baseline)
    monkeypatch.setattr(engine, '_measure_base', lambda *a: pytest.fail('must not replace baseline'))
    monkeypatch.setattr(runner, 'run_tests', lambda *a, **k: result())
    verdict = engine.check(str(tmp_path), base_ref=base, measure_base_coverage=True)
    assert verdict.passed is (baseline == payload(90))
    assert verdict.coverage_baseline_source == base


@pytest.mark.parametrize('kwargs', [{'staged': True}, {'update_baseline': True},
                                   {'run_tests': False}, {'coverage_policy': 'test-only'}])
def test_invalid_base_measurement_modes_suppress_execution(monkeypatch, tmp_path, kwargs):
    base = repo(tmp_path)
    monkeypatch.setattr(engine, '_measure_base', lambda *a: pytest.fail('base must not run'))
    monkeypatch.setattr(runner, 'run_tests', lambda *a, **k: pytest.fail('candidate must not run'))
    verdict = engine.check(str(tmp_path), base_ref=base, measure_base_coverage=True, **kwargs)
    assert not verdict.passed and 'coverage-policy-invalid' in rules(verdict)


@pytest.mark.parametrize('base_result', [TestResult(), result(coverage=None), result(coverage=float('nan')),
                                       TestResult(ran=True, passed=False, coverage=100, framework='pytest')])
def test_failed_or_missing_base_measurement_cannot_fall_back(monkeypatch, tmp_path, base_result):
    base = repo(tmp_path)
    monkeypatch.setattr(engine, '_measure_base', lambda *a: base_result)
    monkeypatch.setattr(runner, 'run_tests', lambda *a, **k: pytest.fail('candidate must not run'))
    verdict = engine.check(str(tmp_path), base_ref=base, measure_base_coverage=True)
    assert not verdict.passed and 'coverage-base-unavailable' in rules(verdict)


@pytest.mark.parametrize('name,languages', [('package-lock.json', {'js'}), ('package.json', {'js'}),
                                         ('uv.lock', {'python'}), ('requirements-dev.txt', {'python'}),
                                         ('nested/package.json', {'js'})])
def test_changed_dependency_inputs_reject_before_base_execution(monkeypatch, tmp_path, name, languages):
    base = repo(tmp_path)
    path = tmp_path / name
    path.parent.mkdir(exist_ok=True)
    path.write_text('{}')
    monkeypatch.setattr(runner, 'run_tests', lambda *a, **k: pytest.fail('incompatible base must not run'))
    before = git(tmp_path, 'worktree', 'list', '--porcelain')
    with pytest.raises(ValueError, match='dependency inputs differ'):
        engine._measure_base(str(tmp_path), base, languages)
    assert git(tmp_path, 'worktree', 'list', '--porcelain') == before


def test_measurement_is_frozen_before_candidate_detectors(monkeypatch, tmp_path):
    from proofofwork.core import detector
    base = repo(tmp_path)
    calls = []
    monkeypatch.setattr(engine, '_measure_base', lambda *a: calls.append('base') or result())
    monkeypatch.setattr(detector, 'ALL_CHECKS', [lambda *a: calls.append('detector') or []])
    monkeypatch.setattr(runner, 'run_tests', lambda *a, **k: calls.append('candidate') or result())
    verdict = engine.check(str(tmp_path), base_ref=base, measure_base_coverage=True)
    assert verdict.passed and calls == ['base', 'detector', 'candidate']


def test_dependency_reuse_rejects_unlocked_js_and_workspaces(tmp_path):
    candidate, frozen = tmp_path / 'candidate', tmp_path / 'base'
    for directory in (candidate, frozen):
        directory.mkdir()
        put(directory / 'package.json', {'devDependencies': {'jest': '30.5.2'}})
    with pytest.raises(ValueError, match='matching dependency lockfile'):
        engine._check_base_dependencies(str(candidate), str(frozen), {'js'})
    for directory in (candidate, frozen):
        put(directory / 'package-lock.json', {})
        put(directory / 'package.json', {'workspaces': ['packages/*']})
    with pytest.raises(ValueError, match='does not support JS workspaces'):
        engine._check_base_dependencies(str(candidate), str(frozen), {'js'})


def test_actual_failing_frozen_base_cleans_worktree(tmp_path):
    repo(tmp_path)
    (tmp_path / 'test_app.py').write_text('def test_failure():\n    assert False\n')
    git(tmp_path, 'add', '-A')
    git(tmp_path, 'commit', '-qm', 'broken base')
    base = git(tmp_path, 'rev-parse', 'HEAD')
    (tmp_path / 'app.py').write_text('value = 3\n')
    before = git(tmp_path, 'worktree', 'list', '--porcelain')
    verdict = engine.check(str(tmp_path), base_ref=base, measure_base_coverage=True)
    assert not verdict.passed and 'coverage-base-unavailable' in rules(verdict)
    assert git(tmp_path, 'worktree', 'list', '--porcelain') == before


@pytest.mark.parametrize('candidate_py,candidate_js,passed', [(95, 50, True), (80, 50, False), (95, 40, False)])
def test_mixed_fresh_baselines_compare_each_language(monkeypatch, tmp_path, candidate_py, candidate_js, passed):
    base = repo(tmp_path)
    monkeypatch.setattr(engine, '_measure_base', lambda *a: TestResult(
        ran=True, passed=True, coverage=95, js_coverage=50, framework='pytest+vitest'))
    monkeypatch.setattr(runner, 'run_tests', lambda *a, **k: TestResult(
        ran=True, passed=True, coverage=candidate_py, js_coverage=candidate_js, framework='pytest+vitest'))
    verdict = engine.check(str(tmp_path), base_ref=base, measure_base_coverage=True)
    assert verdict.passed is passed, verdict.reasons
    assert verdict.coverage_baseline_measurements == {'python': 95, 'js': 50}
    assert build_envelope('fixture', verdict)['predicate']['coverage_baseline_measurements'] == {
        'python': 95, 'js': 50}
    if not passed:
        assert 'coverage-drop' in rules(verdict)


@pytest.mark.parametrize('directive', ['-r deps/runtime.txt', '--requirement=deps/runtime.txt',
                                       '-cdeps/runtime.txt', '--constraint deps/runtime.txt'])
def test_recursive_pip_inputs_are_compared(tmp_path, directive):
    roots = [tmp_path / 'candidate', tmp_path / 'base']
    for directory in roots:
        directory.mkdir()
        put(directory / 'requirements.txt', directive + '\n')
        put(directory / 'deps/runtime.txt', '-c deeper.txt\nsample-package==1.0\n')
        put(directory / 'deps/deeper.txt', 'constraint==1.0\n')
    engine._check_base_dependencies(*(str(root) for root in roots), {'python'})
    put(roots[0] / 'deps/deeper.txt', 'constraint==2.0\n')
    with pytest.raises(ValueError, match='dependency inputs differ'):
        engine._check_base_dependencies(*(str(root) for root in roots), {'python'})


@pytest.mark.parametrize('directive', ['-r ../outside.txt', '-r https://example.invalid/deps.txt',
                                       '-r requirements.txt', '-r $DEPENDENCY_FILE'])
def test_unsafe_or_unbounded_requirement_includes_block(tmp_path, directive):
    root = tmp_path / 'project'
    root.mkdir()
    put(root / 'requirements.txt', directive + '\n')
    put(tmp_path / 'outside.txt', 'sample==1.0\n')
    with pytest.raises(ValueError):
        engine._check_base_dependencies(str(root), str(root), {'python'})


@pytest.mark.parametrize('content', ['[project]\ndynamic=["dependencies"]\n',
                                     '[tool.uv.sources]\nfoo={path="local"}\n',
                                     '[project]\ndependencies=["foo @ file:local.whl"]\n'])
def test_dynamic_or_local_python_dependencies_block(tmp_path, content):
    put(tmp_path / 'pyproject.toml', content)
    with pytest.raises(ValueError, match='independent base provisioning'):
        engine._check_base_dependencies(str(tmp_path), str(tmp_path), {'python'})


def test_local_js_dependency_reference_blocks(tmp_path):
    put(tmp_path / 'package.json', {'dependencies': {'foo': 'file:vendor.tgz'}})
    put(tmp_path / 'package-lock.json', {})
    with pytest.raises(ValueError, match='local JS dependencies'):
        engine._check_base_dependencies(str(tmp_path), str(tmp_path), {'js'})


@pytest.mark.parametrize('dependency', ['pkg@./local', 'pkg@/absolute', 'pkg@local', 'localdir'])
@pytest.mark.parametrize('manifest', ['requirements.txt', 'pyproject.toml'])
def test_compact_local_python_references_are_rejected(tmp_path, dependency, manifest):
    (tmp_path / 'localdir').mkdir()
    content = (f'[project]\ndependencies=["{dependency}"]\n' if manifest == 'pyproject.toml'
               else dependency + '\n')
    put(tmp_path / manifest, content)
    if manifest == 'pyproject.toml' and dependency == 'localdir':
        # A bare project dependency name is a registry package, unlike a pip local directory.
        engine._check_base_dependencies(str(tmp_path), str(tmp_path), {'python'})
    else:
        with pytest.raises(ValueError, match='independent base provisioning'):
            engine._check_base_dependencies(str(tmp_path), str(tmp_path), {'python'})


@pytest.mark.parametrize('dependency', ['helper @ ./helper', 'helper@file:helper.whl',
                                      'helper@../helper', 'helper@/absolute'])
@pytest.mark.parametrize('included', [False, True])
def test_dependency_groups_cannot_reuse_candidate_local_helpers(tmp_path, dependency, included):
    content = f'[dependency-groups]\nhelpers=["{dependency}"]\n'
    if included:
        content += 'dev=[{include-group="helpers"}]\n'
    put(tmp_path / 'pyproject.toml', content)
    with pytest.raises(ValueError, match='independent base provisioning'):
        engine._check_base_dependencies(str(tmp_path), str(tmp_path), {'python'})


def test_registry_dependency_groups_and_normalized_includes_are_supported(tmp_path):
    put(tmp_path / 'pyproject.toml', '[dependency-groups]\nTest_Helpers=["pytest>=8"]\n'
        'dev=[{include-group="test-helpers"},"coverage==7.13.5"]\n')
    engine._check_base_dependencies(str(tmp_path), str(tmp_path), {'python'})


@pytest.mark.parametrize('groups', [
    'dev=[{include-group="missing"}]',
    'dev=[{include-group="helpers"}]\nhelpers=[{include-group="dev"}]',
    'dev=[{path="helper"}]',
    'dev="pytest"',
    'Test_Helpers=[]\ntest-helpers=[]',
])
def test_unbounded_or_malformed_dependency_groups_fail_closed(tmp_path, groups):
    put(tmp_path / 'pyproject.toml', '[dependency-groups]\n' + groups + '\n')
    with pytest.raises((ValueError, TypeError)):
        engine._check_base_dependencies(str(tmp_path), str(tmp_path), {'python'})


@pytest.mark.parametrize('dependency,allowed', [('hatchling>=1', True),
                                               ('helper @ ./local-helper', False),
                                               ('helper@file:helper.whl', False)])
def test_build_system_requirements_cannot_reuse_candidate_helpers(tmp_path, dependency, allowed):
    put(tmp_path / 'pyproject.toml', f'[build-system]\nrequires=["{dependency}"]\n'
        'build-backend="helper"\n')
    if allowed:
        engine._check_base_dependencies(str(tmp_path), str(tmp_path), {'python'})
    else:
        with pytest.raises(ValueError, match='independent base provisioning'):
            engine._check_base_dependencies(str(tmp_path), str(tmp_path), {'python'})
