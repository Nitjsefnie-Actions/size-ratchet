#!/usr/bin/env python3
"""How much of what THIS change added to the action's own modules is
covered, and what it missed.

The floor next door (.github/ci-thresholds.json, enforced by the tests
workflow's Coverage gate) reports one number for the whole file, and that
number barely moves: a pull request can add fifty uncovered lines to a
thousand-line script and the total falls by a fraction of a point, which is
inside the buffer the floor already allows. So the tree-level figure cannot
tell a reviewer whether the code in front of them was tested -- only whether
the file as a whole still is.

This reports the other number: of the lines this change ADDED that coverage
considers executable, how many did the suite reach. It is deliberately NOT a
gate. A refactor that moves code around, a change that only deletes, and a fix
whose test lives in an unmeasured helper all produce a low patch figure for
reasons a reviewer should judge rather than a threshold should block on.

Lines the diff added that coverage.py does not consider executable are
excluded: blank lines, comments, docstrings and `else:` are not measured
statements, and counting them would make the percentage depend on formatting.
For any changed Python source the XML does measure, every added executable
statement must have an XML record; an absent record is an invalid report,
never a smaller denominator.

The diff body is decoded with replacement characters, never refused: the
diff is git-generated, a byte git renders is not garbage this reporter may
refuse, and only line numbers reach the report, so unreadable content cannot
move the number. A path whose bytes are not UTF-8 is still a one-line
refusal -- a path this reporter cannot name is a report it cannot write
honestly.
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from coverage import Coverage
from coverage.exceptions import CoverageException
from coverage.files import GlobMatcher, canonical_filename, prep_patterns

# `+++ b/path`, with git's optional quoting and the /dev/null of a deletion.
_TARGET = re.compile(r'^\+\+\+ (.*)$')
# `@@ -old,count +new,count @@`; the counts are optional and mean 1.
_HUNK = re.compile(r'^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def _decode_git_path(value):
    """Decode a quoted Git path and remove its diff-side prefix."""
    if not value.startswith('"'):
        value = value.split('\t', 1)[0]
        return value[2:] if value.startswith('b/') else value
    escaped = value[1:-1] if value.endswith('"') else value[1:]
    escapes = {
        '\\': b'\\', '"': b'"', 'a': b'\a', 'b': b'\b',
        'f': b'\f', 'n': b'\n', 'r': b'\r', 't': b'\t',
        'v': b'\v',
    }
    decoded = bytearray()
    index = 0
    while index < len(escaped):
        char = escaped[index]
        if char != '\\':
            decoded.extend(char.encode('utf-8'))
            index += 1
            continue
        octal = escaped[index + 1:index + 4]
        if len(octal) == 3 and all(item in '01234567' for item in octal):
            decoded.append(int(octal, 8))
            index += 4
            continue
        if index + 1 == len(escaped):
            decoded.extend(b'\\')
            index += 1
            continue
        escaped_char = escaped[index + 1]
        decoded.extend(escapes.get(escaped_char,
                                   escaped_char.encode('utf-8')))
        index += 2
    value = decoded.decode('utf-8')
    return value[2:] if value.startswith('b/') else value


def executable_lines(coverage_xml):
    """Return {path: {line number: times hit}} from a Cobertura report."""
    root = ET.parse(coverage_xml).getroot()
    if root.tag != 'coverage':
        raise ValueError('root is not <coverage>')
    measured = {}
    usable = False
    for class_node in root.iter('class'):
        filename = class_node.get('filename')
        if not filename:
            continue
        lines = measured.setdefault(filename, {})
        for line_node in class_node.iter('line'):
            number = line_node.get('number')
            hits = line_node.get('hits')
            if number is None:
                raise ValueError(f'missing line number for {filename}')
            try:
                number_value = int(number)
            except ValueError as error:
                raise ValueError(
                    f'invalid line number for {filename}: {number!r}'
                ) from error
            if number_value <= 0:
                raise ValueError(
                    f'invalid line number for {filename}: {number!r} '
                    '(must be positive)')
            if hits is None:
                raise ValueError(
                    f'missing hits for {filename}:{number_value}')
            try:
                hits_value = int(hits)
            except ValueError as error:
                raise ValueError(
                    f'invalid hits for {filename}:{number_value}: '
                    f'{hits!r}') from error
            # A file can appear as more than one <class>; take the best hit
            # count so a line reached by any of them counts as covered.
            lines[number_value] = max(lines.get(number_value, 0), hits_value)
            usable = True
    if not usable:
        raise ValueError('no usable line entries')
    return measured


def added_lines(diff_text):
    """Return {path: {line numbers this diff adds}} from a unified diff."""
    added = {}
    path = None
    line_number = 0
    in_hunk = False
    old_remaining = new_remaining = 0
    for line in diff_text.split('\n'):
        header = line.removesuffix('\r')
        if line.startswith('Binary files '):
            raise ValueError(
                f'binary diff record is not measurable: {line}')
        # `--- ` counts as a file header only outside a hunk. Git renders a
        # REMOVED line whose content begins `-- ` as `--- ...`, and taking
        # that for a header clears the path and silently drops every later
        # hunk of the file. The `+++` match below is guarded the same way.
        if header.startswith('diff --git ') or (
                not in_hunk and header.startswith('--- ')):
            path = None
            in_hunk = False
            continue
        target = _TARGET.match(header) if not in_hunk else None
        if target is not None:
            name = _decode_git_path(target.group(1))
            path = None if name == '/dev/null' else name
            continue
        hunk = _HUNK.match(header)
        if hunk is not None:
            old_remaining = int(hunk.group(1) or 1)
            line_number = int(hunk.group(2))
            new_remaining = int(hunk.group(3) or 1)
            in_hunk = bool(old_remaining or new_remaining)
            continue
        if path is None or not in_hunk:
            continue
        if line.startswith('+'):
            added.setdefault(path, set()).add(line_number)
            line_number += 1
            new_remaining -= 1
        elif line.startswith('-'):
            old_remaining -= 1
        elif line.startswith(' ') or line == '':
            line_number += 1
            old_remaining -= 1
            new_remaining -= 1
        # A `-` line exists only in the old file and moves nothing.
        if old_remaining == 0 and new_remaining == 0:
            in_hunk = False
    return added


def _analyzer():
    """A coverage.py reader for config and statement analysis, not for data.

    `analysis2` opens -- and creates -- the configured coverage DATA FILE, and
    this reporter reads no coverage data: it asks the analyzer which lines are
    executable statements and borrows the repository config's omit patterns. So
    it is given `data_file=None`, coverage.py's no-disk data, and no open can
    collide with the file the measuring collector already holds (issue 1471).
    That is all it buys: `_init_data` still runs `ensure_dir_for_file` on the
    configured path before it consults `_no_disk`, so a named path under
    directories that do not exist has those created.
    """
    return Coverage(config_file=True, data_file=None)


def validate_statement_records(measured, added):
    """Reject a measured source whose added statements are absent from XML.

    Absence is a hard error: it must never silently remove an executable line
    from the denominator and turn incomplete measurement into flattering news.
    """
    analyzer = _analyzer()
    for path in sorted(set(measured) & set(added)):
        _source, statements, _excluded, _missing, _formatted = (
            analyzer.analysis2(path))
        required = set(statements) & added[path]
        absent = required.difference(measured[path])
        if absent:
            raise ValueError(
                f'missing executable statement records for {path}: '
                f'{_ranges(absent)}')


def _ranges(numbers):
    """Collapse sorted line numbers into `3`, `5-9` spans for readability."""
    spans = []
    for number in sorted(numbers):
        if spans and number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ', '.join(str(low) if low == high else f'{low}-{high}'
                     for low, high in spans)


def measure(measured, added):
    """Return (per-file rows, covered, total) over the added statements."""
    rows = []
    covered = total = 0
    for path in sorted(added):
        lines = measured.get(path)
        if not lines:
            # Not a file this report measures -- a workflow, a fixture, a
            # Markdown file. Absent is not the same as uncovered.
            continue
        touched = sorted(number for number in added[path] if number in lines)
        if not touched:
            continue
        missed = [number for number in touched if lines[number] == 0]
        rows.append((path, len(touched) - len(missed), len(touched), missed))
        covered += len(touched) - len(missed)
        total += len(touched)
    return rows, covered, total


def _measured_source(path):
    """Whether the coverage report is expected to name this changed path.

    the measured-source universe is the action's own modules: `.coveragerc`'s
    `[report] include` scopes the report to `run.py` and `scripts/ci/*.py`,
    so a changed path anywhere else -- the suite, the workflows, the docs --
    is not measured source, and its absence from the report says nothing.
    """
    return path == 'run.py' or path.startswith('scripts/ci/')


def unmeasured_sources(measured, added):
    """Return changed source paths the report never named.

    A systematic path-spelling mismatch -- the report naming an absolute
    checkout path where the diff says `run.py` -- is otherwise
    indistinguishable from a change confined to files coverage does not
    measure. Returning every absent path matters when one changed source file
    is measured and another is not: a boolean all-or-nothing guard would hide
    the latter.
    """
    config = _analyzer()
    patterns = [
        pattern
        for option in ('run:omit', 'report:omit')
        for pattern in config.get_option(option) or []]
    omitted = GlobMatcher(prep_patterns(patterns))
    return {path for path in added
            if _measured_source(path) and path not in measured
            and not omitted.match(canonical_filename(path))}


def render(rows, covered, total, unmeasured=()):
    """Render the markdown comment body for one run."""
    unmeasured = set(unmeasured)
    out = ['### Coverage of this change', '']
    if total == 0 and unmeasured:
        out.append('This change added lines to the action\'s own modules '
                   '(run.py, scripts/ci/*.py), the files the coverage '
                   'report measures, but the report names none of the '
                   'changed paths. Nothing here was measured, which is not '
                   'the same as nothing needing to be: the report most '
                   'likely spells paths differently than the diff does.')
        out.append('')
        out.append('Unmeasured changed source files:')
        out.extend(f'- `{path}`' for path in sorted(unmeasured))
    elif total == 0:
        # Said in full rather than as "0 lines": the common case here is a
        # change that only touches the suite or the workflows, which
        # `.coveragerc`'s `[report] include` leaves out of the report, and a
        # bare "nothing added" reads like the tool failed to find the diff.
        out.append('No **measured** lines were added. The report measures '
                   'only the action\'s own modules (`run.py`, `scripts/ci/'
                   '*.py`; `[report] include` in `.coveragerc`), so a change '
                   'confined to the suite, the workflows or any other file '
                   'has no patch coverage to report -- that is not the same '
                   'as none of it running.')
    else:
        percent = 100.0 * covered / total
        if covered < total:
            percent = min(percent, 99.9)
        subject = 'measured added lines' if unmeasured else 'added lines'
        out.append(f'**{percent:.1f}%** of {subject} covered '
                   f'({covered}/{total}).')
        out.append('')
        out.append('| File | Covered | Added | Missed lines |')
        out.append('| --- | ---: | ---: | --- |')
        for path, file_covered, file_total, missed in rows:
            detail = _ranges(missed) if missed else '—'
            out.append(
                f'| `{path}` | {file_covered} | {file_total} | {detail} |')
        if unmeasured:
            out.append('')
            out.append('The coverage report did not measure these changed '
                       'source files:')
            out.extend(f'- `{path}`' for path in sorted(unmeasured))
        if covered == total:
            out.append('')
            if unmeasured:
                out.append('Every measured added line was reached; the '
                           'files above were not measured.')
            else:
                out.append('Every added line was reached.')
    return '\n'.join(out) + '\n'


def main():
    """Read the coverage report and a diff; print the markdown body."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage', required=True,
                        help='Cobertura XML written by `coverage xml`')
    parser.add_argument('--diff', default='-',
                        help='unified diff to read, or - for stdin')
    args = parser.parse_args()

    diff_bytes = (sys.stdin.buffer.read() if args.diff == '-'
                  else Path(args.diff).read_bytes())
    # Replacement, not refusal: the diff is git-generated, and the workflow
    # diffs with --text, so a tracked file's bytes are rendered here
    # verbatim -- a byte git renders is not garbage this reporter may
    # refuse, and one unreadable line in an unrelated file must not fail a
    # legitimate tree's report (the branch-debt fixture round shipped a
    # fixture with raw non-UTF-8 bytes and the live run red: 37100400229).
    # Nothing but line numbers crosses into the report, and counting is
    # byte-independent, so replacement characters in unreadable content
    # cannot move the number. The quoted-path decode below stays strict and
    # guarded: a path this reporter cannot name is a report it cannot write
    # honestly.
    diff_text = diff_bytes.decode('utf-8', errors='replace')
    try:
        # Inside the guard with its sibling: a git-quoted path whose bytes
        # are not UTF-8 raises UnicodeDecodeError, a ValueError subclass,
        # and outside it that died as a traceback.
        measured = executable_lines(args.coverage)
        added = added_lines(diff_text)
        validate_statement_records(measured, added)
    except (CoverageException, ET.ParseError, ValueError) as error:
        print(f'coverage report invalid: {error}', file=sys.stderr)
        return 1
    rows, covered, total = measure(measured, added)
    sys.stdout.write(render(rows, covered, total,
                            unmeasured_sources(measured, added)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
