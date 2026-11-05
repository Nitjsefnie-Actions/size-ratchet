"""Tree budgets on the default-branch push: the action's steps run as the
runner runs them, against a disposable bare remote."""
from __future__ import annotations

import pytest

from tests._block_harness import MEASURE, PUSH, run_action, run_block_as_called
from tests.helpers import add_and_commit, git, head, make_repo, run_gate
from tests.test_push import deliver, push_context

RECORDS = '.github/size-baseline.json'
TREE = {'tests/a.py': 'a\n' * 3, 'tests/notes.md': 'n\n' * 5,
        'tests/deep/c.txt': 'c\n' * 7, 'src/x.py': 'x\n' * 520}
BOTH = ('{\n  "schema_version": 1,\n  "baseline": {\n    "src/x.py": %d\n'
        '  },\n  "trees": {\n    "tests/": %d\n  }\n}\n')
TREES_ONLY = '{\n  "trees": {\n    "tests/": %d\n  }\n}\n'
LOOSE = '~ tests/: required 15, recorded 20'


def pushed_repo(tmp_path, records, files=TREE):
    repo = make_repo(tmp_path, files)
    if records is not None:
        add_and_commit(repo, RECORDS, records, 'carry the records')
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'clone', '-q', '--bare', str(repo), str(remote))
    return repo, head(repo), remote


def landed(remote, sha):
    """The records file of the one commit pushed on top of ``sha``."""
    tip = git(remote, 'rev-parse', 'main').strip()
    assert git(remote, 'rev-list', f'{sha}..{tip}').split() == [tip]
    assert git(remote, 'diff', '--name-only', sha, tip) == RECORDS + '\n'
    return git(remote, 'show', f'{tip}:{RECORDS}')


@pytest.mark.parametrize('shape', [BOTH.replace('%d', '520', 1), TREES_ONLY],
                         ids=['both', 'trees-only'])
def test_keyed_loose_trees_alone_are_committed(tmp_path, shape):
    repo, sha, remote = pushed_repo(tmp_path, shape % 20)
    done = deliver(repo, remote, **{'tree-budgets': 'tests/'})
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr
    assert f'  {LOOSE}\n' in done[MEASURE].stdout
    assert done[PUSH].returncode == 0, done[PUSH].stderr
    assert landed(remote, sha) == shape % 15


def test_keyed_loose_halves_land_in_one_commit(tmp_path):
    repo, sha, remote = pushed_repo(tmp_path, BOTH % (550, 20))
    done = deliver(repo, remote, **{'tree-budgets': 'tests/'})
    assert done[PUSH].returncode == 0, done[PUSH].stderr
    assert landed(remote, sha) == BOTH % (520, 15)


def test_keyed_tight_halves_push_nothing(tmp_path):
    repo, sha, remote = pushed_repo(tmp_path, BOTH % (520, 15))
    done = deliver(repo, remote, **{'tree-budgets': 'tests/'})
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr
    assert '1 tree budgets hold' in done[MEASURE].stdout
    assert PUSH not in done
    assert git(remote, 'rev-parse', 'main').strip() == sha


def test_keyed_without_a_records_file_creates_what_tighten_writes(tmp_path):
    files = {**TREE, 'src/x.py': 'x\n' * 600}
    repo, sha, remote = pushed_repo(tmp_path, None, files)
    # Without tree budgets the per-file half alone still seeds nothing.
    alone = deliver(repo, remote)
    assert 'nothing to verify' in alone[MEASURE].stdout
    assert PUSH not in alone
    done = deliver(repo, remote, **{'tree-budgets': 'tests/'})
    assert done[PUSH].returncode == 0, done[PUSH].stderr
    created = landed(remote, sha)
    assert created == ('{\n  "trees": {\n    "tests/": 15\n  },\n'
                       '  "schema_version": 1,\n  "baseline": {\n'
                       '    "src/x.py": 600\n  }\n}\n')
    assert run_gate(repo, 'tighten', tree_budgets='tests/').returncode == 0
    assert (repo / RECORDS).read_text() == created


def test_a_stale_staged_file_never_reaches_the_push(tmp_path):
    repo, sha, remote = pushed_repo(tmp_path, BOTH % (520, 20))
    temp = tmp_path / 'repo-runner-temp'
    temp.mkdir()
    (temp / 'size-ratchet-records').write_text(
        '{"baseline": {"src/x.py": 1}, "trees": {"tests/": 2}, "x": 3}\n')
    done = deliver(repo, remote, **{'tree-budgets': 'tests/'})
    assert done[PUSH].returncode == 0, done[PUSH].stderr
    assert landed(remote, sha) == BOTH % (520, 15)


def called(repo, secrets=None, extra_env=None, **inputs):
    return run_block_as_called(
        repo, 'push', 'refs/heads/main',
        {'repository': {'default_branch': 'main'}},
        environment_secrets=secrets, extra_env=extra_env,
        inputs={'tree-budgets': 'tests/', **inputs})[1]


def test_a_called_keyed_run_commits_the_loose_trees(tmp_path):
    # The caller's job binds main-push, whose secret reaches push-key.
    repo, sha, remote = pushed_repo(tmp_path, BOTH % (520, 20))
    done = called(repo, {'main-push': {'MASTER_PUSH_DEPLOY_KEY': 'k'}},
                  {'SIZE_RATCHET_PUSH_REMOTE': str(remote)})
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr
    assert '::notice::loose tree' not in done[MEASURE].stdout
    assert done[PUSH].returncode == 0, done[PUSH].stderr
    assert landed(remote, sha) == BOTH % (520, 15)


def keyless(repo, tight):
    return run_action(repo, {'tree-budgets': 'tests/', 'require-tight': tight},
                      push_context(repo))


@pytest.mark.parametrize('tight', ['false', 'true'])
def test_a_called_keyless_run_notes_each_loose_tree(tmp_path, tight):
    repo, _, _ = pushed_repo(tmp_path, BOTH % (520, 20))
    done = called(repo, **{'require-tight': tight})
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr
    assert done[MEASURE].stderr == ''
    assert (f'::notice::loose tree budget: {LOOSE} (a called run has no '
            "push-key; the block's standalone push run commits it)\n") \
        in done[MEASURE].stdout
    assert '::warning::' not in done[MEASURE].stdout
    assert PUSH not in done


def test_the_block_s_keyless_run_warns_on_a_loose_tree(tmp_path):
    repo, _, _ = pushed_repo(tmp_path, BOTH % (520, 20))
    done = keyless(repo, 'false')
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr
    assert (f'::warning::loose tree budget: {LOOSE} (push-key is unset, so '
            'no run commits it)\n') in done[MEASURE].stdout
    assert '::notice::loose tree' not in done[MEASURE].stdout
    assert PUSH not in done


def test_the_block_s_keyless_run_fails_a_loose_tree_when_tight(tmp_path):
    repo, _, _ = pushed_repo(tmp_path, BOTH % (520, 20))
    done = keyless(repo, 'true')
    assert done[MEASURE].returncode == 1
    assert f'    {LOOSE}\n' in done[MEASURE].stderr
    assert 'push-key is unset, so no run commits them' in done[MEASURE].stderr


@pytest.mark.parametrize('arm', ['keyed', 'called', 'false', 'true'])
def test_a_total_above_its_record_fails_every_arm(tmp_path, arm):
    # The per-file record is loose too: nothing of either half lands.
    repo, sha, remote = pushed_repo(tmp_path, BOTH % (550, 14))
    done = {'keyed': lambda: deliver(repo, remote, **{'tree-budgets': 'tests/'}),
            'called': lambda: called(repo)}.get(arm, lambda: keyless(repo, arm))()
    assert done[MEASURE].returncode == 1
    assert 'tests/: 15 lines above its record of 14' in done[MEASURE].stderr
    assert PUSH not in done
    assert git(remote, 'rev-parse', 'main').strip() == sha
