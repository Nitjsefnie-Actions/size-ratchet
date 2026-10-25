#!/usr/bin/env python3
"""Stage a higher Python coverage calibration for the separate ratchet push.

A missing artifact means no raise is justified, so invalid measurements and
records must refuse loudly instead of looking like an ordinary no-op. Only the
ubuntu-latest record is measured here; another schema or cell needs a deliberate
change to the measuring job before it can produce data for the privileged push.
The input record stays untouched, and a lower measurement never loosens it.
"""
import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys


CALIBRATION_GAP = Decimal('1.5')
RAISE_HYSTERESIS = Decimal('1.5')


def _measurement(value):
    """Read a finite measurement with decimal arithmetic, excluding booleans."""
    if isinstance(value, bool):
        raise ValueError('measured must be a finite number')
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('measured must be a finite number') from None
    if not result.is_finite():
        raise ValueError('measured must be a finite number')
    return result


def record_number(value, field):
    """Read a finite JSON number, excluding booleans and numeric strings."""
    if type(value) not in (int, float):
        raise ValueError(f'{field} must be a finite number')
    return _measurement(value)


def stage(measured, thresholds, out_dir):
    """Stage a raise only above the recorded measurement's hysteresis."""
    record = json.loads(thresholds.read_text(encoding='utf-8'))
    if type(record['schema_version']) is not int or record['schema_version'] != 1:
        raise ValueError('schema_version must be exactly 1')
    python = record['coverage']['python']
    if python['cell'] != 'ubuntu-latest':
        raise ValueError('coverage.python.cell must be exactly "ubuntu-latest"')
    floor = record_number(python['floor'], 'coverage.python.floor')
    recorded_measured = record_number(python['measured'], 'coverage.python.measured')
    measured = _measurement(measured)
    if measured - recorded_measured > RAISE_HYSTERESIS:
        python['floor'] = measured - CALIBRATION_GAP
        python['measured'] = measured
        payload = json.dumps(record, indent=2, allow_nan=False, default=float) + '\n'
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / 'ci-thresholds.json').write_text(
            payload, encoding='utf-8')
        print(f'Coverage floor: {floor} -> {python["floor"]}')
    else:
        print(f'No raise justified by this run: {measured} <= '
              f'{recorded_measured} + {RAISE_HYSTERESIS}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--measured', required=True)
    parser.add_argument('--thresholds', required=True, type=Path)
    parser.add_argument('--out-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        stage(args.measured, args.thresholds, args.out_dir)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        print(f'{args.thresholds}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
