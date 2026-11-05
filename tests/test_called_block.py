"""The README install block as a called reusable workflow, run end to end."""
from __future__ import annotations

import pytest

import yaml

from tests._block_harness import (ACTION_ROOT, MEASURE, PUSH,
                                  block_environment, install_block, render,
                                  run_block_as_called)
from tests.helpers import add_and_commit, git, head, make_repo, put_records

RECORDS = '.github/size-baseline.json'


def test_a_called_pull_request_binds_no_environment_and_never_pushes(
        tmp_path):
    # No environment is bound, so the default-branch key never arrives.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600, 'src/y.py': 'b\n'})
    base = put_records(repo, {'src/x.py': 600})
    add_and_commit(repo, 'src/x.py', 'a\n' * 550, 'shrink x')
    payload = {'pull_request': {'base': {'sha': base},
                                'head': {'sha': head(repo)}},
               'repository': {'default_branch': 'master'}}
    assert block_environment({'event_name': 'pull_request',
                              'ref': 'refs/pull/5/merge', 'event': payload,
                              'ref_type': 'unknown', 'ref_name': '5/merge'}) == ''
    ran, done = run_block_as_called(
        repo, 'pull_request', 'refs/pull/5/merge', payload,
        environment_secrets={'master-push': {'MASTER_PUSH_DEPLOY_KEY': 'k'}})
    assert ran == 2
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr
    assert '::notice::src/x.py: stale (the file shrank below its record)' \
        in done[MEASURE].stdout
    assert PUSH not in done


@pytest.mark.parametrize('branch', ['master', 'trunk'])
def test_a_called_default_branch_push_commits_the_records(tmp_path, branch):
    # The job binds <branch>-push, whose secret reaches push-key.
    repo = make_repo(tmp_path, branch=branch,
                     files={'src/x.py': 'a\n' * 520})
    sha = put_records(repo, {'src/x.py': 550})
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'clone', '-q', '--bare', str(repo), str(remote))
    ran, done = run_block_as_called(
        repo, 'push', f'refs/heads/{branch}',
        {'repository': {'default_branch': branch}},
        environment_secrets={f'{branch}-push':
                             {'MASTER_PUSH_DEPLOY_KEY': 'k'}},
        extra_env={'SIZE_RATCHET_PUSH_REMOTE': str(remote)})
    assert ran == 2
    assert done[PUSH].returncode == 0, done[PUSH].stderr
    tip = git(remote, 'rev-parse', branch).strip()
    assert git(remote, 'rev-parse', f'{tip}^').strip() == sha
    assert '"src/x.py": 520' in git(remote, 'show', f'{tip}:{RECORDS}')


@pytest.mark.parametrize('tight', ['false', 'true'])
@pytest.mark.parametrize('stored,inherit', [(False, True), (True, False)])
def test_a_called_push_without_the_key_passes_on_loose_records(
        tmp_path, stored, inherit, tight):
    # No key in the environment, or a caller that does not pass it: the
    # block's own push run commits, so require-tight never fails this one.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 500})
    put_records(repo, {'src/x.py': 550})
    ran, done = run_block_as_called(
        repo, 'push', 'refs/heads/master',
        {'repository': {'default_branch': 'master'}}, inherit=inherit,
        environment_secrets={'master-push': {'MASTER_PUSH_DEPLOY_KEY': 'k'}}
        if stored else None, inputs={'require-tight': tight})
    assert ran == 2
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr
    assert done[MEASURE].stderr == ''
    assert ('::notice::loose record: - src/x.py: recorded 550 (a called '
            "run has no push-key; the block's standalone push run commits "
            'it)\n') in done[MEASURE].stdout
    assert '::warning::' not in done[MEASURE].stdout
    assert PUSH not in done


def test_the_block_s_name_is_the_one_run_the_action_treats_as_standalone():
    # The concurrency group and the action read the same name test.
    spec = yaml.safe_load((ACTION_ROOT / 'action.yml').read_text())
    called = [step for step in spec['runs']['steps'] if step.get('id') ==
              'measure'][0]['env']['SIZE_RATCHET_CALLED']
    assert render(called, {'workflow': install_block()['name']}, {}) == 'false'
    assert render(called, {'workflow': 'ci-gate'}, {}) == 'true'


@pytest.mark.parametrize('default,pushed,bound', [
    ('main', 'main', 'main-push'), ('master', 'master', 'master-push'),
    ('main', 'master', ''), ('master', 'main', ''), ('main', 'feature', '')])
def test_the_environment_binds_only_on_a_default_branch_push(
        default, pushed, bound):
    context = {'event_name': 'push', 'ref': f'refs/heads/{pushed}',
               'ref_type': 'branch', 'ref_name': pushed,
               'event': {'repository': {'default_branch': default}}}
    assert block_environment(context) == bound
    assert block_environment({**context, 'ref': f'refs/tags/{default}',
                              'ref_type': 'tag'}) == ''


def test_a_called_leg_on_any_other_caller_event_reports_success(tmp_path):
    # Item 2: the fold accepts the leg on every caller event. On an
    # event neither arm measures the steps skip and the job goes green
    # without running the action, whose auto mode would fail closed.
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n'})
    for event, ref in [('push', 'refs/heads/feature'),
                       ('workflow_dispatch', 'refs/heads/master'),
                       ('workflow_dispatch', 'refs/heads/feature')]:
        ran, done = run_block_as_called(
            repo, event, ref, {'repository': {'default_branch': 'master'}})
        assert ran == 0, (event, ref)
        assert done is None
