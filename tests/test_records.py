"""Tests for the records-file reader and the byte-preserving writer."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'ci'))
import records  # noqa: E402


FOREIGN = (
    '{\n'
    '  "schema_version": 1,\n'
    '  "note": "kept for the coverage floor",\n'
    '  "sizes": {\n'
    '    "a.py": 501\n'
    '  },\n'
    '  "trailing": [1, 2]\n'
    '}\n'
)


def test_splice_replaces_exactly_the_member_value():
    updated = records.splice(FOREIGN, 'sizes',
                             records.serialize_map({'a.py': 480}))
    assert updated == ('{\n  "schema_version": 1,\n'
                       '  "note": "kept for the coverage floor",\n'
                       '  "sizes": {\n    "a.py": 480\n  },\n'
                       '  "trailing": [1, 2]\n}\n')


def test_splice_preserves_every_other_byte():
    updated = records.splice(FOREIGN, 'sizes', '{"a.py": 480}')
    # Bytes before the member key and after the member value are identical.
    assert FOREIGN[:FOREIGN.index('"sizes"')] == updated[:updated.index('"sizes"')]
    assert FOREIGN[FOREIGN.index('"trailing"'):] == \
        updated[updated.index('"trailing"'):]


def test_splice_inserts_into_an_empty_object():
    updated = records.splice('{}', 'baseline', '{}')
    assert updated == '{\n  "baseline": {}\n}'


def test_splice_inserts_beside_existing_members():
    updated = records.splice('{\n  "schema_version": 1\n}\n', 'baseline',
                             records.serialize_map({'a.py': 501}))
    assert updated == ('{\n  "baseline": {\n    "a.py": 501\n  },'
                       '\n  "schema_version": 1\n}\n')


def test_serialize_is_sorted_and_indented():
    assert records.serialize_map({'b.py': 2, 'a.py': 1}) == \
        '{\n  "a.py": 1,\n  "b.py": 2\n}'


def test_document_text_places_the_member_after_schema_version():
    text = records.document_text('baseline', {'a.py': 501})
    assert text == ('{\n  "schema_version": 1,\n  "baseline": {\n'
                    '    "a.py": 501\n  }\n}\n')


def test_round_trip_write_is_deterministic(tmp_path):
    path = tmp_path / 'size-baseline.json'
    assert records.write_map(tmp_path, 'size-baseline.json', 'baseline',
                             {'a.py': 501})
    records.write_map(tmp_path, 'size-baseline.json', 'baseline',
                      {'a.py': 501})
    stable = path.read_bytes()
    # The writer's own spelling is a fixed point: a third identical write
    # changes nothing.
    assert not records.write_map(tmp_path, 'size-baseline.json', 'baseline',
                                 {'a.py': 501})
    assert path.read_bytes() == stable
    records.write_map(tmp_path, 'size-baseline.json', 'baseline', {'a.py': 480})
    assert path.read_text() == ('{\n  "schema_version": 1,\n  "baseline": {\n'
                                '    "a.py": 480\n  }\n}\n')


def test_read_document_absent_file_and_absent_key_are_no_records(tmp_path):
    assert records.read_document(tmp_path / 'absent.json', 'baseline') is None
    path = tmp_path / 'doc.json'
    path.write_text('{"schema_version": 1}\n')
    assert records.read_document(path, 'baseline') is None


def test_read_document_empty_map_is_records(tmp_path):
    path = tmp_path / 'doc.json'
    path.write_text('{"schema_version": 1, "baseline": {}}\n')
    assert records.read_document(path, 'baseline') == {}


def test_read_document_member_moves_the_map(tmp_path):
    path = tmp_path / 'doc.json'
    path.write_text(FOREIGN)
    assert records.read_document(path, 'sizes') == {'a.py': 501}
    assert records.read_document(path, 'baseline') is None


def test_read_document_rejects_a_foreign_schema_version(tmp_path):
    path = tmp_path / 'doc.json'
    path.write_text('{"schema_version": 2, "baseline": {}}\n')
    with pytest.raises(ValueError, match='schema_version'):
        records.read_document(path, 'baseline')


def test_read_document_rejects_non_line_counts(tmp_path):
    path = tmp_path / 'doc.json'
    path.write_text('{"schema_version": 1, "baseline": {"a.py": true}}\n')
    with pytest.raises(ValueError, match='not a line count'):
        records.read_document(path, 'baseline')
    path.write_text('{"schema_version": 1, "baseline": {"a.py": -1}}\n')
    with pytest.raises(ValueError, match='not a line count'):
        records.read_document(path, 'baseline')


def test_read_map_at_reads_the_named_revision(tmp_path):
    from tests.helpers import make_repo, write_files, git, head
    repo = make_repo(tmp_path, {'a.py': 'x\n'})
    base = head(repo)
    write_files(repo, {'.github/size-baseline.json':
                       '{"schema_version": 1, "baseline": {"a.py": 5}}\n'})
    git(repo, 'add', '.github/size-baseline.json')
    git(repo, 'commit', '-qm', 'records')
    records_path = '.github/size-baseline.json'
    assert records.read_map_at(repo, base, records_path, 'baseline') is None
    assert records.read_map_at(repo, 'HEAD', records_path, 'baseline') == \
        {'a.py': 5}
    assert records.read_map_at(repo, 'HEAD', records_path, 'other') is None


def test_read_map_at_rejects_an_unresolvable_revision(tmp_path):
    from tests.helpers import make_repo
    repo = make_repo(tmp_path, {'a.py': 'x\n'})
    with pytest.raises(ValueError, match='base-ref'):
        records.read_map_at(repo, 'nosuchrevision',
                            '.github/size-baseline.json', 'baseline')


def test_validate_path_refuses_empty_absolute_and_escaping(tmp_path):
    with pytest.raises(ValueError, match='empty'):
        records.validate_path(tmp_path, '')
    with pytest.raises(ValueError, match='repository-relative'):
        records.validate_path(tmp_path, '/etc/passwd')
    with pytest.raises(ValueError, match='escapes'):
        records.validate_path(tmp_path, 'a/../../etc/x.json')


def test_scan_refuses_malformed_documents():
    with pytest.raises(ValueError):
        records.scan_members('[]')
    with pytest.raises(ValueError):
        records.scan_members('{"a": }')
    with pytest.raises(ValueError):
        records.scan_members('{"a": 1,}')
