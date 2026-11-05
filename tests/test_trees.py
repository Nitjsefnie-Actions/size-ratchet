"""End-to-end tests for tree budgets: inputs, measurement, tighten, verify."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.helpers import (ACTION_ROOT, add_and_commit, git, head,  # noqa: E402
                           make_repo, run_gate)

RECORDS = '.github/size-baseline.json'
PUSH = {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main'}
# Distinct line counts, so a dropped or double-counted file moves the total.
TREE = {'tests/a.py': 'a\n' * 3, 'tests/notes.md': 'n\n' * 5,
        'tests/deep/c.txt': 'c\n' * 6 + 'c'}
TOTAL = 15


def document(trees=None, baseline=None, **other):
    data = {'schema_version': 1, **other}
    if baseline is not None:
        data['baseline'] = baseline
    if trees is not None:
        data['trees'] = trees
    return json.dumps(data, indent=2) + '\n'


def put(repo, trees=None, baseline=None, message='chore: carry the records'):
    add_and_commit(repo, RECORDS, document(trees, baseline), message)
    return head(repo)


def written_trees(repo, records=RECORDS):
    return json.loads((repo / records).read_text()).get('trees')


def validate_only(repo, **inputs):
    env = dict(os.environ, GITHUB_WORKSPACE=str(repo))
    env.update({'SIZE_RATCHET_' + name.upper(): value
                for name, value in inputs.items()})
    return subprocess.run([sys.executable, '-I', str(ACTION_ROOT / 'run.py'),
                           '--validate-only'],
                          env=env, capture_output=True, text=True, timeout=60)


# --- inputs ---------------------------------------------------------------


@pytest.mark.parametrize('value,match', [
    ('tests/\nsrc', "input tree-budgets line 2 ('src'): folder 'src' has no "
                    'trailing /'),
    ('tests/\n\ntests/', "input tree-budgets line 3 ('tests/'): duplicate "
                         "folder 'tests/'"),
    ('/tests/', "input tree-budgets line 1 ('/tests/'): folder '/tests/' is "
                'not a repository-relative path prefix'),
    ('a/../b/', 'is not a repository-relative path prefix'),
    ('test*/', 'carries a wildcard'),
])
def test_a_bad_tree_budgets_line_is_refused_up_front(tmp_path, value, match):
    repo = make_repo(tmp_path, TREE)
    done = validate_only(repo, tree_budgets=value)
    assert done.returncode == 2, done.stderr
    assert match in done.stderr


def test_member_trees_is_refused_beside_tree_budgets(tmp_path):
    repo = make_repo(tmp_path, TREE)
    done = validate_only(repo, tree_budgets='tests/', member='trees')
    assert done.returncode == 2, done.stderr
    assert "input member 'trees' is the key tree-budgets keeps" in done.stderr
    assert validate_only(repo, member='trees').returncode == 0


# --- measurement and tighten ----------------------------------------------


def test_the_total_sums_every_text_file_under_the_prefix(tmp_path):
    repo = make_repo(tmp_path, {
        **TREE,
        'tests/blob.bin': b'x\0\n' * 11,      # binary
        'tests/skip.py': 'k\n' * 13,          # excluded
        'tests_old/x.py': 'o\n' * 17,         # shares the characters, not the /
        'src/y.py': 'y\n' * 19,               # outside
        'tests/records.json': document(baseline={}),  # the records file
    })
    (repo / 'tests' / 'link.py').symlink_to('a.py')
    git(repo, 'add', 'tests/link.py')
    git(repo, 'commit', '-qm', 'link')
    done = run_gate(repo, 'tighten', tree_budgets='tests/\ntests/deep/\n',
                    exclude='tests/skip.py', records='tests/records.json')
    assert done.returncode == 0, done.stderr
    assert written_trees(repo, 'tests/records.json') == \
        {'tests/': TOTAL, 'tests/deep/': 7}


def test_tighten_lowers_keeps_drops_and_never_raises(tmp_path):
    repo = make_repo(tmp_path, {**TREE, 'docs/a.md': 'd\n' * 4,
                                'src/b.py': 'b\n' * 2})
    put(repo, {'tests/': 40, 'docs/': 1, 'old/': 9}, {})
    done = run_gate(repo, 'tighten', tree_budgets='tests/\ndocs/\nsrc/\n')
    assert done.returncode == 0, done.stderr
    # Lowered to its total, kept below it, seeded, and the unlisted dropped.
    assert written_trees(repo) == {'tests/': TOTAL, 'docs/': 1, 'src/': 2}
    before = (repo / RECORDS).read_bytes()
    again = run_gate(repo, 'tighten', tree_budgets='tests/\ndocs/\nsrc/\n')
    assert again.returncode == 0, again.stderr
    assert 'tree budgets are unchanged' in again.stdout
    assert (repo / RECORDS).read_bytes() == before


def test_tighten_keeps_every_byte_outside_the_two_maps(tmp_path):
    original = ('{\n  "note": "kept", "sizes": {"src/big.py": 900},\n'
                '  "trees": {"tests/": 99},\n  "trailing": [1, 2]\n}\n')
    repo = make_repo(tmp_path, {**TREE, 'src/big.py': 'x\n' * 600})
    add_and_commit(repo, RECORDS, original, 'carry a foreign document')
    done = run_gate(repo, 'tighten', tree_budgets='tests/', member='sizes')
    assert done.returncode == 0, done.stderr
    assert (repo / RECORDS).read_text() == (
        '{\n  "note": "kept", "sizes": {\n    "src/big.py": 600\n  },\n'
        '  "trees": {\n    "tests/": 15\n  },\n  "trailing": [1, 2]\n}\n')


def test_tighten_creates_an_absent_records_file_with_both_maps(tmp_path):
    repo = make_repo(tmp_path, TREE)
    done = run_gate(repo, 'tighten', tree_budgets='tests/')
    assert done.returncode == 0, done.stderr
    assert json.loads((repo / RECORDS).read_text()) == \
        {'schema_version': 1, 'baseline': {}, 'trees': {'tests/': TOTAL}}


# --- empty tree-budgets is inert -----------------------------------------


def test_an_empty_input_leaves_the_trees_key_unread_and_unwritten(tmp_path):
    repo = make_repo(tmp_path, {**TREE, 'src/big.py': 'x\n' * 600})
    trees_text = '{"tests/": 1, "unlisted/": 7}'
    original = ('{\n  "schema_version": 1,\n  "baseline": '
                '{"src/big.py": 900},\n  "trees": ' + trees_text + '\n}\n')
    add_and_commit(repo, RECORDS, original, 'carry an over-budget tree')
    base = head(repo)
    done = run_gate(repo, 'auto', default_branch='main', extra_env=PUSH,
                    require_tight='true')
    assert done.returncode == 1  # the per-file record is loose, not the tree
    assert 'tests/' not in done.stdout + done.stderr
    raised = original.replace('"tests/": 1', '"tests/": 999')
    add_and_commit(repo, RECORDS, raised, 'fix: raise the tree by hand')
    done = run_gate(repo, 'check', base_ref=base)
    assert done.returncode == 0, done.stderr
    done = run_gate(repo, 'tighten')
    assert done.returncode == 0, done.stderr
    text = (repo / RECORDS).read_text()
    assert text == raised.replace('{"src/big.py": 900}',
                                  '{\n    "src/big.py": 600\n  }')


# --- the default-branch verification -------------------------------------


@pytest.mark.parametrize('tight', ['true', 'false'])
def test_verify_fails_a_total_above_its_record(tmp_path, tight):
    repo = make_repo(tmp_path, TREE)
    put(repo, {'tests/': TOTAL - 1}, {})
    done = run_gate(repo, 'auto', default_branch='main', extra_env=PUSH,
                    tree_budgets='tests/', require_tight=tight)
    assert done.returncode == 1
    assert 'tests/: 15 lines above its record of 14' in done.stderr


@pytest.mark.parametrize('trees,line', [
    ({'tests/': TOTAL + 5}, '~ tests/: required 15, recorded 20'),
    ({}, '+ tests/: record 15'),
    (None, '+ tests/: record 15'),
    ({'tests/': TOTAL, 'docs/': 4}, '- docs/: recorded 4'),
])
def test_verify_names_each_loose_tree_budget(tmp_path, trees, line):
    repo = make_repo(tmp_path, TREE)
    put(repo, trees, {})
    strict = run_gate(repo, 'auto', default_branch='main', extra_env=PUSH,
                      tree_budgets='tests/', require_tight='true')
    assert strict.returncode == 1
    assert f'    {line}\n' in strict.stderr
    lenient = run_gate(repo, 'auto', default_branch='main', extra_env=PUSH,
                       tree_budgets='tests/', require_tight='false')
    assert lenient.returncode == 0, lenient.stderr
    assert f'::notice::loose tree budget: {line}\n' in lenient.stdout


def test_a_records_file_holding_only_trees_is_records(tmp_path):
    repo = make_repo(tmp_path, TREE)
    put(repo, {'tests/': TOTAL + 1})
    done = run_gate(repo, 'auto', default_branch='main', extra_env=PUSH,
                    tree_budgets='tests/', require_tight='true')
    assert done.returncode == 1
    assert '~ tests/: required 15, recorded 16' in done.stderr
    put(repo, {'tests/': TOTAL})
    done = run_gate(repo, 'auto', default_branch='main', extra_env=PUSH,
                    tree_budgets='tests/')
    assert done.returncode == 0, done.stderr
    assert '1 tree budgets hold' in done.stdout
