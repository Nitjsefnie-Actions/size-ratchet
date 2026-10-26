#!/usr/bin/env python3
"""The file-size ratchet: measure, judge, tighten.

Every regular, non-binary tracked file that has a ceiling or a record is
measured in lines: the number of ``\\n`` bytes plus one when a non-empty
file does not end in ``\\n``. A file's ceiling is the first that applies of
an override (longest matching ``<folder>/`` prefix), a per-type ceiling, and
the built-in default; an untyped file has no ceiling and is never measured
unless it has a record or ``unclassified: fail`` asks for it. A recorded
number is never raised and no entry is ever added by hand.

Exit statuses: 0 pass, 1 violation, 2 configuration or environment failure."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import records


# Re-exported: the policy prices files by these, and callers reach them here.
from config import DEFAULT_CEILING, TYPES, Policy, type_of  # noqa: E402,F401

GROWTH_REMEDY = (
    'A recorded number is never raised by hand and no entry is added by '
    'hand: relocate the code into a new file, or shrink the file.')
SEED_REMEDY = (
    'Run the action in tighten mode on the proposed head and commit the '
    'records file it writes.')

STALE_REASONS = {
    'deleted': 'the file is gone from the tree',
    'excluded': 'the file is excluded from measurement',
    'graduated': 'the file is at or below its ceiling',
    'shrunk': 'the file shrank below its record',
}


def list_tree(root, revision):
    """[(mode, sha, path)] for the revision's tree, NUL-separated."""
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-tree', '-r', '-z', revision],
        capture_output=True, check=True, timeout=300)
    entries = []
    for raw in listed.stdout.split(b'\0'):
        if not raw:
            continue
        meta, _, path = raw.partition(b'\t')
        mode, otype, sha = meta.split()
        if otype == b'blob':
            entries.append((mode.decode(), sha.decode(),
                            path.decode('utf-8', 'surrogateescape')))
    return entries


REGULAR_MODES = ('100644', '100755')


def is_regular(mode):
    return mode in REGULAR_MODES


def is_binary(blob):
    """Git's own rule: a NUL byte in the first 8000 bytes is binary."""
    return b'\0' in blob[:8000]


def count_lines(blob):
    """``\\n`` count, plus one for a non-empty file without a final ``\\n``."""
    count = blob.count(b'\n')
    if blob and not blob.endswith(b'\n'):
        count += 1
    return count


def read_blobs(root, wanted):
    """Yield (path, bytes) for each (sha, path) in ``wanted``.

    One ``git cat-file --batch`` process reads every blob; git flushes
    after each request, so the reader never waits on output git has not
    produced.
    """
    if not wanted:
        return
    process = subprocess.Popen(
        ['git', '-C', str(root), 'cat-file', '--batch'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert process.stdin is not None and process.stdout is not None
    try:
        for sha, path in wanted:
            process.stdin.write(f'{sha}\n'.encode())
            process.stdin.flush()
            header = process.stdout.readline()
            if not header:
                raise OSError(f'git cat-file exited before answering {path}')
            parts = header.split()
            if len(parts) != 3 or parts[1] != b'blob':
                detail = header.decode(errors='replace').strip()
                raise OSError(f'cannot read {path}: {detail}')
            size = int(parts[2])
            blob = process.stdout.read(size)
            trailing = process.stdout.read(1)
            if len(blob) != size or trailing != b'\n':
                raise OSError(f'git cat-file gave a short read for {path}')
            yield path, blob
    finally:
        process.stdin.close()
        process.wait(timeout=60)


def measure(root, entries, policy, recorded, records_path=None):
    """{path: lines} for the files this run judges: typed and unexcluded,
    or recorded, or unexcluded under unclassified: fail. The records file
    itself is never judged: it is the gate's own data, not source.
    """
    recorded = set(recorded or ())
    unclassified = policy.unclassified == 'fail'
    judged = []
    seen = set()
    for mode, sha, path in entries:
        if not is_regular(mode) or policy.excluded(path) or path in seen:
            continue
        if records_path is not None and path == records_path:
            continue
        if type_of(path) is not None or path in recorded or unclassified:
            judged.append((sha, path))
            seen.add(path)
    sizes = {}
    for path, blob in read_blobs(root, judged):
        if is_binary(blob):
            continue
        sizes[path] = count_lines(blob)
    return sizes


def ceiling_violations(sizes, baseline, policy):
    """Unrecorded measured files above their ceiling; an untyped file has
    none, so it is reported by unclassified_violations instead."""
    violations = {}
    for path, count in sorted(sizes.items()):
        if path in baseline:
            continue
        limit = policy.ceiling(path)
        if limit is not None and count > limit:
            violations[path] = (count, limit)
    return violations


def unclassified_violations(sizes, baseline, policy):
    """Measured files with no ceiling and no record, under fail."""
    if policy.unclassified != 'fail':
        return []
    return [path for path in sorted(sizes)
            if path not in baseline and policy.ceiling(path) is None]


def _stale_detail(detail):
    """The stale sentence's second half, shaped by the state's detail."""
    if isinstance(detail, tuple):
        return f'recorded {detail[1]}, now {detail[0]}'
    return f'recorded {detail}'


def record_states(sizes, baseline, policy):
    """Per-record verdict: 'grown', a stale reason, or 'same'. An untyped
    file has no ceiling, so its record can shrink but never graduate."""
    states = {}
    for path, recorded in sorted(baseline.items()):
        if policy.excluded(path):
            states[path] = ('excluded', recorded)
            continue
        if path not in sizes:
            states[path] = ('deleted', recorded)
            continue
        count = sizes[path]
        if count > recorded:
            states[path] = ('grown', (count, recorded))
            continue
        ceiling = policy.ceiling(path)
        if ceiling is not None and count <= ceiling:
            states[path] = ('graduated', (count, ceiling))
        elif count < recorded:
            states[path] = ('shrunk', (count, recorded))
        else:
            states[path] = ('same', (count, recorded))
    return states


def tightened(baseline, sizes, policy):
    """The map a tighten writes, or ``None`` when nothing changes. A
    ``baseline`` of ``None`` seeds every typed, unexcluded file over its
    ceiling at its current count; otherwise each record lowers to its
    file's count, dropping deleted, excluded and graduated files, and an
    untyped file's record is kept, lowered."""
    if baseline is None:
        return {
            path: count
            for path, count in sorted(sizes.items())
            if policy.ceiling(path) is not None
            and count > policy.ceiling(path)
        }
    updated = {}
    for path, recorded in baseline.items():
        if policy.excluded(path):
            continue
        count = sizes.get(path)
        if count is None:
            continue
        ceiling = policy.ceiling(path)
        if ceiling is not None and count <= ceiling:
            continue
        updated[path] = min(count, recorded)
    if updated == baseline:
        return None
    return updated


def _diff_report(required, actual):
    lines = []
    for path in sorted(set(required) - set(actual)):
        lines.append(f'+ {path}: record {required[path]}')
    for path in sorted(set(actual) - set(required)):
        lines.append(f'- {path}: recorded {actual[path]}')
    for path in sorted(set(required) & set(actual)):
        if required[path] != actual[path]:
            lines.append(f'~ {path}: required {required[path]}, '
                         f'recorded {actual[path]}')
    return lines


def run(root, records_path, member_input, mode, base_ref, policy,
        stale, require_tight):
    """The gate over one checkout. Returns 0 or 1; raises on failure."""
    member = member_input or records.DEFAULT_MEMBER
    records.validate_path(root, records_path)
    entries = list_tree(root, 'HEAD')
    if mode == 'tighten':
        # Tighten rewrites the working-tree file, so it reads that file.
        head_map = records.read_document(root / records_path, member)
        return _tighten(root, records_path, member, entries, policy, head_map)
    # Check judges one revision: the tree and its records both from HEAD.
    head_map = records.read_map_at(root, 'HEAD', records_path, member)
    if mode == 'verify':
        return _verify(root, records_path, member, entries, policy, head_map)
    return _check(root, member, base_ref, entries, policy, head_map,
                  records_path, stale, require_tight)


def _tighten(root, records_path, member, entries, policy, head_map):
    recorded = head_map or {}
    sizes = measure(root, entries, policy, recorded, records_path)
    updated = tightened(head_map, sizes, policy)
    if updated is None:
        print('size-ratchet: nothing to tighten; the records file is unchanged')
        return 0
    if records.write_map(root, records_path, member, updated):
        seeded = len(set(updated) - set(recorded))
        lowered = sum(1 for path in updated
                      if path in recorded and updated[path] != recorded[path])
        print(f'size-ratchet: wrote {records_path} '
              f'({seeded} seeded, {lowered} lowered)')
    return 0


def _check(root, member, base_ref, entries, policy, head_map, records_path,
           stale, require_tight):
    failures = []
    if not base_ref:
        raise ValueError('input base-ref is empty and check needs it: pass '
                         'the revision whose records bind this check')
    base_map = records.read_map_at(root, base_ref, records_path, member)
    # Measure every path either side records; a head edit must not
    # unmeasure a file.
    recorded = set(base_map or {}) | set(head_map or {})
    sizes = measure(root, entries, policy, recorded, records_path)
    if base_map is None:
        return _check_seed(head_map, sizes, policy)
    # An exact head count for an unrecorded over-ceiling file is a seed
    # only while the base map is empty: against any other base the file
    # was bound by its ceiling, and a records edit cannot loosen that.
    seed_adds = {
        path for path, count in (head_map or {}).items()
        if not base_map
        and (limit := policy.ceiling(path)) is not None
        and sizes.get(path, 0) > limit
        and count == sizes.get(path)
    }
    for path in unclassified_violations(sizes, base_map, policy):
        failures.append(f'{path}: unclassified (no type and no record); '
                        'name a ceiling for it or exclude it')
    for path, (count, limit) in ceiling_violations(sizes, base_map, policy).items():
        if path in seed_adds:
            continue
        failures.append(f'{path}: {count} lines above its ceiling of {limit}')
    for path, state in record_states(sizes, base_map, policy).items():
        if state[0] == 'grown':
            count, recorded = state[1]
            failures.append(f'{path}: {count} lines above its record of {recorded}')
        elif state[0] == 'same':
            continue
        elif stale == 'fail':
            failures.append(f'{path}: stale ({STALE_REASONS[state[0]]}); '
                            f'{_stale_detail(state[1])}')
        else:
            print(f'::notice::{path}: stale ({STALE_REASONS[state[0]]}); '
                  f'{_stale_detail(state[1])}')
    if require_tight:
        head_entries = head_map or {}
        base_sizes = measure(root, list_tree(root, base_ref), policy,
                             base_map, records_path)
        touched = _changed_entries(base_sizes, sizes, base_map, head_entries)
        # An input-caused exclusion is the pull request's change; a
        # ceiling-raise graduation stays a notice (see the issue).
        touched |= {path for path, state in
                    record_states(sizes, base_map, policy).items()
                    if state[0] == 'excluded'}
        required = tightened(base_map, sizes, policy)
        required = dict(base_map) if required is None else dict(required)
        for path in seed_adds:
            required[path] = head_entries[path]
        scoped = {
            path for path in set(required) | set(head_entries)
            if path in touched
            and head_entries.get(path, _MISSING) != required.get(path, _MISSING)
        }
        if scoped:
            failures.append('require-tight: entries this pull request '
                            'changes must be tight, and these are not:')
            failures.extend('  ' + line for line in
                            _diff_report_subset(required, head_entries,
                                                scoped))
    if failures:
        print('size-ratchet: the size ratchet failed:', file=sys.stderr)
        for failure in failures:
            print(f'  {failure}', file=sys.stderr)
        print(GROWTH_REMEDY, file=sys.stderr)
        return 1
    print(f'size-ratchet: {len(sizes)} measured files within the size policy')
    return 0


def _changed_entries(base_sizes, sizes, base_map, head_map):
    """Entries whose file count or record moved between base and head."""
    missing = _MISSING
    paths = set(base_sizes) | set(sizes) | set(base_map or {}) \
        | set(head_map or {})
    return {
        path for path in paths
        if base_sizes.get(path, missing) != sizes.get(path, missing)
        or base_map.get(path, missing) != head_map.get(path, missing)
    }


# Distinct from every count, even an empty file's 0.
_MISSING = object()


def _diff_report_subset(required, actual, paths):
    """_diff_report restricted to ``paths``."""
    keep = {f'{path}: ' for path in paths}
    return [line for line in _diff_report(required, actual)
            if line[2:].startswith(tuple(keep))]


def _verify(root, records_path, member, entries, policy, head_map):
    """The default-branch run: records must already be tight, since
    tighten here would only write an uncommitted checkout."""
    if head_map is None:
        print('size-ratchet: no records on the default branch; '
              'nothing to verify')
        return 0
    recorded = set(head_map)
    sizes = measure(root, entries, policy, recorded, records_path)
    expected = tightened(head_map, sizes, policy)
    if expected is not None:
        print('size-ratchet: the size ratchet failed:', file=sys.stderr)
        print('  the records are loose; tighten over this tree writes:',
              file=sys.stderr)
        for line in _diff_report(expected, head_map):
            print(f'  {line}', file=sys.stderr)
        print('lower or drop each named entry in a pull request that '
              'touches the file, or commit the tightened records to the '
              'default branch', file=sys.stderr)
        return 1
    print(f'size-ratchet: records are tight over {len(sizes)} '
          'measured files')
    return 0


def _check_seed(head_map, sizes, policy):
    failures = unclassified_violations(sizes, head_map or {}, policy)
    if failures:
        print('size-ratchet: the size ratchet failed:', file=sys.stderr)
        for path in failures:
            print(f'  {path}: unclassified (no type and no record); name a '
                  'ceiling for it or exclude it', file=sys.stderr)
        return 1
    if head_map is None:
        print('size-ratchet: the size ratchet failed:', file=sys.stderr)
        print('  neither the base revision nor this head carries records; '
              'run tighten on the proposed head and commit the records '
              'file it writes', file=sys.stderr)
        print(SEED_REMEDY, file=sys.stderr)
        return 1
    expected = tightened(None, sizes, policy)
    if head_map != expected:
        print('size-ratchet: the size ratchet failed:', file=sys.stderr)
        print('  the base revision has no records, so this head must carry '
              'the exact seed tighten writes; differing entries:',
              file=sys.stderr)
        for line in _diff_report(expected, head_map):
            print(f'  {line}', file=sys.stderr)
        print(SEED_REMEDY, file=sys.stderr)
        return 1
    print(f'size-ratchet: seeded records verified over {len(sizes)} '
          'measured files')
    return 0
