#!/usr/bin/env python3
"""The action's inputs: parse, validate, and resolve them into a Policy.

Every input is judged here, before anything is measured, so the
validate-only pass the composite step runs first refuses a bad value up
front. A refusal is a ValueError naming the input (and the line, for the
multiline inputs); run.py turns it into exit 2. A value that would parse
but could never take effect -- a duplicate type, a folder no
repository-relative path can start with, a ceiling spelled `+7` or
`1_000` -- is refused too, because silently inert configuration reads as
a gate that is on.
"""
from __future__ import annotations

import re

TYPES = {
    'source': ('.py', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.sh', '.bash'),
    'stylesheet': ('.css', '.scss'),
    'sql': ('.sql',),
    'yaml': ('.yml', '.yaml'),
}
DEFAULT_CEILING = 500

MODES = ('auto', 'check', 'tighten')
STALE = ('warn', 'fail')
_DIGITS = re.compile(r'[0-9]+')


def type_of(rel):
    """The file's type by extension, or ``None`` when untyped."""
    for type_name, extensions in TYPES.items():
        if rel.endswith(extensions):
            return type_name
    return None


def _positive_int(text, input_name, line_no, raw_line):
    # Plain decimal digits only: int() also takes `+7`, `1_000` and
    # surrounding whitespace, spellings nobody writes meaning a ceiling.
    value = int(text) if _DIGITS.fullmatch(text) else 0
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
        if type_name in parsed:
            raise ValueError(f'input ceilings line {line_no} ({raw!r}): '
                             f'duplicate type {type_name!r}')
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
        if folder.startswith('/') or '..' in folder.split('/'):
            raise ValueError(f'{in_error}: folder {folder!r} is not a '
                             'repository-relative path prefix')
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


def choice(input_name, value, allowed):
    """A value from a closed set, or a refusal naming the input."""
    if value not in allowed:
        raise ValueError(f'input {input_name} must be '
                         f'{", ".join(allowed[:-1])} or {allowed[-1]}, got {value!r}')
    return value


def require_tight_input(text):
    """The boolean input accepts exactly 'true' or 'false'."""
    return choice('require-tight', text, ('true', 'false')) == 'true'
