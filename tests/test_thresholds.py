"""The thresholds ratchet: a raise stages byte-exact data, a refusal is loud."""
from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1] /
          '.github' / 'scripts' / 'ratchet_thresholds.py')

# The record's committed shape; every case copies it and mutates one member,
# so a refusal is attributable to that mutation, not the base shape.
RECORD = {
    'schema_version': 1,
    'coverage': {'python': {
        'floor': 94.7, 'measured': 96.2, 'cell': 'ubuntu-latest'}},
}

# Measured values that justify a raise: each clears the recorded 96.2 by
# more than the 1.5 hysteresis, so the raise arm's arithmetic is driven.
RAISES = ['better', 'decimal_raise']

# Measured values that must stage nothing: equal, lower, inside the
# hysteresis, one decimal step below its boundary, and exactly on it.
NO_RAISES = ['equal', 'lower', 'within_hysteresis', 'below_boundary',
             'boundary']

# Every refusal: malformed JSON, a non-numeric or non-finite --measured,
# a wrong/missing/boolean schema, a foreign cell, a missing member, and a
# record number that is a string, null, a boolean or non-finite.
REFUSALS = [
    'malformed',
    'measured:garbage', 'measured:NaN', 'measured:inf', 'measured:-inf',
    'schema', 'missing_schema', 'boolean_schema',
    'cell',
    'missing:floor', 'missing:measured', 'missing:cell',
    'invalid:floor:"96.2"', 'invalid:floor:null', 'invalid:floor:true',
    'invalid:floor:NaN', 'invalid:floor:Infinity',
    'invalid:measured:"96.2"', 'invalid:measured:null',
    'invalid:measured:true', 'invalid:measured:NaN',
    'invalid:measured:Infinity',
]


def run_case(case, scratch):
    record = json.loads(json.dumps(RECORD))
    thresholds = scratch / 'thresholds.json'
    out = scratch / 'not-created'
    measured = '97.8'
    expected_floor = Decimal('96.3')
    refused = False
    if case == 'equal':
        measured = '96.2'
    elif case == 'lower':
        measured = '95.9'
    elif case == 'within_hysteresis':
        measured = '97.6'
    elif case == 'below_boundary':
        measured = '97.69999999999999'
    elif case == 'boundary':
        measured = '97.7'
    elif case == 'decimal_raise':
        measured = '97.71'
        expected_floor = Decimal('96.21')
    elif case == 'malformed':
        refused = True
    elif case.startswith('measured:'):
        measured = case.split(':', 1)[1]
        refused = True
    elif case == 'schema':
        record['schema_version'] = 2
        refused = True
    elif case == 'missing_schema':
        del record['schema_version']
        refused = True
    elif case == 'boolean_schema':
        record['schema_version'] = True
        refused = True
    elif case == 'cell':
        record['coverage']['python']['cell'] = 'macos-latest'
        refused = True
    elif case.startswith('missing:'):
        del record['coverage']['python'][case.split(':', 1)[1]]
        refused = True
    elif case.startswith('invalid:'):
        field, value = case.split(':')[1:]
        record['coverage']['python'][field] = json.loads(value)
        refused = True
    elif case != 'better':
        raise AssertionError(f'unmodelled ratchet case: {case}')
    thresholds.write_text(
        '{broken' if case == 'malformed' else json.dumps(record))
    before = thresholds.read_bytes()
    done = subprocess.run(
        [sys.executable, '-I', str(SCRIPT),
         f'--measured={measured}', '--thresholds', str(thresholds),
         '--out-dir', str(out)],
        check=False, capture_output=True, text=True, timeout=60)
    assert thresholds.read_bytes() == before, \
        'the measuring script edited the input record'
    if refused:
        assert done.returncode != 0, f'{case}: invalid input accepted: {done.stdout}'
        assert str(thresholds) in done.stderr, \
            f'{case}: refusal did not name {thresholds}'
        assert not out.exists(), f'{case}: refusal staged data'
    else:
        assert done.returncode == 0, f'{case}: exit {done.returncode}: {done.stderr}'
        assert not done.stderr, done.stderr
        assert len(done.stdout.splitlines()) == 1, 'expected one decision line'
        if case in RAISES:
            assert out.is_dir(), 'gain above the hysteresis staged no record'
            assert sorted(p.name for p in out.iterdir()) == ['ci-thresholds.json']
            staged = out / 'ci-thresholds.json'
            assert json.loads(staged.read_text(), parse_float=Decimal) == {
                'schema_version': 1,
                'coverage': {'python': {
                    'floor': expected_floor,
                    'measured': Decimal(measured),
                    'cell': 'ubuntu-latest'}},
            }, 'the calibration gap, exact measurement or record metadata is wrong'
            assert staged.read_text() == """{
  "schema_version": 1,
  "coverage": {
    "python": {
      "floor": FLOOR,
      "measured": MEASURED,
      "cell": "ubuntu-latest"
    }
  }
}
""".replace('FLOOR', str(expected_floor)).replace('MEASURED', measured), \
                'staged JSON does not use the committed two-space layout'
        else:
            assert not out.exists(), \
                f'{case}: no raise justified, but the out-dir was created'


@pytest.mark.parametrize('case', RAISES)
def test_a_raise_above_hysteresis_stages_the_calibration(case, tmp_path):
    run_case(case, tmp_path)


@pytest.mark.parametrize('case', NO_RAISES)
def test_a_measurement_within_the_hysteresis_stages_nothing(case, tmp_path):
    run_case(case, tmp_path)


@pytest.mark.parametrize('case', REFUSALS)
def test_an_invalid_input_refuses_naming_the_record_and_stages_nothing(case, tmp_path):
    run_case(case, tmp_path)
