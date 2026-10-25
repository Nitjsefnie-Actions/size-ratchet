"""End-to-end tests: the gate run as a subprocess, as the action runs it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'ci'))
import records  # noqa: E402

from tests.helpers import add_and_commit, git, head, make_repo, run_gate  # noqa: E402

BIG = '\n'.join(f'x{i} = {i}' for i in range(501)) + '\n'  # 501 lines
OVER = '\n'.join(f'y{i} = {i}' for i in range(600)) + '\n'
RECORDS = '.github/size-baseline.json'


def put_records(repo, baseline, member='baseline'):
    """Commit a records file holding ``baseline`` (a seed for the tree)."""
    text = records.document_text(member, baseline)
    add_and_commit(repo, RECORDS, text, 'carry the records seed')
    return head(repo)


def test_a_clean_tree_passes(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 400})
    base = put_records(repo, {})
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 0, done.stderr
    assert 'within the size policy' in done.stdout


def test_growth_fails_naming_path_lines_and_record(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 480})
    base = put_records(repo, {'src/a.py': 480})
    add_and_commit(repo, 'src/a.py', 'x\n' * 495, 'grow the file')
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 1
    assert '495 lines above its record of 480' in done.stderr
    assert 'never raised by hand' in done.stderr


def test_an_unrecorded_file_over_its_ceiling_fails(tmp_path):
    repo = make_repo(tmp_path, {'src/big.py': BIG})
    base = put_records(repo, {})
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 1
    assert 'src/big.py: 501 lines above its ceiling of 500' in done.stderr


def test_an_override_ceiling_relaxes_one_folder(tmp_path):
    repo = make_repo(tmp_path, {'tests/big.py': BIG})
    base = put_records(repo, {})
    done = run_gate(repo, 'check', base_ref=base,
                    overrides='tests/: 700\n')
    assert done.returncode == 0, done.stderr


def test_excluded_files_neither_measured_nor_unclassified(tmp_path):
    repo = make_repo(tmp_path, {'src/big.py': BIG, 'docs/README.md': OVER})
    base = put_records(repo, {})
    done = run_gate(repo, 'check', base_ref=base, unclassified='fail',
                    exclude='src/big.py\ndocs/\n')
    assert done.returncode == 0, done.stderr


def test_unclassified_fail_names_the_file(tmp_path):
    repo = make_repo(tmp_path, {'docs/README.md': OVER, 'src/a.py': 'x\n'})
    base = put_records(repo, {})
    failing = run_gate(repo, 'check', base_ref=base, unclassified='fail')
    assert failing.returncode == 1
    assert 'docs/README.md' in failing.stderr
    assert run_gate(repo, 'check', base_ref=base).returncode == 0


def test_stale_warn_prints_a_notice_and_passes(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 400})
    base = put_records(repo, {'src/a.py': 480})
    add_and_commit(repo, 'src/a.py', 'x\n' * 300, 'shrink the file')
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 0, done.stderr
    assert '::notice::src/a.py: stale' in done.stdout


def test_stale_fail_fails(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 400})
    base = put_records(repo, {'src/a.py': 480})
    add_and_commit(repo, 'src/a.py', 'x\n' * 300, 'shrink the file')
    done = run_gate(repo, 'check', base_ref=base, stale='fail')
    assert done.returncode == 1
    assert 'src/a.py: stale' in done.stderr


@pytest.mark.parametrize('stale', ['warn', 'fail'])
def test_an_unchanged_record_is_neither_failure_nor_stale(tmp_path, stale):
    # A record equal to the head count (issue 11) is neither a failure nor
    # a stale notice; before the fix the check crashed on it instead. The
    # file sits over its ceiling, so the record is 'same', not graduated.
    repo = make_repo(tmp_path, {'src/a.py': BIG})
    base = put_records(repo, {'src/a.py': 501})
    done = run_gate(repo, 'check', base_ref=base, stale=stale)
    assert done.returncode == 0, done.stderr
    assert 'Traceback' not in done.stderr
    assert '::notice::' not in done.stdout


def test_an_unchanged_record_leaves_the_stale_ones_reported(tmp_path):
    repo = make_repo(tmp_path, {'src/same.py': BIG,
                                'src/stale.py': 'x\n' * 600})
    base = put_records(repo, {'src/same.py': 501, 'src/stale.py': 900})
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 0, done.stderr
    assert '::notice::src/stale.py: stale' in done.stdout
    assert '::notice::src/same.py' not in done.stdout


def test_a_deleted_entry_is_stale(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 480})
    base = put_records(repo, {'src/a.py': 480})
    git(repo, 'rm', '-q', 'src/a.py')
    git(repo, 'commit', '-qm', 'delete the file')
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 0, done.stderr
    assert '::notice::src/a.py: stale' in done.stdout


def test_require_tight_accepts_the_exact_tighten_map(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 560})
    base = put_records(repo, {'src/a.py': 600})
    add_and_commit(repo, 'src/a.py', 'x\n' * 560 + 'y\n', 'shrink the file')
    add_and_commit(repo, RECORDS,
                   records.document_text('baseline', {'src/a.py': 561}),
                   'carry the lowered record')
    done = run_gate(repo, 'check', base_ref=base, require_tight='true')
    assert done.returncode == 0, done.stderr


def test_require_tight_fails_naming_each_differing_entry(tmp_path):
    # The seed stays at the recorded 600 while the tree shrank to 560:
    # tighten-from-base writes 560, so the head records must move with it.
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 560})
    base = put_records(repo, {'src/a.py': 600})
    done = run_gate(repo, 'check', base_ref=base, require_tight='true')
    assert done.returncode == 1
    assert '~ src/a.py: required 560, recorded 600' in done.stderr


def test_require_tight_names_a_missing_head_entry(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 560})
    base = put_records(repo, {'src/a.py': 600})
    add_and_commit(repo, RECORDS,
                   records.document_text('baseline', {}),
                   'drop the record by hand')
    done = run_gate(repo, 'check', base_ref=base, require_tight='true')
    assert done.returncode == 1
    assert '+ src/a.py: record 560' in done.stderr


def test_the_base_records_bind_not_the_head_edits(tmp_path):
    """A records edit in the head cannot loosen the gate."""
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 480})
    base = put_records(repo, {'src/a.py': 480})
    add_and_commit(repo, RECORDS,
                   records.document_text('baseline', {'src/a.py': 500}),
                   'raise the record by hand')
    add_and_commit(repo, 'src/a.py', 'x\n' * 490, 'grow past the head record')
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 1
    assert 'src/a.py: 490 lines above its record of 480' in done.stderr


def test_seed_fallback_accepts_the_exact_seed(tmp_path):
    repo = make_repo(tmp_path, {'src/big.py': BIG, 'src/small.py': 'x\n'})
    base = head(repo)  # no records at the base: the head's seed binds
    put_records(repo, {'src/big.py': 501})
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize('seed,line', [
    ({'src/big.py': 600}, '~ src/big.py: required 501, recorded 600'),
    ({'src/big.py': 501, 'src/small.py': 5}, '- src/small.py: recorded 5'),
    ({}, '+ src/big.py: record 501'),
])
def test_seed_fallback_names_a_wrong_entry(tmp_path, seed, line):
    repo = make_repo(tmp_path, {'src/big.py': BIG, 'src/small.py': 'x\n'})
    base = head(repo)
    put_records(repo, seed)
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 1
    assert line in done.stderr


def test_check_reads_the_committed_records_not_the_working_tree(tmp_path):
    repo = make_repo(tmp_path, {'src/big.py': BIG})
    base = head(repo)
    put_records(repo, {'src/big.py': 501})
    # An uncommitted edit is not the revision under check.
    (repo / RECORDS).write_text(records.document_text('baseline', {}))
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 0, done.stderr


def test_a_head_that_drops_an_untyped_record_cannot_escape_it(tmp_path):
    # Base records an untyped file; the head grows it and drops the entry.
    # The base's record still binds (issue 1 items 4 and 5).
    repo = make_repo(tmp_path, {'Makefile': 'x\n' * 900})
    base = put_records(repo, {'Makefile': 900})
    add_and_commit(repo, 'Makefile', 'x\n' * 950, 'grow the untyped file')
    put_records(repo, {})
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 1, done.stdout
    assert 'Makefile: 950 lines above its record of 900' in done.stderr


def test_the_seed_paths_unclassified_check_fails(tmp_path):
    repo = make_repo(tmp_path, {'docs/notes.md': 'x\n' * 10, 'src/a.py': 'x\n'})
    base = head(repo)  # no base records: the seed path
    put_records(repo, {})
    done = run_gate(repo, 'check', base_ref=base, unclassified='fail')
    assert done.returncode == 1
    assert 'docs/notes.md: unclassified' in done.stderr
    assert run_gate(repo, 'check', base_ref=base).returncode == 0


@pytest.mark.parametrize('document', ['{"baseline": {', '{"baseline":'])
def test_a_truncated_records_file_is_a_configuration_error(tmp_path, document):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n'})
    add_and_commit(repo, RECORDS, document, 'truncate the records file')
    done = run_gate(repo, 'tighten')
    assert done.returncode == 2, done.stderr
    assert 'records file' in done.stderr and 'size-baseline.json' in done.stderr
    assert 'Traceback' not in done.stderr


def test_a_non_utf8_records_file_is_named(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n'})
    add_and_commit(repo, RECORDS, b'{"baseline": {"\xff": 1}}', 'break the encoding')
    done = run_gate(repo, 'tighten')
    assert done.returncode == 2
    assert 'size-baseline.json' in done.stderr


def test_neither_revision_having_records_fails_with_the_seed_instruction(tmp_path):
    repo = make_repo(tmp_path, {'src/big.py': BIG})
    done = run_gate(repo, 'check', base_ref=head(repo))
    assert done.returncode == 1
    assert 'neither the base revision nor this head carries records' in \
        done.stderr
    assert 'run tighten' in done.stderr


def test_tighten_seeds_over_ceiling_files(tmp_path):
    repo = make_repo(tmp_path, {'src/big.py': BIG, 'src/small.py': 'x\n'})
    done = run_gate(repo, 'tighten')
    assert done.returncode == 0, done.stderr
    on_disk = json.loads((repo / RECORDS).read_text())
    assert on_disk['baseline'] == {'src/big.py': 501}
    assert on_disk['schema_version'] == 1


def test_tighten_with_nothing_to_change_writes_nothing(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 501})
    put_records(repo, {'src/a.py': 501})
    before = (repo / RECORDS).read_bytes()
    done = run_gate(repo, 'tighten')
    assert done.returncode == 0, done.stderr
    assert (repo / RECORDS).read_bytes() == before
    assert 'nothing to tighten' in done.stdout


def test_tighten_lowers_shrinks_and_drops_graduates(tmp_path):
    repo = make_repo(tmp_path, {'src/shrunk.py': 'x\n' * 600,
                                'src/graduated.py': 'x\n' * 400,
                                'src/stays.py': 'x\n' * 501})
    put_records(repo, {'src/shrunk.py': 900, 'src/graduated.py': 900,
                       'src/stays.py': 501})
    done = run_gate(repo, 'tighten')
    assert done.returncode == 0, done.stderr
    on_disk = json.loads((repo / RECORDS).read_text())
    assert on_disk['baseline'] == {'src/shrunk.py': 600, 'src/stays.py': 501}


def test_tighten_never_raises(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 700})
    put_records(repo, {'src/a.py': 600})
    before = (repo / RECORDS).read_bytes()
    done = run_gate(repo, 'tighten')
    assert done.returncode == 0, done.stderr
    assert (repo / RECORDS).read_bytes() == before
    assert 'nothing to tighten' in done.stdout


def test_tighten_with_a_member_keeps_other_keys_byte_for_byte(tmp_path):
    other = ('{\n  "schema_version": 1,\n'
             '  "note": "coverage floor lives here"\n}\n')
    repo = make_repo(tmp_path, {'src/big.py': BIG, 'note.txt': 'x\n'})
    add_and_commit(repo, RECORDS, other, 'carry the foreign document')
    done = run_gate(repo, 'tighten', member='sizes')
    assert done.returncode == 0, done.stderr
    text = (repo / RECORDS).read_text()
    assert text == ('{\n  "sizes": {\n    "src/big.py": 501\n  },\n'
                    '  "schema_version": 1,\n'
                    '  "note": "coverage floor lives here"\n}\n')


def test_auto_checks_a_pull_request(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n' * 480})
    base = put_records(repo, {'src/a.py': 480})
    add_and_commit(repo, 'src/a.py', 'x\n' * 495, 'grow the file')
    done = run_gate(repo, 'auto', base_ref=base,
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 1
    assert '495 lines above its record of 480' in done.stderr


def test_auto_tightens_a_push_to_the_default_branch(tmp_path):
    repo = make_repo(tmp_path, {'src/big.py': BIG})
    done = run_gate(repo, 'auto', default_branch='main', extra_env={
        'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main'})
    assert done.returncode == 0, done.stderr
    assert (repo / RECORDS).exists()


@pytest.mark.parametrize('event,ref', [
    ('push', 'refs/heads/feature'),
    ('workflow_dispatch', ''),
    ('schedule', 'refs/heads/main'),
])
def test_auto_fails_closed_on_other_events(tmp_path, event, ref):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n'})
    done = run_gate(repo, 'auto', default_branch='main', extra_env={
        'GITHUB_EVENT_NAME': event, 'GITHUB_REF': ref})
    assert done.returncode == 2
    assert f'cannot handle event {event!r}' in done.stderr


def test_a_forced_check_without_base_ref_is_a_configuration_error(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n'})
    done = run_gate(repo, 'check')
    assert done.returncode == 2
    assert 'base-ref' in done.stderr


@pytest.mark.parametrize('bad,match', [
    ('source: 0', 'not a positive integer'),
    ('rust: 500', "unknown type 'rust'"),
    ('no separator', 'expected "<type>: <ceiling>"'),
])
def test_bad_ceilings_exit_two_naming_the_input(tmp_path, bad, match):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n'})
    done = run_gate(repo, 'check', base_ref=head(repo), ceilings=bad)
    assert done.returncode == 2
    assert 'input ceilings line 1' in done.stderr
    assert match in done.stderr


@pytest.mark.parametrize('bad,match', [
    ('tests: 700', 'has no trailing /'),
    ('tests/: 700\ntests/: 300', 'duplicate folder'),
    ('tests*/: 700', 'wildcard'),
])
def test_bad_overrides_exit_two_naming_the_input(tmp_path, bad, match):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n'})
    done = run_gate(repo, 'check', base_ref=head(repo), overrides=bad)
    assert done.returncode == 2
    assert match in done.stderr


def test_bad_enum_inputs_exit_two(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n'})
    for key, value, match in [
            ('unclassified', 'strict',
             'input unclassified must be ignore or fail'),
            ('stale', 'strict', 'input stale must be warn or fail'),
            ('require_tight', 'TRUE',
             'input require-tight must be true or false')]:
        done = run_gate(repo, 'check', base_ref=head(repo), **{key: value})
        assert done.returncode == 2, (key, done.stderr)
        assert match in done.stderr
    done = run_gate(repo, 'strict', base_ref=head(repo))
    assert done.returncode == 2
    assert 'input mode must be auto, check or tighten' in done.stderr


def test_an_escaping_records_path_is_a_configuration_error(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n'})
    done = run_gate(repo, 'tighten', records='../outside.json')
    assert done.returncode == 2
    assert 'records' in done.stderr


def test_an_unreadable_file_fails_the_run_naming_it(tmp_path):
    import size_baseline
    from tests.helpers import write_files
    repo = make_repo(tmp_path, {'src/big.py': BIG})
    sha = size_baseline.list_tree(repo, 'HEAD')[0][1]
    loose = repo / '.git' / 'objects' / sha[:2] / sha[2:]
    loose.unlink()
    done = run_gate(repo, 'tighten')
    assert done.returncode == 2, done.stderr
    assert 'src/big.py' in done.stderr


def test_a_corrupt_records_file_is_a_configuration_error(tmp_path):
    repo = make_repo(tmp_path, {'src/big.py': BIG, 'broken.txt': 'x\n'})
    add_and_commit(repo, RECORDS, '{not json', 'break the records file')
    done = run_gate(repo, 'tighten')
    assert done.returncode == 2
    assert 'records file' in done.stderr


@pytest.mark.parametrize('name,value,match', [
    ('mode', 'bogus', 'input mode must be auto, check or tighten'),
    ('stale', 'bogus', 'input stale must be warn or fail'),
    ('require_tight', 'yes', 'input require-tight must be true or false'),
    ('records', '../x.json', 'input records'),
])
def test_validate_only_refuses_every_bad_input(tmp_path, name, value, match):
    import os
    import subprocess
    from tests.helpers import ACTION_ROOT
    repo = make_repo(tmp_path, {'src/a.py': 'x\n'})
    env = dict(os.environ, GITHUB_WORKSPACE=str(repo),
               **{'SIZE_RATCHET_' + name.upper(): value})
    done = subprocess.run([sys.executable, '-I', str(ACTION_ROOT / 'run.py'),
                           '--validate-only'],
                          env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 2, done.stderr
    assert match in done.stderr


def test_stale_is_validated_in_tighten_mode_too(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': 'x\n'})
    done = run_gate(repo, 'tighten', stale='bogus')
    assert done.returncode == 2
    assert 'input stale must be warn or fail' in done.stderr

