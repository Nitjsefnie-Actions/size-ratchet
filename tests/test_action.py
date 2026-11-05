"""Pins on the composite action's own interface in action.yml.

These are the action's inputs and its env-passing discipline — security and
consistency invariants over the file this repository ships, not pins on a
dependency's version or a workflow's `with:` inputs.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ACTION_ROOT = Path(__file__).resolve().parents[1]


def action():
    return yaml.safe_load((ACTION_ROOT / 'action.yml').read_text())


def step(name='Measure and apply the ratchet'):
    steps = action()['runs']['steps']
    named = [item for item in steps if item.get('name') == name]
    assert len(named) == 1
    return named[0]


PUSH_STEP = 'Push the tightened records'


def test_every_input_has_a_default():
    inputs = action()['inputs']
    expected = {
        'ceilings', 'overrides', 'exclude', 'unclassified', 'records',
        'member', 'mode', 'base-ref', 'stale', 'require-tight', 'push-key',
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
    # Exactly three steps: setup-python (whatever release Dependabot pins),
    # the runner and the push, run: steps that install nothing.
    assert len(steps) == 3
    assert steps[0]['uses'].startswith('actions/setup-python@')
    for item in steps[1:]:
        assert 'uses' not in item and 'run' in item
        for forbidden in ('pip install', 'npm ', 'venv', 'curl', 'wget'):
            assert forbidden not in item['run'], forbidden


def test_validate_only_judges_the_inputs_alone(tmp_path):
    # A manual run has no event context; the validate-only pass the action
    # runs first must not depend on one.
    import os
    import subprocess
    import sys
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GITHUB_', 'SIZE_RATCHET_'))}
    env['GITHUB_WORKSPACE'] = str(tmp_path)
    done = subprocess.run(
        [sys.executable, '-I', str(ACTION_ROOT / 'run.py'), '--validate-only'],
        env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_the_composite_uses_a_bash_shell():
    assert step()['shell'] == step(PUSH_STEP)['shell'] == 'bash'
