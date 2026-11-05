#!/usr/bin/env python3
"""Tree budgets: a ratchet on the total line count under a folder."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import records
import size_baseline
from config import TREES_MEMBER

FEATURE_SUBJECT = re.compile(r'feat(\([^)]*\))?:')
RAISE_REMEDY = (
    'Only a feature change may raise a tree budget: a commit subject in '
    'base-ref..HEAD starting feat: or feat(<scope>):. Any other change '
    'trims its own lines under that folder.')
TREE_REMEDY = (
    "Tighten writes each listed folder's budget at its total, never above "
    'its record. A higher budget needs a feature commit in the range; any '
    'other change trims its own lines under the folder.')


def totals(root, revision, folders, policy, records_path):
    """{folder: total lines} over ``revision``'s tree."""
    sums = dict.fromkeys(folders, 0)
    prefixes = tuple(folders)
    entries = size_baseline.list_tree(root, revision)
    wanted = [(sha, path) for mode, sha, path in entries
              if size_baseline.is_regular(mode) and path != records_path
              and not policy.excluded(path) and path.startswith(prefixes)]
    for path, blob in size_baseline.read_blobs(root, wanted):
        if size_baseline.is_binary(blob):
            continue
        lines = size_baseline.count_lines(blob)
        # A file under nested listed folders counts toward each of them.
        for folder in folders:
            if path.startswith(folder):
                sums[folder] += lines
    return sums


def tightened(recorded, sums):
    """Each listed folder at min(record, total); unlisted ones dropped."""
    recorded = recorded or {}
    return {folder: min(total, recorded.get(folder, total))
            for folder, total in sums.items()}


def has_feature_commit(root, base_ref):
    """A shallow or unreadable history raises: it proves neither answer."""
    shallow = records.subprocess_run(
        ['git', '-C', str(root), 'rev-parse', '--is-shallow-repository'])
    if shallow.returncode != 0 or shallow.stdout.strip() != b'false':
        raise ValueError('cannot read the feature history for a raised tree '
                         'budget: the repository is shallow or unreadable; '
                         'check out with fetch-depth: 0')
    log = records.subprocess_run(
        ['git', '-C', str(root), 'log', '--format=%s', f'{base_ref}..HEAD'])
    if log.returncode != 0:
        raise ValueError(f'cannot read the feature history {base_ref}..HEAD: '
                         f'{log.stderr.decode(errors="replace").strip()}')
    # Git separates subjects with LF; any other separator is subject text.
    subjects = log.stdout.decode('utf-8', 'replace').split('\n')
    return any(FEATURE_SUBJECT.match(subject) for subject in subjects)


def run(root, records_path, folders, mode, base_ref, policy, require_tight):
    """The tree half of the gate: 0 or 1."""
    sums = totals(root, 'HEAD', folders, policy, records_path)
    if mode == 'tighten':
        recorded = records.read_document(root / records_path, TREES_MEMBER)
        return _tighten(root, records_path, recorded, sums)
    head = records.read_map_at(root, 'HEAD', records_path, TREES_MEMBER)
    if mode == 'verify':
        return _verify(head, sums, require_tight)
    base = records.read_map_at(root, base_ref, records_path, TREES_MEMBER) or {}
    base_sums = (totals(root, base_ref, folders, policy, records_path)
                 if require_tight else {})
    return _report(_check(root, base_ref, base, head or {}, sums, base_sums,
                          require_tight), sums)


def _tighten(root, records_path, recorded, sums):
    updated = tightened(recorded, sums)
    if updated == recorded:
        print('size-ratchet: tree budgets are unchanged')
        return 0
    records.write_map(root, records_path, TREES_MEMBER, updated)
    print(f'size-ratchet: wrote the tree budgets to {records_path}: '
          + ', '.join(f'{folder} {total}' for folder, total in updated.items()))
    return 0


def _check(root, base_ref, base, head, sums, base_sums, require_tight):
    failures = []
    raised = []
    for folder, total in sums.items():
        record = head.get(folder)
        shown = 'no record' if record is None else f'record {record}'
        if folder not in base:
            if record != total:
                failures.append(
                    f'{folder}: the base has no record, so the head must '
                    f'carry the seed tighten writes: total {total}, {shown}')
            continue
        if record is None:
            failures.append(f'{folder}: the head drops the record '
                            f'(base {base[folder]}); total {total}')
            continue
        if total > record:
            failures.append(f'{folder}: {total} lines above its record of '
                            f'{record}')
        if record > base[folder]:
            raised.append(folder)
        moved = (base_sums.get(folder) != total or base[folder] != record)
        if require_tight and moved and record != total:
            failures.append(f'require-tight: {folder}: total {total} moved '
                            f'or its record did, so it must record exactly '
                            f'{total}; recorded {record}')
    # Read lazily: a pull request that raises no budget needs no history.
    if raised and not has_feature_commit(root, base_ref):
        failures.extend(
            f'{folder}: total {sums[folder]}, record raised from '
            f'{base[folder]} to {head[folder]} without a feature commit. '
            + RAISE_REMEDY for folder in raised)
    return failures


def _report(failures, sums):
    if failures:
        print('size-ratchet: the tree budget failed:', file=sys.stderr)
        for failure in failures:
            print(f'  {failure}', file=sys.stderr)
        print(TREE_REMEDY, file=sys.stderr)
        return 1
    print(f'size-ratchet: {len(sums)} tree budgets hold')
    return 0


def _verify(head, sums, require_tight):
    """Over budget fails; any other drift from tighten is loose."""
    head = head or {}
    over = [f'{folder}: {total} lines above its record of {head[folder]}'
            for folder, total in sums.items()
            if folder in head and total > head[folder]]
    loose = size_baseline.diff_report(tightened(head, sums), head)
    if loose and not require_tight:
        for line in loose:
            print(f'::notice::loose tree budget: {line}')
    elif loose:
        over.append('the tree budgets are loose; tighten over this tree '
                    'writes:')
        over.extend('  ' + line for line in loose)
    return _report(over, sums)
