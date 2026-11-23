#!/usr/bin/env python3
"""The check's require-tight 'false' arm: a base entry binds the head as
tighten over the base tree would, whether or not the push run committed it."""
from __future__ import annotations

UNCLASSIFIED = ('{}: unclassified (no type and no record); name a ceiling '
                'for it or exclude it')
RESTORE_REMEDY = (
    'Putting back what this pull request dropped or lowered raises nothing '
    'by hand: restore each number shown, or exclude the file or shrink it '
    'to the lower of the head record (its ceiling for a dropped entry) and '
    'the bound named above, and commit what tighten then writes; tighten '
    "never drops an untyped file's record.")


def bounds(base_map, base_sizes, sizes, policy):
    """({path: [failure]}, {path: cap}); a None cap marks a file the base
    tree does not measure."""
    held, caps = {}, {}
    for path, recorded in sorted(base_map.items()):
        count, base, ceiling = sizes.get(path), base_sizes.get(path), \
            policy.ceiling(path)
        if count is None:
            continue
        failures = []
        if base is None and ceiling is None:
            bound, kind = recorded, 'record'
            if policy.unclassified == 'fail':
                failures.append(UNCLASSIFIED.format(path))
        elif base is None:
            bound = min(recorded, ceiling)
            kind = 'record' if recorded <= ceiling else 'ceiling'
        else:
            bound = min(recorded, base)
            kind = 'record' if recorded <= base else 'base count'
        if count > bound:
            failures.insert(0, f'{path}: {count} lines above its {kind} of '
                            f'{bound}' + _why(kind, recorded, base, ceiling))
        if failures:
            held[path], caps[path] = failures, None if base is None else bound
    return held, caps


def _why(kind, recorded, base, ceiling):
    if kind == 'ceiling':
        return ('; the base tree measures no file here, so tighten drops '
                f'its base record of {recorded}')
    if kind == 'record':
        return ''
    if ceiling is None or base > ceiling:
        return f'; tighten lowers its base record of {recorded} to it'
    return (f'; the base record of {recorded} is one tighten drops, the file '
            'being at or below its ceiling in the base tree, and until the '
            "push run's tighten commit or a merged pull request drops it the "
            'file may not grow past its base count')


def cap(required, caps):
    """Show a held entry's bound, or no entry, for a higher one."""
    for path, bound in caps.items():
        if bound is None:
            required.pop(path, None)
        elif path in required:
            required[path] = min(required[path], bound)


def groups(head_entries, base_map, sizes, required, seed_adds):
    """Raised, added, and lowered or dropped past tighten: each binds every
    later pull request."""
    return [('records this pull request raises over the base (keep each at '
             'or below its base record; drop an entry the base lacks), '
             'beside what tighten writes:',
             {path for path, count in head_entries.items()
              if path not in seed_adds
              and (path not in base_map or count > base_map[path])}),
            ('records this pull request lowers below both its base record '
             'and its file, or drops while tighten keeps it, beside what '
             'tighten writes:',
             (set(required) - set(head_entries))
             | {path for path, count in head_entries.items()
                if path in base_map and path in sizes
                and count < min(base_map[path], sizes[path])})]
