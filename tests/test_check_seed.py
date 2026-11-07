"""End-to-end tests for a check whose base revision has no records."""
from __future__ import annotations

import pytest

from tests.helpers import (add_and_commit, head, make_repo, put_records,
                           run_gate, write_files)

BIG = 'x\n' * 501
TREE = {'src/big.py': BIG, 'src/small.py': 'x\n'}


@pytest.mark.parametrize('big,small', [(510, 1), (505, 10), (400, 500)])
@pytest.mark.parametrize('require_tight,stale', [('false', 'warn'),
                                                 ('true', 'fail')])
def test_a_held_or_shrunk_file_passes_without_records(tmp_path, big, small,
                                                      require_tight, stale):
    repo = make_repo(tmp_path, {'src/big.py': 'x\n' * 510,
                                'src/small.py': 'x\n'})
    base = head(repo)
    write_files(repo, {'src/big.py': 'x\n' * big,
                       'src/small.py': 'x\n' * small})
    add_and_commit(repo, 'src/other.py', 'x\n', 'hold or shrink the files')
    done = run_gate(repo, 'check', base_ref=base, require_tight=require_tight,
                    stale=stale)
    assert done.returncode == 0, done.stderr
    assert "within the size policy and the base tree's counts" in done.stdout


def test_an_over_ceiling_file_grown_without_records_fails(tmp_path):
    repo = make_repo(tmp_path, TREE)
    base = head(repo)
    add_and_commit(repo, 'src/big.py', 'x\n' * 502, 'grow the big file')
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 1
    assert 'src/big.py: 502 lines above its base count of 501' in done.stderr
    assert 'Or carry the exact seed' in done.stderr


@pytest.mark.parametrize('path', ['src/new.py', 'src/small.py'])
def test_a_file_crossing_its_ceiling_without_records_fails(tmp_path, path):
    repo = make_repo(tmp_path, TREE)
    base = head(repo)
    add_and_commit(repo, path, 'x\n' * 600, 'cross the ceiling')
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 1
    assert f'{path}: 600 lines above its ceiling of 500' in done.stderr


def test_an_exact_seed_lets_the_head_grow_a_file(tmp_path):
    repo = make_repo(tmp_path, TREE)
    base = head(repo)
    add_and_commit(repo, 'src/big.py', 'x\n' * 510, 'grow the big file')
    put_records(repo, {'src/big.py': 510})
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize('seed,line', [
    ({'src/big.py': 520}, '~ src/big.py: required 510, recorded 520'),
    ({'src/big.py': 501}, '~ src/big.py: required 510, recorded 501'),
])
def test_a_loose_head_map_fails_over_a_base_without_records(tmp_path, seed,
                                                            line):
    repo = make_repo(tmp_path, TREE)
    base = head(repo)
    add_and_commit(repo, 'src/big.py', 'x\n' * 510, 'grow the big file')
    put_records(repo, seed)
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 1
    assert line in done.stderr


def test_an_untyped_file_fails_unclassified_without_records(tmp_path):
    repo = make_repo(tmp_path, {'docs/notes.md': 'x\n', 'src/a.py': 'x\n'})
    done = run_gate(repo, 'check', base_ref=head(repo), unclassified='fail')
    assert done.returncode == 1
    assert 'docs/notes.md: unclassified' in done.stderr
