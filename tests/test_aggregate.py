"""The aggregate fold: every leg must succeed, and nothing else is green."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'ci' / 'aggregate_gate.py'


def fold(needs):
    env = dict(os.environ)
    env.pop('SIZE_RATCHET_NEEDS_JSON', None)
    if needs is not None:
        env['SIZE_RATCHET_NEEDS_JSON'] = needs if isinstance(needs, str) else json.dumps(needs)
    return subprocess.run([sys.executable, '-I', str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=60)


def legs(**results):
    return {name.replace('_', '-'): {'result': result, 'outputs': {}}
            for name, result in results.items()}


def test_every_leg_green_passes():
    done = fold(legs(suites='success', shellcheck='success', size_baseline='success'))
    assert done.returncode == 0, done.stderr
    assert 'all 3 legs succeeded' in done.stdout


@pytest.mark.parametrize('result', ['failure', 'cancelled', 'skipped', None])
def test_any_other_leg_result_folds_red_naming_the_leg(result):
    done = fold(legs(suites='success', shellcheck='success', size_baseline=result))
    assert done.returncode == 1
    assert f'size-baseline: {result}' in done.stderr


@pytest.mark.parametrize('needs', [None, '', '{}'])
def test_an_absent_or_empty_needs_context_is_red(needs):
    done = fold(needs)
    assert done.returncode == 1
    assert 'aggregate:' in done.stderr
