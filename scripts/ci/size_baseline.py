#!/usr/bin/env python3
"""The file-size ratchet: measure, judge, tighten.

Every regular, non-binary tracked file that has a ceiling or a record is
measured in lines: the number of ``\\n`` bytes plus one when a non-empty
file does not end in ``\\n``. A file's ceiling is the first that applies of
an override (longest matching ``<folder>/`` prefix), a per-type ceiling, and
the built-in default; an untyped file has no ceiling and is never measured
unless it has a record or ``unclassified: fail`` asks for it. A recorded
number is never raised and no entry is ever added by hand: growth is
answered by relocating code into a new file, or by shrinking the file.

Exit statuses: 0 pass, 1 violation, 2 configuration or environment failure.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import records


TYPES = {
    'source': ('.py', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.sh', '.bash'),
    'stylesheet': ('.css', '.scss'),
    'sql': ('.sql',),
    'yaml': ('.yml', '.yaml'),
}
DEFAULT_CEILING = 500

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


def type_of(rel):
    """The file's type by extension, or ``None`` when untyped."""
    for type_name, extensions in TYPES.items():
        if rel.endswith(extensions):
            return type_name
    return None


def _positive_int(text, input_name, line_no, raw_line):
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise ValueError(
            f'input {input_name} line {line_no} ({raw_line!r}): '
            f'ceiling {text!r} is not a positive integer')
    return value


def parse_ceilings(text):
    """``<type>: <ceiling>`` lines; unknown type or a bad ceiling fails."""
    parsed = {}
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        type_name, sep, rest = line.partition(':')
        if not sep:
            raise ValueError(f'input ceilings line {line_no} ({raw!r}): '
                             'expected "<type>: <ceiling>"')
        type_name = type_name.strip()
        if type_name not in TYPES:
            raise ValueError(f'input ceilings line {line_no} ({raw!r}): '
                             f'unknown type {type_name!r} '
                             f'(known: {", ".join(sorted(TYPES))})')
        parsed[type_name] = _positive_int(rest.strip(), 'ceilings', line_no, raw)
    return parsed


def parse_overrides(text):
    """``<folder>/: <ceiling>`` lines; the folder must end in ``/``."""
    parsed = {}
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        folder, sep, rest = line.partition(':')
        if not sep:
            raise ValueError(f'input overrides line {line_no} ({raw!r}): '
                             'expected "<folder>/: <ceiling>"')
        folder = folder.strip()
        in_error = (f'input overrides line {line_no} ({raw!r})')
        if not folder.endswith('/'):
            raise ValueError(f'{in_error}: folder {folder!r} has no trailing /')
        if any(char in folder for char in '*?['):
            raise ValueError(f'{in_error}: folder {folder!r} carries a '
                             'wildcard; a folder is a literal path prefix')
        if folder in parsed:
            raise ValueError(f'{in_error}: duplicate folder {folder!r}')
        parsed[folder] = _positive_int(rest.strip(), 'overrides', line_no,
                                       f'{folder}: {rest}')
    return parsed


def parse_exclude(text):
    """Exact paths or ``<folder>/`` prefixes, one per non-blank line."""
    return [stripped for raw in text.splitlines()
            if (stripped := raw.strip())]


def check_policy(unclassified):
    if unclassified not in ('ignore', 'fail'):
        raise ValueError(f'input unclassified must be ignore or fail, '
                         f'got {unclassified!r}')


class Policy:
    """Resolved ceilings, overrides and exclusions for one run."""

    def __init__(self, ceilings_text='', overrides_text='', exclude_text='',
                 unclassified='ignore'):
        check_policy(unclassified)
        self.ceilings = parse_ceilings(ceilings_text)
        self.overrides = parse_overrides(overrides_text)
        self.exclude = parse_exclude(exclude_text)
        self.unclassified = unclassified
        self.prefixes = sorted(self.overrides, key=len, reverse=True)

    def excluded(self, rel):
        for entry in self.exclude:
            if entry.endswith('/'):
                if rel.startswith(entry):
                    return True
            elif rel == entry:
                return True
        return False

    def ceiling(self, rel):
        """The first ceiling that applies: override, per-type, default."""
        if type_of(rel) is None:
            return None
        for prefix in self.prefixes:
            if rel.startswith(prefix):
                return self.overrides[prefix]
        return self.ceilings.get(type_of(rel), DEFAULT_CEILING)


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

    One ``git cat-file --batch`` process reads every blob. Without
    ``--buffer``, git flushes after each request, so the reader never waits
    on output git has not produced. A blob git cannot produce raises naming
    the path: an unreadable file fails the run.
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
    """{path: lines} for the files this run judges.

    A file is judged when it is typed and unexcluded, or recorded, or the
    run fails unclassified files and it is unexcluded. Binary files, and
    files git does not store as regular blobs, are never judged. The
    records file itself is never judged: it is the gate's own data, not
    source, and flagging it unclassified would fail every consumer.
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
    """Unrecorded measured files above their ceiling.

    An untyped file has no ceiling, so it can never be over one; it is
    reported by unclassified_violations instead.
    """
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
    """Per-record verdict: 'grown' (a violation) or a stale reason.

    An untyped file has no ceiling, so its record can shrink but never
    graduate; graduation needs a ceiling to fall to.
    """
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
    """The map a tighten writes, or ``None`` when it would write nothing.

    ``baseline`` of ``None`` means no records: seeding records every typed,
    unexcluded file over its ceiling at its current count. Otherwise every
    record lowers to the file's current count, drops when its file is
    deleted, excluded or graduated (at or below the ceiling), and is kept,
    lowered, when its file has no ceiling. A record is never raised and no
    entry is ever added except when seeding.
    """
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
    head_map = records.read_document(root / records_path, member)
    if mode == 'tighten':
        return _tighten(root, records_path, member, entries, policy, head_map)
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
    if stale not in ('warn', 'fail'):
        raise ValueError(f'input stale must be warn or fail, got {stale!r}')
    base_map = records.read_map_at(root, base_ref, records_path, member)
    sizes = measure(root, entries, policy, head_map or {}, records_path)
    if base_map is None:
        return _check_seed(head_map, sizes, policy)
    for path in unclassified_violations(sizes, base_map, policy):
        failures.append(f'{path}: unclassified (no type and no record); '
                        'name a ceiling for it or exclude it')
    for path, (count, limit) in ceiling_violations(sizes, base_map, policy).items():
        failures.append(f'{path}: {count} lines above its ceiling of {limit}')
    for path, state in record_states(sizes, base_map, policy).items():
        if state[0] == 'grown':
            count, recorded = state[1]
            failures.append(f'{path}: {count} lines above its record of {recorded}')
        elif stale == 'fail':
            failures.append(f'{path}: stale ({STALE_REASONS[state[0]]}); '
                            f'{_stale_detail(state[1])}')
        else:
            print(f'::notice::{path}: stale ({STALE_REASONS[state[0]]}); '
                  f'{_stale_detail(state[1])}')
    if require_tight:
        required = tightened(base_map, sizes, policy)
        if required is None:
            required = base_map
        actual = head_map or {}
        if actual != required:
            failures.append('require-tight: the head records differ from '
                            'what tighten writes over this tree:')
            failures.extend('  ' + line for line in
                            _diff_report(required, actual))
    if failures:
        print('size-ratchet: the size ratchet failed:', file=sys.stderr)
        for failure in failures:
            print(f'  {failure}', file=sys.stderr)
        print(GROWTH_REMEDY, file=sys.stderr)
        return 1
    print(f'size-ratchet: {len(sizes)} measured files within the size policy')
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
