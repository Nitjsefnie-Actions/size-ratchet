"""The README install block as written, run end to end."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests._block_harness import (BLOCK_IF, block_step_if, block_with_inputs,
                                  eval_expression, install_block,
                                  MEASURE, PUSH,
                                  install_block_text, push_run_starts,
                                  run_block_as_called)
from tests.helpers import (add_and_commit, git, head, make_repo, put_records,
                           records, run_gate, write_files)

ACTION_ROOT = Path(__file__).resolve().parents[1]
RECORDS = '.github/size-baseline.json'


def pull_request(repo, base, **inputs):
    return run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'}, **inputs)


def test_the_block_resolves_the_default_branch_at_run_time():
    block = install_block()
    job = block['jobs']['size-baseline']
    assert 'if' not in job, 'the measurability condition lives on the steps'
    assert block_step_if() == BLOCK_IF
    trigger = block[True]
    assert sorted(trigger) == ['pull_request', 'push', 'workflow_call']
    assert trigger['push'] == {'branches': ['main', 'master']}
    assert trigger['pull_request'] in (None, {})
    assert trigger['workflow_call'] in (None, {})


def test_a_push_beside_pull_request_carries_a_branches_filter():
    trigger = install_block()[True]
    assert 'pull_request' in trigger and 'push' in trigger
    assert 'branches' in (trigger['push'] or {})


# (default branch, pushed branch, a push run starts, steps that run)
PUSH_RUNS = [
    ('main', 'main', True, 2), ('main', 'master', True, 0),
    ('main', 'trunk', False, 0), ('main', 'mainline', False, 0),
    ('master', 'master', True, 2), ('master', 'main', True, 0),
    ('master', 'master-fix', False, 0),
    # The documented limit: another default branch gets no standalone run.
    ('trunk', 'trunk', False, 0), ('trunk', 'main', True, 0),
    ('trunk', 'master', True, 0), ('trunk', 'feature/x', False, 0),
]


@pytest.mark.parametrize('default,pushed,starts,steps', PUSH_RUNS)
def test_a_branch_push_starts_a_run_only_on_main_or_master(
        tmp_path, default, pushed, steps, starts):
    # A newest-run reader on a PR head SHA finds no push run beside it
    # unless the head branch is main or master.
    ref = f'refs/heads/{pushed}'
    assert push_run_starts(ref) is starts
    if not starts:
        return
    repo = make_repo(tmp_path, branch=pushed, files={'src/x.py': 'a\n' * 500})
    put_records(repo, {'src/x.py': 550})
    ran, done = run_block_as_called(
        repo, 'push', ref, {'repository': {'default_branch': default}},
        workflow='size baseline')
    assert ran == steps
    if steps:
        # No key is bound in this model, so the loose entry is a warning.
        assert done[MEASURE].returncode == 0, done[MEASURE].stderr
        assert ('::warning::loose record: - src/x.py: recorded 550 '
                '(push-key is unset, so no run commits it)\n') \
            in done[MEASURE].stdout
        assert PUSH not in done


def test_a_tag_push_starts_no_run():
    assert not push_run_starts('refs/tags/main')


def test_the_concurrency_group_splits_a_called_run_from_the_standalone_run():
    # Item 3: no other run of the block cancels a called run.
    standalone_pr = {'workflow': 'size baseline', 'event_name': 'pull_request',
                     'ref': 'refs/pull/5/merge'}
    called = {'workflow': 'ci-gate', 'event_name': 'pull_request',
              'ref': 'refs/pull/5/merge', 'run_id': '3699691921'}
    later_called = {'workflow': 'ci-gate', 'event_name': 'pull_request',
                    'ref': 'refs/pull/5/merge', 'run_id': '3699698649'}
    group = install_block()['concurrency']['group']
    assert eval_expression(group, standalone_pr) == \
        'size-baseline-refs/pull/5/merge'
    assert eval_expression(group, called) == 'size-baseline-called-3699691921'
    assert eval_expression(group, standalone_pr) != eval_expression(group, called)
    assert eval_expression(group, called) != eval_expression(group, later_called)
    # GitHub string equality ignores case: a caller named Size Baseline
    # takes the standalone group too, which is the caveat the README names.
    assert eval_expression(
        group, {'workflow': 'Size Baseline', 'event_name': 'pull_request',
                'ref': 'refs/pull/5/merge'}) == \
        'size-baseline-refs/pull/5/merge'
    assert install_block()['concurrency']['cancel-in-progress'] is True


def test_a_push_run_cancels_no_other_commit_s_run():
    # The records commit's own push run shares no group with the run that
    # pushed it or with an earlier commit's run, and a pending run is
    # replaced even without cancel-in-progress, so each commit is its own
    # group; a pull request's newer run still replaces its older one.
    group = install_block()['concurrency']['group']

    def push(ref, sha):
        return eval_expression(group, {'workflow': 'size baseline',
                                       'event_name': 'push', 'ref': ref,
                                       'sha': sha})
    pushed, records = push('refs/heads/main', 'a1'), push('refs/heads/main', 'b2')
    assert pushed == 'size-baseline-refs/heads/main-a1'
    assert records == 'size-baseline-refs/heads/main-b2'
    # One commit pushed to both filtered branches is two runs.
    assert push('refs/heads/master', 'a1') == 'size-baseline-refs/heads/master-a1'
    # A called run on a caller's push keeps the caller's group, never the
    # standalone push run's.
    assert eval_expression(group, {'workflow': 'ci-gate', 'event_name': 'push',
                                   'ref': 'refs/heads/main', 'sha': 'a1',
                                   'run_id': '3699691921'}) == \
        'size-baseline-called-3699691921'

    def pull(sha):
        return eval_expression(group, {'workflow': 'size baseline',
                                       'event_name': 'pull_request',
                                       'ref': 'refs/pull/5/merge', 'sha': sha})
    assert pull('c3') == pull('d4') == 'size-baseline-refs/pull/5/merge'
    assert install_block()['concurrency']['cancel-in-progress'] is True


def test_the_block_shows_every_input_and_the_consumer_tree_budget():
    inputs = yaml.safe_load((ACTION_ROOT / 'action.yml').read_text())['inputs']
    shown = block_with_inputs()
    assert set(shown) == set(inputs)
    for name, spec in inputs.items():
        if name not in ('push-key', 'tree-budgets'):
            assert shown[name] == spec['default'], name
            continue
        if name == 'tree-budgets':
            assert inputs[name]['default'] == ''
            assert shown[name] == 'tests/'
    assert inputs['push-key']['default'] == ''
    assert shown['push-key'] == '${{ secrets.MASTER_PUSH_DEPLOY_KEY }}'


def test_the_own_gate_mirrors_the_block():
    """The own size-baseline job is the block with ./ pinned, except tree-budgets."""
    tests_yml = yaml.safe_load(
        (ACTION_ROOT / '.github' / 'workflows' / 'tests.yml').read_text())
    job = tests_yml['jobs']['size-baseline']
    assert 'if' not in job
    assert job['environment'] == \
        install_block()['jobs']['size-baseline']['environment']
    assert [step.get('if') for step in job['steps']] == \
        [step.get('if') for step in
         install_block()['jobs']['size-baseline']['steps']]
    own = [step for step in job['steps'] if step.get('uses') == './'][0]
    assert own['with'] == {**block_with_inputs(), 'tree-budgets': 'tests/'}
    checkout = [step for step in job['steps']
                if step.get('uses', '').startswith('actions/checkout')][0]
    block_checkout = [step for step in install_block()['jobs']
                      ['size-baseline']['steps']
                      if step.get('uses', '').startswith('actions/checkout')][0]
    assert checkout['with'] == block_checkout['with']
    assert 'workflow_dispatch' in tests_yml[True]


# --- the block's inputs, driven end to end -------------------------------


def test_a_pull_request_shrink_passes_under_the_block_defaults(tmp_path):
    # require-tight defaults to 'false': a pull request is never asked to
    # commit a lowered record, and growth past a record still fails.
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n' * 600})
    base = put_records(repo, {'src/x.py': 600})
    add_and_commit(repo, 'src/x.py', 'a\n' * 550, 'shrink x')
    done = pull_request(repo, base)
    assert done.returncode == 0, done.stderr
    assert ('::notice::src/x.py: stale (the file shrank below its record); '
            'recorded 600, now 550\n') in done.stdout
    add_and_commit(repo, 'src/x.py', 'a\n' * 601, 'grow x')
    done = pull_request(repo, base)
    assert done.returncode == 1
    assert 'src/x.py: 601 lines above its record of 600' in done.stderr
    assert 'lowers below' not in done.stderr


def test_a_pull_request_shrink_must_carry_the_record(tmp_path):
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n' * 600, 'src/y.py': 'b\n'})
    base = put_records(repo, {'src/x.py': 600})
    add_and_commit(repo, 'src/x.py', 'a\n' * 550, 'shrink x')
    done = pull_request(repo, base, require_tight='true')
    assert done.returncode == 1
    assert '~ src/x.py: required 550, recorded 600' in done.stderr
    put_records(repo, {'src/x.py': 550})
    done = pull_request(repo, base, require_tight='true')
    assert done.returncode == 0, done.stderr


def test_two_concurrent_shrinks_merge_then_an_unrelated_pr_passes(tmp_path):
    # Item 3: after two concurrent shrinks merge, the record sits above
    # the file; a later pull request that touches neither passes, the
    # loose entry a stale notice, not its failure.
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n' * 600, 'src/y.py': 'b\n'})
    base = put_records(repo, {'src/x.py': 600})
    add_and_commit(repo, 'src/x.py', 'a\n' * 550, 'shrink x')
    put_records(repo, {'src/x.py': 550})
    done = pull_request(repo, base, require_tight='true')
    assert done.returncode == 0, done.stderr
    # The merged outcome: 550 recorded, 520 lines, over the ceiling.
    add_and_commit(repo, 'src/x.py', 'a\n' * 520, 'the second shrink lands')
    base_after = head(repo)
    add_and_commit(repo, 'src/y.py', 'b\nb\nb\n', 'the unrelated pull request')
    done = pull_request(repo, base_after, require_tight='true')
    assert done.returncode == 0, done.stderr
    assert '::notice::src/x.py: stale (the file shrank below its record)' \
        in done.stdout


def test_the_default_branch_run_reports_a_loose_entry(tmp_path):
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n' * 500})
    put_records(repo, {'src/x.py': 550})
    done = run_gate(repo, 'auto', require_tight='true', default_branch='master', extra_env={
        'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/master'})
    assert done.returncode == 1
    # 500 is at the ceiling, so the record graduates: tighten writes a
    # drop, not a lowering.
    assert '- src/x.py: recorded 550' in done.stderr
    assert 'records are loose' in done.stderr


def test_a_loose_merge_under_require_tight_false_leaves_main_green(tmp_path):
    # Under 'false' a pull request shrinks one recorded file and
    # deletes another without touching the records; the merged tree's
    # default-branch run names each loose entry as a notice and passes,
    # while 'true' over the same tree still fails naming them.
    repo = make_repo(tmp_path, branch='master', files={
        'src/x.py': 'a\n' * 905, 'src/y.py': 'b\n' * 600})
    base = put_records(repo, {'src/x.py': 905, 'src/y.py': 600})
    add_and_commit(repo, 'src/x.py', 'a\n' * 900, 'shrink x')
    git(repo, 'rm', '-q', 'src/y.py')
    git(repo, 'commit', '-qm', 'delete y')
    done = pull_request(repo, base, require_tight='false')
    assert done.returncode == 0, done.stderr
    assert ('::notice::src/x.py: stale (the file shrank below its record); '
            'recorded 905, now 900\n') in done.stdout
    push = {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/master'}
    done = run_gate(repo, 'auto', default_branch='master',
                    require_tight='false', extra_env=push)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ''
    assert ('::warning::loose record: ~ src/x.py: required 900, recorded 905 '
            '(push-key is unset, so no run commits it)\n' in done.stdout)
    assert ('::warning::loose record: - src/y.py: recorded 600 '
            '(push-key is unset, so no run commits it)\n' in done.stdout)
    done = run_gate(repo, 'auto', default_branch='master',
                    require_tight='true', extra_env=push)
    assert done.returncode == 1
    assert '~ src/x.py: required 900, recorded 905' in done.stderr
    assert '- src/y.py: recorded 600' in done.stderr


def test_a_touching_pull_request_must_make_the_entry_exact(tmp_path):
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n' * 500})
    base = put_records(repo, {'src/x.py': 550})
    add_and_commit(repo, 'src/x.py', 'a\n' * 501, 'touch the loose file')
    done = pull_request(repo, base, require_tight='true')
    assert done.returncode == 1
    assert '~ src/x.py: required 501, recorded 550' in done.stderr
    put_records(repo, {'src/x.py': 501})
    done = pull_request(repo, base, require_tight='true')
    assert done.returncode == 0, done.stderr


def test_a_push_to_another_branch_never_reaches_the_gate(tmp_path):
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n'})
    add_and_commit(repo, 'src/x.py', 'b\n', 'a branch commit')
    done = run_gate(repo, 'auto', require_tight='true', default_branch='master', extra_env={
        'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/feature'})
    assert done.returncode == 2
    assert 'cannot handle event' in done.stderr


@pytest.mark.parametrize('wrong', [None, 502, 626])
def test_a_seed_entry_the_head_adds_must_be_the_exact_count(tmp_path, wrong):
    # Seeding against an empty base map: the entry must be exact.
    repo = make_repo(tmp_path, branch='master', files={'src/over.py': 'x\n' * 627})
    base = put_records(repo, {})
    put_records(repo, {'src/over.py': wrong or 627})
    done = pull_request(repo, base, require_tight='true')
    assert done.returncode == int(wrong is not None), done.stderr
    if wrong is None:
        return
    # A wrong seed is not a seed: the ceiling violation names the real
    # count and require-tight refuses the entry.
    assert 'src/over.py: 627 lines above its ceiling of 500' in done.stderr
    assert f'- src/over.py: recorded {wrong}' in done.stderr


def test_a_hand_entry_for_an_under_ceiling_file_is_refused(tmp_path):
    repo = make_repo(tmp_path, branch='master', files={'src/small.py': 'x\n'})
    base = put_records(repo, {})
    put_records(repo, {'src/small.py': 1})
    done = pull_request(repo, base, require_tight='true')
    assert done.returncode == 1
    assert '- src/small.py: recorded 1' in done.stderr


def test_a_records_file_deletion_fails_naming_every_entry(tmp_path):
    # A pull request that deletes the records file itself owes every
    # dropped entry named, not a traceback.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600})
    base = put_records(repo, {'src/x.py': 600})
    git(repo, 'rm', '-q', RECORDS)
    git(repo, 'commit', '-qm', 'drop records')
    done = pull_request(repo, base, require_tight='true')
    assert done.returncode == 1
    assert '+ src/x.py: record 600' in done.stderr
    assert 'Traceback' not in done.stderr


def test_a_ceiling_raise_without_a_count_move_is_a_notice(tmp_path):
    # A ceiling raise with no count move is the one stale shape that
    # stays a notice; the default branch's run names the drop.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600})
    base = put_records(repo, {'src/x.py': 600})
    done = pull_request(repo, base, require_tight='true', ceilings='source: 700\n')
    assert done.returncode == 0, done.stderr
    assert '::notice::src/x.py: stale (the file is at or below its ceiling)' \
        in done.stdout


@pytest.mark.parametrize('tight', ['true', 'false'])
@pytest.mark.parametrize('change,inputs', [
    # Excluding a recorded file is a change the pull request made: the
    # entry must go in the same pull request, not a notice on main.
    ('exclude', {'exclude': 'src/x.py\n'}),
    ('shrink below the ceiling', {}),
    ('delete', {}),
])
def test_a_pr_that_unmeasures_or_graduates_the_file_must_drop_its_entry(
        tmp_path, change, inputs, tight):
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600, 'src/y.py': 'b\n'})
    base = put_records(repo, {'src/x.py': 600})
    if change == 'delete':
        git(repo, 'rm', '-q', 'src/x.py')
        git(repo, 'commit', '-qm', 'delete x')
    elif change != 'exclude':
        add_and_commit(repo, 'src/x.py', 'a\n' * 400, change)
    done = pull_request(repo, base, require_tight=tight, **inputs)
    if tight == 'false':
        assert done.returncode == 0, done.stderr
        # So does dropping the entry, or an excluded file's kept below it.
        for kept in [{}, {'src/x.py': 500}] if change == 'exclude' else [{}]:
            put_records(repo, kept)
            done = pull_request(repo, base, require_tight=tight, **inputs)
            assert done.returncode == 0, done.stderr
        return
    assert done.returncode == 1
    assert '- src/x.py: recorded 600' in done.stderr


def test_an_empty_recorded_file_is_neither_absent_nor_ignored(tmp_path):
    # The sentinel separates an absent measurement from an empty file's 0:
    # deleting a recorded empty file demands the drop all the same.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/e.py': '', 'src/y.py': 'b\n'})
    base = put_records(repo, {'src/e.py': 0})
    git(repo, 'rm', '-q', 'src/e.py')
    git(repo, 'commit', '-qm', 'delete empty')
    done = pull_request(repo, base, require_tight='true')
    assert done.returncode == 1
    assert '- src/e.py: recorded 0' in done.stderr


def test_an_added_entry_on_a_nonempty_base_is_refused(tmp_path):
    # Seeds happen only against an empty base map: against any other base
    # the file was bound by its ceiling, and a records edit in the head
    # cannot loosen the gate by recording a new file at its real size.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600})
    base = put_records(repo, {'src/x.py': 600})
    add_and_commit(repo, 'src/huge.py', 'h\n' * 2000, 'add a huge file')
    put_records(repo, {'src/x.py': 600, 'src/huge.py': 2000})
    done = pull_request(repo, base, require_tight='true')
    assert done.returncode == 1
    assert 'src/huge.py: 2000 lines above its ceiling of 500' in done.stderr
    assert '- src/huge.py: recorded 2000' in done.stderr


def test_a_grown_unrecorded_file_cannot_record_itself(tmp_path):
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600, 'src/y.py': 'b\n' * 400})
    base = put_records(repo, {'src/x.py': 600})
    add_and_commit(repo, 'src/y.py', 'b\n' * 900, 'grow y past the ceiling')
    put_records(repo, {'src/x.py': 600, 'src/y.py': 900})
    done = pull_request(repo, base, require_tight='true')
    assert done.returncode == 1
    assert 'src/y.py: 900 lines above its ceiling of 500' in done.stderr
    assert '- src/y.py: recorded 900' in done.stderr
    assert 'never raised by hand' in done.stderr and 'that is what tighten writes' in done.stderr
