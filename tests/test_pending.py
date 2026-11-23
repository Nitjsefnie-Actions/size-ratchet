"""Under require-tight 'false', a pull request checked before the push run's
tighten commit is bound as if that commit had landed."""
from __future__ import annotations

import pytest

from tests.helpers import git, make_repo, put_records, records, write_files
from tests.test_install_block import LOWERED, RAISED, RECORDS, pull_request
from tests.test_trees import TOTAL, TREE, put
from tests.test_trees_check import GROW, check

LOWERS = '; tighten lowers its base record of 900 to it'
DROPS = ('; the base record of 900 is one tighten drops, the file being at or below its ceiling in '
         "the base tree, and until the push run's tighten commit or a merged pull request drops it "
         'the file may not grow past its base count')
GONE = '; the base tree measures no file here, so tighten drops its base record of 900'
UNCLASSIFIED = 'f.txt: unclassified (no type and no record); name a ceiling for it or exclude it'
BIN = 'f.py: 900 lines above its ceiling of 500' + GONE
# (base tree, base map, head tree, head map or None to keep the base map,
# inputs, the line 'false' prints or None for a pass, 'true''s exit status)
ROWS = [
    ({'f.py': 600}, {'f.py': 900}, {'f.py': 900}, None, {}, 'f.py: 900 lines above its base count of 600' + LOWERS, 0),
    ({'f.py': 600}, {'f.py': 900}, {'f.py': 700}, None, {}, 'f.py: 700 lines above its base count of 600' + LOWERS, 1),
    ({'f.py': 600}, {'f.py': 900}, {'f.py': 900}, None, {'ceilings': 'source: 1000'}, 'f.py: 900 lines above its base count of 600' + DROPS, 1),
    ({}, {'f.py': 900}, {'f.py': 900}, None, {}, 'f.py: 900 lines above its ceiling of 500' + GONE, 0),
    ({'f.py': 400}, {'f.py': 900}, {'f.py': 900}, None, {}, 'f.py: 900 lines above its base count of 400' + DROPS, 0),
    ({}, {'f.txt': 900}, {'f.txt': 900}, None, {'unclassified': 'fail'}, UNCLASSIFIED, 0),
    ({'f.py': b'\0\n'}, {'f.py': 900}, {'f.py': 900}, None, {}, BIN, 0),
    ({}, {'f.txt': 5}, {'f.txt': 6}, None, {}, 'f.txt: 6 lines above its record of 5', 1),
    ({}, {'f.txt': 5}, {'f.txt': 5}, None, {}, None, 0),
    # The four shapes 'true' admits.
    ({'f.py': 600}, {'f.py': 900}, {'f.py': 800}, {'f.py': 800}, {}, 'f.py: 800 lines above its base count of 600' + LOWERS, 0),
    ({'f.py': 400}, {'f.py': 900}, {'f.py': 450}, {}, {}, 'f.py: 450 lines above its base count of 400' + DROPS, 0),
    ({}, {'f.py': 900}, {'f.py': 700}, None, {}, 'f.py: 700 lines above its ceiling of 500' + GONE, 1),
    # A lower, drop or raise refusal on a held entry shows its bound.
    ({'f.py': 600}, {'f.py': 900}, {'f.py': 900}, {'f.py': 500}, {}, LOWERED + '~ f.py: required 600, recorded 500', 1),
    ({'f.py': 600}, {'f.py': 900}, {'f.py': 900}, {}, {}, LOWERED + '+ f.py: record 600', 1),
    ({'f.py': 600}, {'f.py': 900}, {'f.py': 900}, {'f.py': 1000}, {}, RAISED + '~ f.py: required 600, recorded 1000', 1),
    ({}, {'f.py': 900}, {'f.py': 900}, {}, {}, 'f.py: 900 lines above its ceiling of 500' + GONE, 1),
    ({'f.py': 600}, {'f.py': 600}, {'f.py': 900}, None, {'ceilings': 'source: 1000'}, 'f.py: 900 lines above its record of 600', 1),
]


@pytest.mark.parametrize('tight', ['false', 'true'])
@pytest.mark.parametrize('base_tree,base_map,head_tree,head_map,inputs,line,strict', ROWS)
def test_a_pending_tighten_binds_the_head(tmp_path, base_tree, base_map, head_tree, head_map,
                                          inputs, line, strict, tight):
    repo = make_repo(tmp_path, {p: n if isinstance(n, bytes) else 'x\n' * n
                                for p, n in {**base_tree, 'keep.py': 1}.items()})
    base = put_records(repo, base_map)
    write_files(repo, {p: 'x\n' * n for p, n in head_tree.items()})
    if head_map is not None:
        write_files(repo, {RECORDS: records.document_text('baseline', head_map)})
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'the pull request')
    done = pull_request(repo, base, require_tight=tight, **inputs)
    assert done.returncode == (strict if tight == 'true' else int(line is not None)), done.stderr
    if line and tight == 'false':
        assert line + '\n' in done.stderr
        lowered = line.startswith(LOWERED)
        assert ('the bound named above' in done.stderr) is (lowered and line[len(LOWERED)] in '+~')
        assert ('lowers below' in done.stderr) is lowered


@pytest.mark.parametrize('tight', ['false', 'true'])
@pytest.mark.parametrize('base_budget,budget,total,subject,line', [
    (TOTAL + 5, TOTAL + 5, TOTAL + 5, 'fix', 'tests/: 20 lines above the base total of 15 (budget 20) without a feature commit.'),
    (TOTAL + 5, TOTAL + 5, TOTAL + 3, 'fix', 'tests/: 18 lines above the base total of 15 (budget 20) without a feature commit.'),
    (TOTAL + 5, TOTAL + 3, TOTAL + 3, 'fix', 'tests/: 18 lines above the base total of 15 (budget 20) without a feature commit.'),
    (TOTAL + 5, None, TOTAL + 5, 'feat', 'tests/: the head drops the record (base total 15); total 20'),
    (TOTAL + 5, TOTAL + 9, TOTAL + 5, 'fix', 'above the total 20; tighten writes 20 (15, the base total, without a feature commit)'),
    (TOTAL + 5, TOTAL + 5, TOTAL + 5, 'feat', None),
    (TOTAL + 5, TOTAL + 5, TOTAL, 'fix', None),
    (TOTAL, TOTAL + 5, TOTAL + 5, 'feat', None),
])
def test_a_pending_tighten_binds_a_tree_total(tmp_path, base_budget, budget, total, subject,
                                              line, tight):
    repo = make_repo(tmp_path, {**TREE, 'docs/a.md': 'd\n'})
    base = put(repo, {'tests/': base_budget}, {})
    write_files(repo, {GROW: 'm\n' * (total - TOTAL), 'docs/a.md': 'e\n'})
    git(repo, 'add', '-A')
    put(repo, {} if budget is None else {'tests/': budget}, {}, message=f'{subject}: the pull request')
    done = check(repo, base, require_tight=tight)
    assert done.returncode == int(line is not None and (tight == 'false' or budget != total)), done.stderr
    if line is not None and tight == 'false':
        assert line in done.stderr
