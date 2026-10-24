#!/usr/bin/env python3
"""Read and write the size ratchet's records file.

The records file holds ``{"schema_version": 1, "baseline": {path: lines}}``.
The ``member`` input moves the baseline map to another top-level key; every
other key of the document is then preserved byte-for-byte on write. The
writer therefore never re-serializes the document: it locates the member's
value span in the raw bytes with the scanner below and splices the new map
into place, leaving the surrounding bytes untouched. Reading walks the same
scanner and validates each member's value with ``json.loads``, so the file
is read and written by one structural parser.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

DEFAULT_MEMBER = 'baseline'

_WHITESPACE = ' \t\n\r'


def _error(path, detail):
    return ValueError(f'records file {path}: {detail}')


def _skip_whitespace(text, pos):
    while pos < len(text) and text[pos] in _WHITESPACE:
        pos += 1
    return pos


def _scan_string(text, pos):
    """Return the end of the JSON string starting at ``pos`` (at ``"``)."""
    pos += 1
    while pos < len(text):
        char = text[pos]
        if char == '\\':
            pos += 2
            continue
        if char == '"':
            return pos + 1
        pos += 1
    raise ValueError('unfinished JSON string')


def _scan_value(text, pos):
    """Return the end of the JSON value starting at ``pos``."""
    char = text[pos]
    if char == '"':
        return _scan_string(text, pos)
    if char in '{[':
        open_char = char
        close_char = '}' if char == '{' else ']'
        pos = _skip_whitespace(text, pos + 1)
        while True:
            if text[pos] == close_char:
                return pos + 1
            if open_char == '{':
                if text[pos] != '"':
                    raise ValueError('expected a JSON string key')
                pos = _scan_string(text, pos)
            else:
                pos = _scan_value(text, pos)
            pos = _skip_whitespace(text, pos)
            if text[pos] == ':':
                pos = _skip_whitespace(text, pos + 1)
                pos = _scan_value(text, pos)
                pos = _skip_whitespace(text, pos)
            if text[pos] == ',':
                pos = _skip_whitespace(text, pos + 1)
            elif text[pos] == close_char:
                return pos + 1
            else:
                raise ValueError('malformed JSON member list')
    end = pos
    while end < len(text) and text[end] not in _WHITESPACE and text[end] not in ',]}':
        end += 1
    if end == pos:
        raise ValueError('expected a JSON value')
    return end


def scan_members(text):
    """Return (members, end) for the top-level object of ``text``.

    ``members`` is a list of (key, value_start, value_end) spans; the spans
    exclude surrounding whitespace so a splice replaces exactly the old
    value's bytes. ``end`` is just past the closing brace.
    """
    pos = _skip_whitespace(text, 0)
    if pos >= len(text) or text[pos] != '{':
        raise ValueError('the document is not a JSON object')
    members = []
    pos = _skip_whitespace(text, pos + 1)
    if pos < len(text) and text[pos] == '}':
        return members, pos + 1
    while True:
        if pos >= len(text) or text[pos] != '"':
            raise ValueError('expected a JSON string key')
        key_start = pos
        key_end = _scan_string(text, pos)
        key = json.loads(text[key_start:key_end])
        pos = _skip_whitespace(text, key_end)
        if pos >= len(text) or text[pos] != ':':
            raise ValueError('expected ":" after a JSON key')
        value_start = _skip_whitespace(text, pos + 1)
        value_end = _scan_value(text, value_start)
        members.append((key, value_start, value_end))
        pos = _skip_whitespace(text, value_end)
        if pos < len(text) and text[pos] == ',':
            pos = _skip_whitespace(text, pos + 1)
            continue
        if pos < len(text) and text[pos] == '}':
            return members, pos + 1
        raise ValueError('malformed JSON member list')


def _member_value(text, members, key):
    for name, value_start, value_end in members:
        if name == key:
            return text[value_start:value_end]
    return None


def _parse_map(path, value_text, role):
    value = json.loads(value_text)
    if not isinstance(value, dict):
        raise _error(path, f'the {role} value is not a JSON object')
    for key, count in value.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise _error(path, f'{role} entry {key!r} is not a line count')
    return value


def read_document(path: Path, member: str):
    """Return the baseline map from the records file at ``path``.

    ``None`` means no records: the file is absent, or the member key is
    absent. An empty map is records. Every parse failure names the file.
    """
    if not path.exists():
        return None
    text = path.read_bytes().decode('utf-8')
    return parse_document(text, path, member)


def parse_document(text: str, display_path, member: str):
    """Parse a records document from text; ``None`` when the key is absent.

    Both the structural scan and every member's ``json.loads`` failure are
    wrapped with the file's name, so a corrupted records file fails naming
    it instead of a bare parser message.
    """
    try:
        members, _ = scan_members(text)
        values = {}
        for key, value_start, value_end in members:
            values[key] = json.loads(text[value_start:value_end])
    except ValueError as failure:
        raise _error(display_path, str(failure)) from failure
    if 'schema_version' in values and values['schema_version'] != 1:
        raise _error(display_path,
                     f'unsupported schema_version {values["schema_version"]!r}')
    if member not in values:
        return None
    return _parse_map(display_path, _member_value(text, members, member),
                      'baseline')


def read_map_at(root: Path, revision: str, records: str, member: str):
    """Read the baseline map from ``revision``'s copy of the records file.

    ``None`` means no records at that revision: the file or the key is
    absent there. An invalid revision or any other git failure raises.
    """
    checked = subprocess_run(
        ['git', '-C', str(root), 'rev-parse', '--verify', '--quiet',
         f'{revision}^{{commit}}'])
    if checked.returncode != 0:
        raise ValueError(f'base-ref {revision!r} is not a commit this '
                         'checkout can resolve; fetch it and pass its SHA')
    exists = subprocess_run(
        ['git', '-C', str(root), 'cat-file', '-e', '--', f'{revision}:{records}'])
    if exists.returncode != 0:
        return None
    shown = subprocess_run(
        ['git', '-C', str(root), 'cat-file', 'blob', '--', f'{revision}:{records}'])
    if shown.returncode != 0:
        raise ValueError(f'cannot read the records file at {revision}: '
                         f'{shown.stderr.decode(errors="replace").strip()}')
    return parse_document(shown.stdout.decode('utf-8'),
                          f'{revision}:{records}', member)


def _reindent(value_json, key_indent):
    """Shift each continuation line right by ``key_indent``, relative to its
    own indent — the writer's base indent of two stays relative to the key."""
    out = []
    for index, line in enumerate(value_json.split('\n')):
        if index == 0:
            out.append(line)
            continue
        stripped = line.lstrip(' ')
        original = len(line) - len(stripped)
        out.append(' ' * (original + key_indent) + stripped)
    return '\n'.join(out)


def splice(text: str, member: str, value_json: str) -> str:
    """Return ``text`` with the member's value replaced by ``value_json``.

    A member the document lacks is inserted first. Every byte outside the
    replaced value, and every byte of every other member, is carried through
    unchanged. The new value's continuation lines are indented to the
    member's own nesting, so a write lands in the document's shape and a
    second identical write is a byte-identical no-op.
    """
    members, end = scan_members(text)
    for key, value_start, value_end in members:
        if key == member:
            # The value shares its line with the key, so the key's own
            # indent is the line's leading whitespace.
            line_start = text.rfind('\n', 0, value_start) + 1
            line_prefix = text[line_start:value_start]
            key_indent = len(line_prefix) - len(line_prefix.lstrip(' '))
            return (text[:value_start] + _reindent(value_json, key_indent)
                    + text[value_end:])
    body = _reindent(value_json, 2)
    head = text[:1]
    rest = text[1:end - 1]
    tail = text[end - 1:]
    if not rest.strip():
        return head + f'\n  "{member}": {body}\n' + tail
    return head + f'\n  "{member}": {body},' + rest + tail


def serialize_map(baseline) -> str:
    """The deterministic spelling the writer gives the baseline map."""
    return json.dumps(baseline, indent=2, sort_keys=True)


def document_text(member: str, baseline) -> str:
    """The full document for an absent records file."""
    body = serialize_map(baseline).replace('\n', '\n  ')
    return ('{\n'
            f'  "schema_version": 1,\n'
            f'  "{member}": {body}\n'
            '}\n')


def write_map(root: Path, records: str, member: str, baseline) -> bool:
    """Write ``baseline`` as the member value of the records file.

    Returns whether anything was written. An existing file keeps every byte
    outside the member's value; an absent file is created from
    ``document_text``. Written as bytes, so newline translation cannot move
    a byte.
    """
    path = root / records
    if path.exists():
        text = path.read_bytes().decode('utf-8')
        try:
            updated = splice(text, member, serialize_map(baseline))
        except ValueError as failure:
            raise _error(path, str(failure)) from failure
        changed = updated != text
    else:
        updated = document_text(member, baseline)
        changed = True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(updated.encode('utf-8'))
    return changed


def validate_path(root: Path, records: str) -> None:
    """Refuse a records path that is empty, absolute or leaves the tree."""
    if not records:
        raise ValueError('input records is empty')
    if records.startswith('/'):
        raise ValueError(f'input records {records!r} is not repository-relative')
    resolved = (root / records).resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError(f'input records {records!r} escapes the checkout')


def subprocess_run(argv):
    """One git call, captured; the caller reads the status it asked for."""
    return subprocess.run(argv, capture_output=True, timeout=300)
