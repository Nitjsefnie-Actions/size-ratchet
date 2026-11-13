"""End-to-end tests for tree budgets in a pull request's check."""
from __future__ import annotations

import pytest

from tests.helpers import (add_and_commit, git, head, make_repo, run_gate,
                           write_files)
from tests.test_trees import TOTAL, TREE, put

GROW = 'tests/more.txt'  # five lines added under the tree


def check(repo, base, **inputs):
    return run_gate(repo, 'check', base_ref=base, tree_budgets='tests/',
                    **inputs)


# --- the base has no record: the head carries the exact seed --------------


def base_without_records(repo):
    return head(repo)


def base_without_trees(repo):
    return put(repo, None, {})


def base_without_the_folder(repo):
    return put(repo, {'docs/': 3}, {})


@pytest.mark.parametrize('make_base', [
    base_without_records, base_without_trees, base_without_the_folder])
def test_an_exact_seed_passes_without_a_feature_commit(tmp_path, make_base):
    repo = make_repo(tmp_path, {**TREE, 'docs/a.md': 'd\n' * 3})
    base = make_base(repo)
    put(repo, {'tests/': TOTAL}, {}, message='chore: seed the tree budget')
    done = check(repo, base)
    assert done.returncode == 0, done.stderr
    assert '1 tree budgets hold' in done.stdout


@pytest.mark.parametrize('make_base', [
    base_without_records, base_without_trees, base_without_the_folder])
@pytest.mark.parametrize('trees,shown', [
    ({'tests/': TOTAL + 1}, 'record 16'),
    ({'tests/': TOTAL - 1}, 'record 14'),
])
def test_a_seed_off_the_total_fails(tmp_path, make_base, trees, shown):
    repo = make_repo(tmp_path, {**TREE, 'docs/a.md': 'd\n' * 3})
    base = make_base(repo)
    put(repo, trees, {}, message='feat: seed the tree budget')
    done = check(repo, base)
    assert done.returncode == 1
    assert ('tests/: the base has no record, so the head must carry the '
            f'seed tighten writes: total 15, {shown}') in done.stderr


@pytest.mark.parametrize('make_base', [
    base_without_records, base_without_trees, base_without_the_folder])
@pytest.mark.parametrize('require_tight', ['false', 'true'])
@pytest.mark.parametrize('trim', [0, 1])
def test_no_head_entry_passes_while_the_base_total_holds(
        tmp_path, make_base, require_tight, trim):
    repo = make_repo(tmp_path, {**TREE, 'docs/a.md': 'd\n' * 3})
    base = make_base(repo)
    write_files(repo, {'tests/a.py': 'a\n' * (3 - trim)})
    add_and_commit(repo, 'docs/a.md', 'd\n', 'chore: hold or trim the tests')
    done = check(repo, base, require_tight=require_tight)
    assert done.returncode == 0, done.stderr
    assert '1 tree budgets hold' in done.stdout


@pytest.mark.parametrize('make_base', [
    base_without_records, base_without_trees, base_without_the_folder])
def test_no_head_entry_fails_past_the_base_total(tmp_path, make_base):
    repo = make_repo(tmp_path, {**TREE, 'docs/a.md': 'd\n' * 3})
    base = make_base(repo)
    add_and_commit(repo, GROW, 'm\n', 'feat: grow the tests')
    done = check(repo, base)
    assert done.returncode == 1
    assert 'tests/: 16 lines above the base total of 15' in done.stderr
    put(repo, {'tests/': 16}, {})
    done = check(repo, base)
    assert done.returncode == 0, done.stderr


# --- the base has a record ------------------------------------------------


def test_a_total_above_the_head_record_fails(tmp_path):
    repo = make_repo(tmp_path, TREE)
    base = put(repo, {'tests/': TOTAL + 2}, {})
    add_and_commit(repo, GROW, 'm\n' * 5, 'feat: grow the tests')
    done = check(repo, base, require_tight='false')
    assert done.returncode == 1
    assert 'tests/: 20 lines above its record of 17' in done.stderr


def test_a_record_lowered_below_the_total_fails(tmp_path):
    repo = make_repo(tmp_path, TREE)
    base = put(repo, {'tests/': TOTAL + 2}, {})
    put(repo, {'tests/': TOTAL - 1}, {})
    done = check(repo, base, require_tight='false')
    assert done.returncode == 1
    assert 'tests/: 15 lines above its record of 14' in done.stderr


def test_a_dropped_record_fails(tmp_path):
    repo = make_repo(tmp_path, TREE)
    base = put(repo, {'tests/': TOTAL}, {})
    put(repo, {}, {})
    done = check(repo, base, require_tight='false')
    assert done.returncode == 1
    assert 'tests/: the head drops the record (base 15); total 15' in \
        done.stderr


@pytest.mark.parametrize('subject,verdict', [
    ('feat: grow the tests', 0),
    ('feat(harness): grow the tests', 0),
    ('feat!: grow the tests', 0),
    ('feat(harness)!: grow the tests', 0),
    ('feat!!: grow the tests', 1),
    ('feat !: grow the tests', 1),
    ('feat(harness)! grow the tests', 1),
    ('feat!(harness): grow the tests', 1),
    ('fix: grow the tests', 1),
    ('feature: grow the tests', 1),
    ('feat grow the tests', 1),
    ('chore: feat: grow the tests', 1),
])
def test_a_raise_needs_a_feature_subject_in_the_range(tmp_path, subject,
                                                      verdict):
    repo = make_repo(tmp_path, TREE)
    base = put(repo, {'tests/': TOTAL}, {})
    add_and_commit(repo, GROW, 'm\n' * 5, subject)
    put(repo, {'tests/': TOTAL + 5}, {}, message='chore: record the budget')
    done = check(repo, base)
    assert done.returncode == verdict, done.stderr
    if verdict:
        assert ('tests/: total 20, record raised from 15 to 20 without a '
                'feature commit. Only a feature change may raise a tree '
                'budget') in done.stderr
        assert 'trims its own lines under that folder' in done.stderr
        assert ('starting feat:, feat!:, feat(<scope>): or '
                'feat(<scope>)!:.') in done.stderr
        assert 'feature commit in the range, breaking (feat!:) or not' in \
            done.stderr


def test_a_feature_commit_before_the_base_does_not_count(tmp_path):
    repo = make_repo(tmp_path, TREE)
    add_and_commit(repo, 'src/x.py', 'x\n', 'feat: an older feature')
    base = put(repo, {'tests/': TOTAL}, {})
    add_and_commit(repo, GROW, 'm\n' * 5, 'fix: grow the tests')
    put(repo, {'tests/': TOTAL + 5}, {})
    done = check(repo, base)
    assert done.returncode == 1
    assert 'without a feature commit' in done.stderr


def raised_origin(tmp_path, subject):
    origin = make_repo(tmp_path, TREE, name='origin')
    base = put(origin, {'tests/': TOTAL}, {})
    add_and_commit(origin, GROW, 'm\n' * 5, subject)
    put(origin, {'tests/': TOTAL + 5}, {})
    return origin, base


def test_a_shallow_history_is_a_configuration_error(tmp_path):
    # Depth 3 keeps the base commit, so only the history read can refuse.
    origin, base = raised_origin(tmp_path, 'feat: grow the tests')
    shallow = tmp_path / 'shallow'
    git(tmp_path, 'clone', '-q', '--depth', '3', f'file://{origin}',
        str(shallow))
    assert git(shallow, 'rev-parse', '--is-shallow-repository').strip() == \
        'true'
    done = check(shallow, base)
    assert done.returncode == 2
    assert 'the repository is shallow or unreadable' in done.stderr


def test_a_shallow_history_is_not_read_without_a_raise(tmp_path):
    origin = make_repo(tmp_path, TREE, name='origin')
    base = put(origin, {'tests/': TOTAL}, {})
    add_and_commit(origin, 'src/x.py', 'x\n', 'fix: an unrelated change')
    shallow = tmp_path / 'shallow'
    git(tmp_path, 'clone', '-q', '--depth', '2', f'file://{origin}',
        str(shallow))
    done = check(shallow, base)
    assert done.returncode == 0, done.stderr


def test_an_unreadable_history_is_a_configuration_error(tmp_path):
    origin, base = raised_origin(tmp_path, 'feat: grow the tests')
    middle = git(origin, 'rev-parse', 'HEAD~1').strip()
    (origin / '.git' / 'objects' / middle[:2] / middle[2:]).unlink()
    done = check(origin, base)
    assert done.returncode == 2
    assert f'cannot read the feature history {base}..HEAD' in done.stderr


# --- require-tight ----------------------------------------------------------


def test_a_tree_whose_total_moved_must_record_it_exactly(tmp_path):
    repo = make_repo(tmp_path, {**TREE, 'tests/old.txt': 'o\n' * 4})
    base = put(repo, {'tests/': TOTAL + 4}, {})
    git(repo, 'rm', '-q', 'tests/old.txt')
    git(repo, 'commit', '-qm', 'fix: shrink the tests')
    strict = check(repo, base, require_tight='true')
    assert strict.returncode == 1
    assert ('require-tight: tests/: total 15 moved or its record did, so it '
            'must record exactly 15; recorded 19') in strict.stderr
    assert check(repo, base, require_tight='false').returncode == 0


def test_a_tree_whose_record_moved_must_record_it_exactly(tmp_path):
    repo = make_repo(tmp_path, TREE)
    base = put(repo, {'tests/': TOTAL + 5}, {})
    put(repo, {'tests/': TOTAL + 3}, {})
    strict = check(repo, base, require_tight='true')
    assert strict.returncode == 1
    assert ('require-tight: tests/: total 15 moved or its record did, so it '
            'must record exactly 15; recorded 18') in strict.stderr
    assert check(repo, base, require_tight='false').returncode == 0


def test_an_untouched_loose_tree_passes_require_tight(tmp_path):
    repo = make_repo(tmp_path, TREE)
    base = put(repo, {'tests/': TOTAL + 5}, {})
    add_and_commit(repo, 'src/x.py', 'x\n', 'fix: an unrelated change')
    done = check(repo, base, require_tight='true')
    assert done.returncode == 0, done.stderr


# --- both halves report ----------------------------------------------------


def test_a_per_file_failure_does_not_mask_a_tree_failure(tmp_path):
    repo = make_repo(tmp_path, {**TREE, 'src/a.py': 'x\n' * 480})
    base = put(repo, {'tests/': TOTAL}, {'src/a.py': 480})
    add_and_commit(repo, 'src/a.py', 'x\n' * 490, 'fix: grow the source')
    add_and_commit(repo, GROW, 'm\n' * 5, 'fix: grow the tests')
    done = check(repo, base, require_tight='false')
    assert done.returncode == 1
    assert 'src/a.py: 490 lines above its record of 480' in done.stderr
    assert 'tests/: 20 lines above its record of 15' in done.stderr


def test_a_tree_pass_does_not_mask_a_per_file_failure(tmp_path):
    repo = make_repo(tmp_path, {**TREE, 'src/a.py': 'x\n' * 480})
    base = put(repo, {'tests/': TOTAL}, {'src/a.py': 480})
    add_and_commit(repo, 'src/a.py', 'x\n' * 490, 'fix: grow the source')
    done = check(repo, base)
    assert done.returncode == 1
    assert '1 tree budgets hold' in done.stdout
