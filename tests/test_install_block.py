"""The README install block as written, run end to end."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.helpers import (add_and_commit, head, make_repo, put_records,
                           run_gate)

ACTION_ROOT = Path(__file__).resolve().parents[1]
RECORDS = '.github/size-baseline.json'


def install_block_text():
    """The README's single yaml fence."""
    text = (ACTION_ROOT / 'README.md').read_text()
    fences = re.findall(r'```yaml\n(.*?)```', text, re.S)
    assert len(fences) == 1, 'the README carries exactly one yaml block'
    return fences[0]


def install_block():
    return yaml.safe_load(install_block_text())


def block_with_inputs():
    """The block's `with:` mapping, as the consumer copies it."""
    steps = install_block()['jobs']['size-baseline']['steps']
    return [step for step in steps if 'uses' in step
            and 'size-ratchet' in step['uses']][0]['with']


BLOCK_IF = ("github.event_name == 'pull_request' || "
            "(github.event_name == 'push' && github.ref_type == 'branch' && "
            'github.ref_name == github.event.repository.default_branch)')


def block_step_if():
    """The block's measurability condition, carried by both steps."""
    steps = install_block()['jobs']['size-baseline']['steps']
    ifs = [step.get('if') for step in steps]
    assert len(ifs) == 2 and ifs[0] == ifs[1] and ifs[0]
    return ifs[0]


def eval_expression(text, github):
    """Evaluate the subset of GitHub expressions the block uses; anything
    the grammar does not name is refused, never empty-matched."""
    tokens = re.findall(
        r'\(|\)|,|==|!=|&&|\|\||\'[^\']*\'|[A-Za-z_][\w.]*|\d+', text)
    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else None

    def take():
        nonlocal position
        token = tokens[position]
        position += 1
        return token

    def primary():
        token = take()
        if token == '(':
            value = disjunction()
            assert take() == ')'
            return value
        if token.startswith("'"):
            return token[1:-1]
        if token == 'format':
            assert take() == '('
            template = primary()
            arguments = []
            while peek() == ',':
                take()
                arguments.append(primary())
            assert take() == ')'
            for index, value in enumerate(arguments):
                template = template.replace('{%d}' % index, str(value))
            return template
        if token == 'github' or token.startswith('github.'):
            value = github
            for part in token.split('.')[1:]:
                value = (value or {}).get(part) if isinstance(value, dict) \
                    else None
            return value
        raise AssertionError(f'unrecognized token {token!r} in {text!r}')

    def equal(left, right):
        # GitHub ignores case when comparing strings.
        if isinstance(left, str) and isinstance(right, str):
            return left.lower() == right.lower()
        return left == right

    def comparison():
        value = primary()
        while peek() in ('==', '!='):
            operator = take()
            value = equal(value, primary()) if operator == '==' \
                else not equal(value, primary())
        return value

    def conjunction():
        # Parsed eagerly, then GitHub's value semantics applied: && keeps
        # the right operand when the left is truthy, || the first truthy.
        value = comparison()
        while peek() == '&&':
            take()
            right = comparison()
            value = right if value else False
        return value

    def disjunction():
        value = conjunction()
        while peek() == '||':
            take()
            right = conjunction()
            value = value if value else right
        return value

    value = disjunction()
    assert position == len(tokens), f'unparsed tail in {text!r}'
    return value


def run_block_as_called(repo, event, ref, payload, workflow='ci-gate'):
    """Drive the block as a caller's run would; returns (steps that ran,
    the gate's result or None)."""
    block = install_block()
    assert 'workflow_call' in block[True], 'the block is no longer callable'
    github_context = {
        'workflow': workflow, 'event_name': event, 'ref': ref,
        'ref_type': {'heads': 'branch', 'tags': 'tag'}.get(
            ref[len('refs/'):].partition('/')[0], 'unknown'),
        'ref_name': ref[len('refs/'):].partition('/')[2],
        'event': payload, 'run_id': '3699691921',
    }
    ran, result = 0, None
    for step in block['jobs']['size-baseline']['steps']:
        if 'if' in step and not eval_expression(step['if'], github_context):
            continue
        ran += 1
        if 'size-ratchet' in step.get('uses', ''):
            result = run_gate(
                repo, block_with_inputs()['mode'],
                base_ref=payload.get('pull_request', {}).get('base', {})
                .get('sha'),
                default_branch=payload['repository']['default_branch'],
                extra_env={'GITHUB_EVENT_NAME': event, 'GITHUB_REF': ref,
                           'GITHUB_WORKFLOW': workflow})
    return ran, result


def test_the_block_resolves_the_default_branch_at_run_time():
    block = install_block()
    job = block['jobs']['size-baseline']
    assert 'if' not in job, 'the measurability condition lives on the steps'
    assert block_step_if() == BLOCK_IF
    trigger = block[True]
    assert sorted(trigger) == ['pull_request', 'push', 'workflow_call']
    # An unfiltered trigger: the steps' `if` resolves the default branch.
    assert trigger['push'] in (None, {})
    assert trigger['pull_request'] in (None, {})
    assert trigger['workflow_call'] in (None, {})


def test_the_concurrency_group_splits_a_called_run_from_the_standalone_run():
    # Item 3: no other run of the block cancels a called run.
    standalone_pr = {'workflow': 'size baseline', 'ref': 'refs/pull/5/merge'}
    called = {'workflow': 'ci-gate', 'ref': 'refs/pull/5/merge',
              'run_id': '3699691921'}
    later_called = {'workflow': 'ci-gate', 'ref': 'refs/pull/5/merge',
                    'run_id': '3699698649'}
    group = install_block()['concurrency']['group']
    assert eval_expression(group, standalone_pr) == \
        'size-baseline-refs/pull/5/merge'
    assert eval_expression(group, called) == 'size-baseline-called-3699691921'
    assert eval_expression(group, standalone_pr) != eval_expression(group, called)
    assert eval_expression(group, called) != eval_expression(group, later_called)
    assert eval_expression(
        group, {'workflow': 'size baseline', 'ref': 'refs/heads/master'}) == \
        'size-baseline-refs/heads/master'
    # GitHub string equality ignores case: a caller named Size Baseline
    # takes the standalone group too, which is the caveat the README names.
    assert eval_expression(
        group, {'workflow': 'Size Baseline', 'ref': 'refs/heads/master'}) == \
        'size-baseline-refs/heads/master'
    assert install_block()['concurrency']['cancel-in-progress'] is True


def test_the_block_matches_the_action_defaults():
    inputs = yaml.safe_load((ACTION_ROOT / 'action.yml').read_text())['inputs']
    shown = block_with_inputs()
    assert set(shown) == set(inputs)
    for name, spec in inputs.items():
        assert shown[name] == spec['default'], name


def test_the_own_gate_mirrors_the_block():
    """The repository's own size-baseline job is the block, with ./ pinned."""
    tests_yml = yaml.safe_load(
        (ACTION_ROOT / '.github' / 'workflows' / 'tests.yml').read_text())
    job = tests_yml['jobs']['size-baseline']
    assert 'if' not in job
    assert [step.get('if') for step in job['steps']] == \
        [step.get('if') for step in
         install_block()['jobs']['size-baseline']['steps']]
    own = [step for step in job['steps'] if step.get('uses') == './'][0]
    assert own['with'] == block_with_inputs()
    checkout = [step for step in job['steps']
                if step.get('uses', '').startswith('actions/checkout')][0]
    block_checkout = [step for step in install_block()['jobs']
                      ['size-baseline']['steps']
                      if step.get('uses', '').startswith('actions/checkout')][0]
    assert checkout['with'] == block_checkout['with']


def test_no_workflow_dispatch_trigger_remains_in_tests():
    tests_yml = yaml.safe_load(
        (ACTION_ROOT / '.github' / 'workflows' / 'tests.yml').read_text())
    assert 'workflow_dispatch' not in tests_yml[True]


# --- the block's inputs, driven end to end -------------------------------


def test_a_pull_request_shrink_must_carry_the_record(tmp_path):
    # The block's defaults: require-tight is on and scoped.
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n' * 600, 'src/y.py': 'b\n'})
    base = put_records(repo, {'src/x.py': 600})
    add_and_commit(repo, 'src/x.py', 'a\n' * 550, 'shrink x')
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 1
    assert '~ src/x.py: required 550, recorded 600' in done.stderr
    put_records(repo, {'src/x.py': 550})
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 0, done.stderr


def test_two_concurrent_shrinks_merge_then_an_unrelated_pr_passes(tmp_path):
    # Item 3: after two concurrent shrinks merge, the record sits above
    # the file; a later pull request that touches neither passes, the
    # loose entry a stale notice, not its failure.
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n' * 600, 'src/y.py': 'b\n'})
    base = put_records(repo, {'src/x.py': 600})
    # PR A's head: shrink + exact record -> passes.
    add_and_commit(repo, 'src/x.py', 'a\n' * 550, 'shrink x')
    put_records(repo, {'src/x.py': 550})
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 0, done.stderr
    # The merged outcome: 550 recorded, 520 lines, over the ceiling.
    add_and_commit(repo, 'src/x.py', 'a\n' * 520, 'the second shrink lands')
    # PR C: touches only y.py, never x.py or its record.
    base_after = head(repo)
    add_and_commit(repo, 'src/y.py', 'b\nb\nb\n', 'the unrelated pull request')
    done = run_gate(repo, 'auto', base_ref=base_after,
                    default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 0, done.stderr
    assert '::notice::src/x.py: stale (the file shrank below its record)' \
        in done.stdout


def test_the_default_branch_run_reports_a_loose_entry(tmp_path):
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n' * 500})
    put_records(repo, {'src/x.py': 550})
    done = run_gate(repo, 'auto', default_branch='master', extra_env={
        'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/master'})
    assert done.returncode == 1
    # 500 is at the ceiling, so the record graduates: tighten writes a
    # drop, not a lowering.
    assert '- src/x.py: recorded 550' in done.stderr
    assert 'records are loose' in done.stderr


def test_a_touching_pull_request_must_make_the_entry_exact(tmp_path):
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n' * 500})
    base = put_records(repo, {'src/x.py': 550})
    # Growing the file touches the entry: it must become exact.
    add_and_commit(repo, 'src/x.py', 'a\n' * 501, 'touch the loose file')
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 1
    assert '~ src/x.py: required 501, recorded 550' in done.stderr
    put_records(repo, {'src/x.py': 501})
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 0, done.stderr


def test_a_push_to_another_branch_never_reaches_the_gate(tmp_path):
    # The action itself still fails closed naming the event.
    repo = make_repo(tmp_path, branch='master', files={'src/x.py': 'a\n'})
    add_and_commit(repo, 'src/x.py', 'b\n', 'a branch commit')
    done = run_gate(repo, 'auto', default_branch='master', extra_env={
        'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/feature'})
    assert done.returncode == 2
    assert 'cannot handle event' in done.stderr


# --- the block as a called reusable workflow -----------------------------


def test_a_called_pull_request_runs_the_check_a_direct_run_runs(tmp_path):
    # Item 6, the caller on a pull request: the called leg checks the
    # pull request, the same check the block's own pull-request run does.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600, 'src/y.py': 'b\n'})
    base = put_records(repo, {'src/x.py': 600})
    add_and_commit(repo, 'src/x.py', 'a\n' * 550, 'shrink x')
    ran, done = run_block_as_called(
        repo, 'pull_request', 'refs/pull/5/merge',
        {'pull_request': {'base': {'sha': base}, 'head': {'sha': head(repo)}},
         'repository': {'default_branch': 'master'}})
    assert ran == 2
    assert done.returncode == 1
    assert '~ src/x.py: required 550, recorded 600' in done.stderr


def test_a_called_default_branch_push_runs_the_verification(tmp_path):
    # Item 6, the caller on a default-branch push: the leg verifies,
    # exactly as the block's own default-branch run does.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 500})
    put_records(repo, {'src/x.py': 550})
    ran, done = run_block_as_called(
        repo, 'push', 'refs/heads/master',
        {'repository': {'default_branch': 'master'}})
    assert ran == 2
    assert done.returncode == 1
    assert 'records are loose' in done.stderr


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


def test_an_exact_seed_entry_the_head_adds_is_accepted(tmp_path):
    # Seeding against an empty base map: the entry must be exact.
    repo = make_repo(tmp_path, branch='master', files={'src/over.py': 'x\n' * 627})
    base = put_records(repo, {})
    put_records(repo, {'src/over.py': 627})
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize('wrong', [502, 626])
def test_a_wrong_seed_number_fails_naming_the_exact_count(tmp_path, wrong):
    repo = make_repo(tmp_path, branch='master', files={'src/over.py': 'x\n' * 627})
    base = put_records(repo, {})
    put_records(repo, {'src/over.py': wrong})
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 1
    # A wrong seed is not a seed: the ceiling violation names the real
    # count and require-tight refuses the entry.
    assert 'src/over.py: 627 lines above its ceiling of 500' in done.stderr
    assert f'- src/over.py: recorded {wrong}' in done.stderr


def test_a_hand_entry_for_an_under_ceiling_file_is_refused(tmp_path):
    repo = make_repo(tmp_path, branch='master', files={'src/small.py': 'x\n'})
    base = put_records(repo, {})
    put_records(repo, {'src/small.py': 1})
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 1
    assert '- src/small.py: recorded 1' in done.stderr


def test_a_records_file_deletion_fails_naming_every_entry(tmp_path):
    # A pull request that deletes the records file itself owes every
    # dropped entry named, not a traceback.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600})
    base = put_records(repo, {'src/x.py': 600})
    git_rm = ['git', '-C', str(repo), 'rm', '-q', RECORDS]
    import subprocess
    assert subprocess.run(git_rm, check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'drop records'],
                   check=True, capture_output=True)
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 1
    # The head carries no records, so the required entry reads as missing.
    assert '+ src/x.py: record 600' in done.stderr
    assert 'Traceback' not in done.stderr


def test_an_exclusion_pr_must_drop_the_entry(tmp_path):
    # Excluding a recorded file is a change the pull request made: the
    # entry must go in the same pull request, not a notice on main.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600})
    base = put_records(repo, {'src/x.py': 600})
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    exclude='src/x.py\n',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 1
    assert '- src/x.py: recorded 600' in done.stderr


def test_a_ceiling_raise_without_a_count_move_is_a_notice(tmp_path):
    # A ceiling raise with no count move is the one stale shape that
    # stays a notice; the default branch's run names the drop.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600})
    base = put_records(repo, {'src/x.py': 600})
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    ceilings='source: 700\n',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 0, done.stderr
    assert '::notice::src/x.py: stale (the file is at or below its ceiling)' \
        in done.stdout


def test_a_shrink_below_the_ceiling_must_drop_the_entry(tmp_path):
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600})
    base = put_records(repo, {'src/x.py': 600})
    add_and_commit(repo, 'src/x.py', 'a\n' * 400, 'shrink below the ceiling')
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 1
    assert '- src/x.py: recorded 600' in done.stderr


def test_a_pr_that_deletes_the_file_must_drop_its_entry(tmp_path):
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600, 'src/y.py': 'b\n'})
    base = put_records(repo, {'src/x.py': 600})
    import subprocess
    subprocess.run(['git', '-C', str(repo), 'rm', '-q', 'src/x.py'],
                   check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'delete x'],
                   check=True, capture_output=True)
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 1
    assert '- src/x.py: recorded 600' in done.stderr


def test_an_empty_recorded_file_is_neither_absent_nor_ignored(tmp_path):
    # The sentinel separates an absent measurement from an empty file's 0:
    # deleting a recorded empty file demands the drop all the same.
    repo = make_repo(tmp_path, branch='master',
                     files={'src/e.py': '', 'src/y.py': 'b\n'})
    base = put_records(repo, {'src/e.py': 0})
    import subprocess
    subprocess.run(['git', '-C', str(repo), 'rm', '-q', 'src/e.py'],
                   check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'delete empty'],
                   check=True, capture_output=True)
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
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
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 1
    assert 'src/huge.py: 2000 lines above its ceiling of 500' in done.stderr
    assert '- src/huge.py: recorded 2000' in done.stderr


def test_a_grown_unrecorded_file_cannot_record_itself(tmp_path):
    repo = make_repo(tmp_path, branch='master',
                     files={'src/x.py': 'a\n' * 600, 'src/y.py': 'b\n' * 400})
    base = put_records(repo, {'src/x.py': 600})
    add_and_commit(repo, 'src/y.py', 'b\n' * 900, 'grow y past the ceiling')
    put_records(repo, {'src/x.py': 600, 'src/y.py': 900})
    done = run_gate(repo, 'auto', base_ref=base, default_branch='master',
                    extra_env={'GITHUB_EVENT_NAME': 'pull_request'})
    assert done.returncode == 1
    assert 'src/y.py: 900 lines above its ceiling of 500' in done.stderr
    assert '- src/y.py: recorded 900' in done.stderr
