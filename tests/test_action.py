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


def step():
    steps = action()['runs']['steps']
    named = [item for item in steps if item.get('name') == 'Measure and apply the ratchet']
    assert len(named) == 1
    return named[0]


def test_every_input_has_a_default():
    inputs = action()['inputs']
    expected = {
        'ceilings', 'overrides', 'exclude', 'unclassified', 'records',
        'member', 'mode', 'base-ref', 'stale', 'require-tight',
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
    inputs = action()['inputs']
    for name in inputs:
        assert f'SIZE_RATCHET_{name.upper().replace("-", "_")}' in env, name
    for name, value in env.items():
        if name.startswith('SIZE_RATCHET_'):
            assert value.startswith('${{ inputs.') or \
                value == '${{ github.event.repository.default_branch }}', \
                f'env {name} passes neither an input nor the event context'
    run_text = step()['run']
    assert '${{ inputs.' not in run_text
    assert '${{' not in run_text


def test_the_runner_runs_isolated_and_validates_first():
    run_text = step()['run']
    lines = [line for line in run_text.splitlines() if line.strip()]
    assert lines == [
        'python3 -I "$GITHUB_ACTION_PATH/run.py" --validate-only',
        'python3 -I "$GITHUB_ACTION_PATH/run.py"',
    ]


def test_the_action_installs_nothing():
    run_text = step()['run']
    for forbidden in ('pip install', 'npm ', 'npm ci', 'venv', 'curl', 'wget'):
        assert forbidden not in run_text, forbidden
    uses = [one_step.get('uses') for one_step in action()['runs']['steps']]
    assert uses == ['actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97',
                    None]  # the runner step is a run:, not a uses:


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
    assert step()['shell'] == 'bash'
