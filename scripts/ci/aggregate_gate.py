#!/usr/bin/env python3
"""Fold the tests workflow's leg results into one aggregate verdict.

The aggregate is the one check a ruleset can require: it must always
report, including after a failed or cancelled ancestor. This tree has no
classification skips, so every leg must reach `success` -- a skipped,
cancelled, failed or missing leg folds red, and the fold says which leg
and what it did instead of succeeding.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    raw = os.environ.get('SIZE_RATCHET_NEEDS_JSON', '')
    if not raw:
        print('aggregate: SIZE_RATCHET_NEEDS_JSON is empty; the needs '
              'context did not reach this step', file=sys.stderr)
        return 1
    needs = json.loads(raw)
    if not isinstance(needs, dict) or not needs:
        print('aggregate: the needs context is empty; nothing to fold',
              file=sys.stderr)
        return 1
    failed = []
    print('| leg | result |')
    print('| --- | --- |')
    for name, detail in sorted(needs.items()):
        result = detail.get('result') if isinstance(detail, dict) else None
        print(f'| {name} | {result} |')
        if result != 'success':
            failed.append((name, result))
    if failed:
        # stdout is block-buffered under a runner; flush the table first so
        # the log reads in order.
        sys.stdout.flush()
        print('aggregate: the gate is red:', file=sys.stderr)
        for name, result in failed:
            print(f'  {name}: {result}', file=sys.stderr)
        return 1
    print(f'aggregate: all {len(needs)} legs succeeded')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
