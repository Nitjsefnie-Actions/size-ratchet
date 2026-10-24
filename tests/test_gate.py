"""Tests for measurement and the ratchet's verdict and tighten functions."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'ci'))
import size_baseline  # noqa: E402

from tests.helpers import make_repo  # noqa: E402

BIG = '\n'.join(f'x{i} = {i}' for i in range(501)) + '\n'  # 501 lines
SMALL = 'a\nb\nc\n'


def entries_of(repo):
    return size_baseline.list_tree(repo, 'HEAD')


def test_count_lines_counts_newlines_plus_a_final_partial():
    assert size_baseline.count_lines(b'a\nb\nc\n') == 3
    assert size_baseline.count_lines(b'a\nb\nc') == 3
    assert size_baseline.count_lines(b'') == 0
    assert size_baseline.count_lines(b'\n') == 1
    assert size_baseline.count_lines(b'\nx') == 2


def test_binary_is_a_nul_in_the_first_8000_bytes():
    assert size_baseline.is_binary(b'a\0b')
    assert size_baseline.is_binary(b'a' * 7999 + b'\0')
    assert not size_baseline.is_binary(b'a' * 8000 + b'\0' + b'b')


def test_list_tree_and_measure_over_a_real_tree(tmp_path):
    repo = make_repo(tmp_path, {
        'src/small.py': SMALL,
        'src/big.py': BIG,
        'src/noeol.js': 'a\nb\nc',
        'src/blob.bin': b'x\0y\n' * 10,
    })
    policy = size_baseline.Policy()
    sizes = size_baseline.measure(repo, entries_of(repo), policy, {})
    assert sizes == {'src/small.py': 3, 'src/big.py': 501, 'src/noeol.js': 3}
    assert 'src/blob.bin' not in sizes


def test_a_symlink_is_never_measured(tmp_path):
    repo = make_repo(tmp_path, {'src/real.py': BIG})
    (repo / 'src' / 'link.py').symlink_to(repo / 'src' / 'real.py')
    from tests.helpers import git
    git(repo, 'add', 'src/link.py')
    git(repo, 'commit', '-qm', 'link')
    policy = size_baseline.Policy()
    sizes = size_baseline.measure(repo, entries_of(repo), policy, {})
    assert 'src/link.py' not in sizes


def test_untyped_files_are_measured_only_when_asked(tmp_path):
    files = {'src/a.py': BIG, 'docs/README.md': '\n'.join('x' * 80 for _ in range(600))}
    repo = make_repo(tmp_path, files)
    entries = entries_of(repo)
    assert 'docs/README.md' not in size_baseline.measure(
        repo, entries, size_baseline.Policy(), {})
    assert 'docs/README.md' in size_baseline.measure(
        repo, entries, size_baseline.Policy(unclassified='fail'), {})


def test_an_excluded_file_is_never_measured(tmp_path):
    repo = make_repo(tmp_path, {'src/big.py': BIG, 'src/other.py': BIG})
    policy = size_baseline.Policy(exclude_text='src/big.py\n')
    sizes = size_baseline.measure(repo, entries_of(repo), policy, {})
    assert sizes == {'src/other.py': 501}


def test_unreadable_blob_fails_the_run(tmp_path):
    repo = make_repo(tmp_path, {'src/a.py': BIG})
    sha = size_baseline.list_tree(repo, 'HEAD')[0][1]
    loose = repo / '.git' / 'objects' / sha[:2] / sha[2:]
    loose.unlink()
    policy = size_baseline.Policy()
    try:
        size_baseline.measure(repo, entries_of(repo), policy, {})
    except OSError as failure:
        assert 'src/a.py' in str(failure)
    else:
        raise AssertionError('the missing blob passed measurement')


def test_ceiling_violations_names_unrecorded_files_above_the_ceiling():
    policy = size_baseline.Policy()
    sizes = {'src/big.py': 501, 'src/small.py': 500}
    assert size_baseline.ceiling_violations(sizes, {}, policy) == \
        {'src/big.py': (501, 500)}
    assert size_baseline.ceiling_violations(
        sizes, {'src/big.py': 501}, policy) == {}


def test_record_states_covers_every_verdict():
    policy = size_baseline.Policy(exclude_text='src/gone.py\n')
    sizes = {'src/grown.py': 501, 'src/graduated.py': 500,
             'src/shrunk.py': 600, 'src/same.py': 501}
    baseline = {'src/grown.py': 490, 'src/graduated.py': 900,
                'src/shrunk.py': 900, 'src/same.py': 501,
                'src/deleted.py': 900, 'src/gone.py': 900}
    states = size_baseline.record_states(sizes, baseline, policy)
    assert states == {
        'src/grown.py': ('grown', (501, 490)),
        'src/graduated.py': ('graduated', (500, 500)),
        'src/shrunk.py': ('shrunk', (600, 900)),
        'src/same.py': ('same', (501, 501)),
        'src/deleted.py': ('deleted', 900),
        'src/gone.py': ('excluded', 900),
    }


def test_an_untyped_record_never_graduates():
    policy = size_baseline.Policy()
    states = size_baseline.record_states(
        {'data.rst': 5}, {'data.rst': 900}, policy)
    assert states == {'data.rst': ('shrunk', (5, 900))}


def test_a_file_at_its_record_is_not_grown():
    policy = size_baseline.Policy()
    states = size_baseline.record_states({'src/a.py': 501},
                                         {'src/a.py': 501}, policy)
    assert states == {'src/a.py': ('same', (501, 501))}


def test_a_file_at_its_record_but_below_the_ceiling_is_graduated():
    policy = size_baseline.Policy()
    states = size_baseline.record_states({'src/a.py': 490},
                                         {'src/a.py': 490}, policy)
    assert states == {'src/a.py': ('graduated', (490, 500))}


def test_tighten_seeds_only_files_over_their_ceiling(tmp_path):
    repo = make_repo(tmp_path, {'src/at.py': 'x\n' * 500, 'src/over.py': BIG,
                                'src/under.py': SMALL})
    policy = size_baseline.Policy()
    sizes = size_baseline.measure(repo, entries_of(repo), policy, {})
    assert size_baseline.tightened(None, sizes, policy) == {'src/over.py': 501}


def test_tighten_seeding_ignores_untyped_and_excluded_files(tmp_path):
    repo = make_repo(tmp_path, {'src/over.py': BIG,
                                'src/typed.py': BIG})
    policy = size_baseline.Policy(exclude_text='src/typed.py\n')
    sizes = size_baseline.measure(repo, entries_of(repo), policy, {})
    sizes['docs/README.md'] = 900
    assert size_baseline.tightened(None, sizes, policy) == {'src/over.py': 501}


def test_tighten_lowers_a_shrunk_record_and_drops_a_graduated_one():
    policy = size_baseline.Policy()
    sizes = {'src/shrunk.py': 600, 'src/graduated.py': 400,
             'src/same.py': 900}
    baseline = {'src/shrunk.py': 900, 'src/graduated.py': 900,
                'src/same.py': 900}
    assert size_baseline.tightened(baseline, sizes, policy) == \
        {'src/shrunk.py': 600, 'src/same.py': 900}


def test_tighten_drops_deleted_and_excluded_entries():
    policy = size_baseline.Policy(exclude_text='src/exiled.py\n')
    sizes = {'src/staying.py': 900}
    baseline = {'src/staying.py': 900, 'src/deleted.py': 900,
                'src/exiled.py': 900}
    assert size_baseline.tightened(baseline, sizes, policy) == \
        {'src/staying.py': 900}


def test_tighten_never_raises_a_record():
    policy = size_baseline.Policy()
    sizes = {'src/grown.py': 950}
    baseline = {'src/grown.py': 900}
    assert size_baseline.tightened(baseline, sizes, policy) is None


def test_tighten_keeps_an_untyped_record_lowered_to_its_count():
    policy = size_baseline.Policy()
    sizes = {'data.rst': 300}
    baseline = {'data.rst': 900}
    assert size_baseline.tightened(baseline, sizes, policy) == \
        {'data.rst': 300}


def test_tighten_returns_none_when_nothing_would_change():
    policy = size_baseline.Policy()
    baseline = {'src/a.py': 501}
    sizes = {'src/a.py': 501}
    assert size_baseline.tightened(baseline, sizes, policy) is None
