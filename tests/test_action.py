"""Pins on the composite action's own interface in action.yml.

These are the action's inputs and its env-passing discipline — security and
consistency invariants over the file this repository ships, not pins on a
dependency's version or a workflow's `with:` inputs.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from tests._block_harness import MEASURE, PUSH, run_action
from tests.helpers import make_repo, put_records

ACTION_ROOT = Path(__file__).resolve().parents[1]


def action():
    return yaml.safe_load((ACTION_ROOT / 'action.yml').read_text())


def step(name='Measure and apply the ratchet'):
    steps = action()['runs']['steps']
    named = [item for item in steps if item.get('name') == name]
    assert len(named) == 1
    return named[0]


PUSH_STEP = 'Push the tightened records'
INSTALL_STEP = 'Install Python'
WAIT_STEP = 'Wait before repeating the Python install'
REPEAT_STEP = 'Repeat the Python install'
FALLBACK_STEP = "Fall back to the runner's Python"


def test_every_input_has_a_default():
    inputs = action()['inputs']
    expected = {
        'ceilings', 'overrides', 'exclude', 'unclassified', 'records',
        'member', 'mode', 'base-ref', 'stale', 'require-tight', 'push-key',
        'tree-budgets',
    }
    assert set(inputs) == expected
    for name, spec in inputs.items():
        assert 'default' in spec, f'input {name} has no default'
        assert not spec.get('required'), f'input {name} is required'


def test_base_ref_defaults_to_the_pull_request_base():
    assert action()['inputs']['base-ref']['default'] == \
        '${{ github.event.pull_request.base.sha }}'


def test_inputs_reach_the_runner_through_env_only():
    env = step()['env']
    inputs = set(action()['inputs']) - {'push-key'}
    for name in inputs:
        assert env[f'SIZE_RATCHET_{name.upper().replace("-", "_")}'] == \
            f'${{{{ inputs.{name} }}}}', name
    assert set(env) == {f'SIZE_RATCHET_{name.upper().replace("-", "_")}'
                        for name in inputs} | {
        'SIZE_RATCHET_DEFAULT_BRANCH', 'SIZE_RATCHET_PUSH_KEY_SET',
        'SIZE_RATCHET_CALLED'}
    assert env['SIZE_RATCHET_DEFAULT_BRANCH'] == \
        '${{ github.event.repository.default_branch }}'
    assert env['SIZE_RATCHET_PUSH_KEY_SET'] == "${{ inputs.push-key != '' }}"
    for name in ('Measure and apply the ratchet', PUSH_STEP):
        assert '${{' not in step(name)['run'], name


def test_the_key_reaches_the_push_step_alone():
    # Security invariant over every step of the action: only the push
    # step's environment carries the key.
    carriers = [item.get('name') for item in action()['runs']['steps']
                if 'inputs.push-key }}' in str(item)]
    assert carriers == [PUSH_STEP]
    push = step(PUSH_STEP)
    assert push['env'] == {
        'SIZE_RATCHET_PUSH_KEY': '${{ inputs.push-key }}',
        'SIZE_RATCHET_RECORDS': '${{ inputs.records }}',
        'SIZE_RATCHET_DEFAULT_BRANCH':
            '${{ github.event.repository.default_branch }}'}
    assert push['if'] == "steps.measure.outputs.push == 'true'"
    assert step()['id'] == 'measure'
    assert push['run'].strip() == \
        'python3 -I "$GITHUB_ACTION_PATH/run.py" --push'


def test_the_runner_runs_isolated_and_validates_first():
    run_text = step()['run']
    lines = [line for line in run_text.splitlines() if line.strip()]
    assert lines == [
        'python3 -I "$GITHUB_ACTION_PATH/run.py" --validate-only',
        'python3 -I "$GITHUB_ACTION_PATH/run.py"',
    ]


def test_the_action_installs_nothing():
    steps = action()['runs']['steps']
    # Exactly six steps: setup-python (whatever release Dependabot pins)
    # and its repeat, then the wait, the fallback, the runner and the push,
    # run: steps that install nothing.
    assert len(steps) == 6
    fetched = [item for item in steps if 'uses' in item]
    assert [item['name'] for item in fetched] == [INSTALL_STEP, REPEAT_STEP]
    for item in fetched:
        assert item['uses'].startswith('actions/setup-python@')
    for item in steps:
        if item in fetched:
            continue
        assert 'run' in item
        for forbidden in ('pip install', 'npm ', 'venv', 'curl', 'wget'):
            assert forbidden not in item['run'], forbidden


def test_the_install_is_repeated_before_anything_needs_python():
    assert [item['name'] for item in action()['runs']['steps']] == [
        INSTALL_STEP, WAIT_STEP, REPEAT_STEP, FALLBACK_STEP,
        'Measure and apply the ratchet', PUSH_STEP]


def run_with_installs(tmp_path, outcomes, python=True):
    """The action over a repository with one recorded file, each install
    taking its given outcome, on a PATH of the bare tools a runner has:
    `sleep` is a stub recording its argument, `python3` is optional."""
    repo = make_repo(tmp_path, branch='main', files={'src/x.py': 'a\n' * 600})
    base = put_records(repo, {'src/x.py': 600})
    stubs = tmp_path / 'stubs'
    stubs.mkdir()
    slept = tmp_path / 'slept'
    sleep = stubs / 'sleep'
    sleep.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$1" >> "{slept}"\n')
    sleep.chmod(0o755)
    for tool in ('bash', 'git'):
        (stubs / tool).symlink_to(shutil.which(tool))
    if python:
        (stubs / 'python3').symlink_to(Path(sys.executable).resolve())
    github = {'event_name': 'pull_request', 'ref': 'refs/pull/1/merge',
              'sha': base, 'workflow': 'size baseline',
              'event': {'pull_request': {'base': {'sha': base}},
                        'repository': {'default_branch': 'main'}}}
    done = run_action(repo, {}, github, install_outcomes=outcomes,
                      extra_env={'PATH': str(stubs)})
    return done, slept


def test_a_first_install_that_works_neither_waits_nor_falls_back(tmp_path):
    done, slept = run_with_installs(tmp_path, ('success',))
    assert WAIT_STEP not in done and FALLBACK_STEP not in done
    assert not slept.exists()
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr


def test_a_repeat_that_works_waits_once_and_does_not_fall_back(tmp_path):
    done, slept = run_with_installs(tmp_path, ('failure', 'success'))
    assert slept.read_text() == '60\n'
    assert FALLBACK_STEP not in done
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr


def test_an_outage_outlasting_both_installs_measures_on_the_runner_python(
        tmp_path):
    done, slept = run_with_installs(tmp_path, ('failure', 'failure'))
    assert slept.read_text() == '60\n'
    assert done[FALLBACK_STEP].returncode == 0, done[FALLBACK_STEP].stderr
    version = subprocess.run(
        [sys.executable, '--version'],
        capture_output=True, text=True, check=True).stdout.strip()
    warning = done[FALLBACK_STEP].stdout
    assert warning.startswith('::warning::') and version in warning
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr


def test_a_runner_with_no_python_fails_at_the_fallback(tmp_path):
    done, _ = run_with_installs(tmp_path, ('failure', 'failure'), python=False)
    assert done[FALLBACK_STEP].returncode == 127
    assert 'python3' in done[FALLBACK_STEP].stderr
    assert MEASURE not in done and PUSH not in done


def test_the_repeat_installs_what_the_first_attempt_would():
    # A consistency check between the two steps, whatever they pin: a bump
    # that moves one leaves a repeat installing a different interpreter.
    first, repeat = step(INSTALL_STEP), step(REPEAT_STEP)
    assert repeat['uses'] == first['uses']
    assert repeat['with'] == first['with']


def test_validate_only_judges_the_inputs_alone(tmp_path):
    # A manual run has no event context; the validate-only pass the action
    # runs first must not depend on one.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GITHUB_', 'SIZE_RATCHET_'))}
    env['GITHUB_WORKSPACE'] = str(tmp_path)
    done = subprocess.run(
        [sys.executable, '-I', str(ACTION_ROOT / 'run.py'), '--validate-only'],
        env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_the_composite_uses_a_bash_shell():
    for item in action()['runs']['steps']:
        if 'run' in item:
            assert item['shell'] == 'bash', item['name']
