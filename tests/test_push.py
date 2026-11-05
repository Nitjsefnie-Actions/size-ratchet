"""The default-branch push: the action's steps run as the runner runs them,
against a disposable bare remote."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from tests._block_harness import MEASURE, PUSH, run_action
from tests.helpers import add_and_commit, git, head, make_repo, put_records

RECORDS = '.github/size-baseline.json'
BOT = 'github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>'


def push_context(repo, branch='main', sha=None):
    return {'event_name': 'push', 'ref': f'refs/heads/{branch}',
            'ref_type': 'branch', 'ref_name': branch,
            'sha': sha or head(repo),
            'event': {'repository': {'default_branch': branch}}}


def loose_repo(tmp_path):
    """A main whose one record is loose by 30 lines, and its bare remote."""
    repo = make_repo(tmp_path, {'src/x.py': 'a\n' * 520, 'src/y.py': 'b\n'})
    sha = put_records(repo, {'src/x.py': 550})
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'clone', '-q', '--bare', str(repo), str(remote))
    return repo, sha, remote


def deliver(repo, remote, key='dummy-key', sha=None, **inputs):
    return run_action(repo, {'push-key': key, **inputs},
                      push_context(repo, sha=sha),
                      {'SIZE_RATCHET_PUSH_REMOTE': str(remote)})


def test_a_loose_record_is_committed_on_top_of_the_pushed_sha(tmp_path):
    repo, sha, remote = loose_repo(tmp_path)
    # No hook of the checkout runs beside the key.
    hook = repo / '.git' / 'hooks' / 'pre-push'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    done = deliver(repo, remote)
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr
    assert '~ src/x.py: required 520, recorded 550' in done[MEASURE].stdout
    assert done[PUSH].returncode == 0, done[PUSH].stderr
    tip = git(remote, 'rev-parse', 'main').strip()
    assert git(remote, 'rev-parse', f'{tip}^').strip() == sha
    assert git(remote, 'diff', '--name-only', sha, tip) == RECORDS + '\n'
    assert git(remote, 'show', f'{tip}:{RECORDS}') == (
        '{\n  "schema_version": 1,\n  "baseline": {\n'
        '    "src/x.py": 520\n  }\n}\n')
    assert git(remote, 'log', '-1', '--format=%an <%ae>|%cn <%ce>|%B', tip) \
        == f'{BOT}|{BOT}|ci: tighten size-ratchet records\n\n'
    # The checkout itself is untouched: only objects were written.
    assert head(repo) == sha
    assert git(repo, 'status', '--porcelain') == ''


def test_the_records_commit_s_own_run_pushes_nothing(tmp_path):
    repo, _, remote = loose_repo(tmp_path)
    assert deliver(repo, remote)[PUSH].returncode == 0
    landed = git(remote, 'rev-parse', 'main').strip()
    git(repo, 'fetch', '-q', str(remote), 'main')
    git(repo, 'checkout', '-q', landed)
    done = deliver(repo, remote)
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr
    assert 'nothing to push' in done[MEASURE].stdout
    assert PUSH not in done
    assert git(remote, 'rev-parse', 'main').strip() == landed


def test_a_branch_that_moved_passes_and_pushes_nothing(tmp_path):
    repo, sha, remote = loose_repo(tmp_path)
    other = make_repo(tmp_path, {'z': 'z\n'}, name='other')
    git(other, 'fetch', '-q', str(remote), 'main')
    git(other, 'reset', '-q', '--hard', 'FETCH_HEAD')
    newer = add_and_commit(other, 'src/y.py', 'c\n', 'a newer push')
    git(other, 'push', '-q', str(remote), 'HEAD:main')
    done = deliver(repo, remote, sha=sha)
    assert done[PUSH].returncode == 0, done[PUSH].stderr
    assert f'::notice::main moved past {sha}' in done[PUSH].stdout
    assert git(remote, 'rev-parse', 'main').strip() == newer


def test_a_refusal_while_the_branch_stands_still_fails_naming_it(tmp_path):
    repo, sha, remote = loose_repo(tmp_path)
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho "ruleset says no" >&2\nexit 1\n')
    hook.chmod(0o755)
    done = deliver(repo, remote)
    assert done[PUSH].returncode == 2
    assert f'refused, and the remote does not show it moved past {sha}' \
        in done[PUSH].stderr
    assert 'ruleset says no' in done[PUSH].stderr
    assert git(remote, 'rev-parse', 'main').strip() == sha


def test_a_checkout_that_is_not_github_sha_fails_without_pushing(tmp_path):
    repo, sha, remote = loose_repo(tmp_path)
    add_and_commit(repo, 'src/y.py', 'd\n', 'a commit the run was not for')
    done = deliver(repo, remote, sha=sha)
    assert done[MEASURE].returncode == 2
    assert f'is not github.sha {sha}' in done[MEASURE].stderr
    assert PUSH not in done
    assert git(remote, 'rev-parse', 'main').strip() == sha


def test_a_tight_checkout_that_is_not_github_sha_fails_too(tmp_path):
    repo = make_repo(tmp_path, {'src/x.py': 'a\n' * 520})
    sha = put_records(repo, {'src/x.py': 520})
    add_and_commit(repo, 'src/y.py', 'd\n', 'a commit the run was not for')
    done = deliver(repo, tmp_path / 'absent.git', sha=sha)
    assert done[MEASURE].returncode == 2
    assert f'is not github.sha {sha}' in done[MEASURE].stderr


def test_a_dotted_records_path_still_pushes(tmp_path):
    repo, sha, remote = loose_repo(tmp_path)
    done = deliver(repo, remote, records='./' + RECORDS)
    assert done[PUSH].returncode == 0, done[PUSH].stderr
    tip = git(remote, 'rev-parse', 'main').strip()
    assert git(remote, 'diff', '--name-only', sha, tip) == RECORDS + '\n'


def test_growth_on_the_default_branch_is_kept_not_failed(tmp_path):
    # As on v2.0.4's default-branch run: a grown record is never raised and
    # never fails the run; the other entries still tighten.
    repo = make_repo(tmp_path, {'src/x.py': 'a\n' * 520, 'src/z.py': 'z\n' * 600})
    sha = put_records(repo, {'src/x.py': 550, 'src/z.py': 590})
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'clone', '-q', '--bare', str(repo), str(remote))
    done = deliver(repo, remote)
    assert done[PUSH].returncode == 0, done[PUSH].stderr
    tip = git(remote, 'rev-parse', 'main').strip()
    assert git(remote, 'rev-parse', f'{tip}^').strip() == sha
    assert '"src/x.py": 520,\n    "src/z.py": 590\n' in \
        git(remote, 'show', f'{tip}:{RECORDS}')


def test_an_empty_key_stages_nothing(tmp_path):
    repo, sha, remote = loose_repo(tmp_path)
    for tight, status in (('false', 0), ('true', 1)):
        done = deliver(repo, remote, key='', **{'require-tight': tight})
        assert done[MEASURE].returncode == status, done[MEASURE].stderr
        assert 'push-key is unset' in done[MEASURE].stdout + \
            done[MEASURE].stderr
        assert PUSH not in done
    assert git(remote, 'rev-parse', 'main').strip() == sha


def test_a_pull_request_never_pushes(tmp_path):
    repo, base, remote = loose_repo(tmp_path)
    add_and_commit(repo, 'src/x.py', 'a\n' * 510, 'shrink x')
    context = {'event_name': 'pull_request', 'ref': 'refs/pull/5/merge',
               'sha': head(repo),
               'event': {'pull_request': {'base': {'sha': base}},
                         'repository': {'default_branch': 'main'}}}
    done = run_action(repo, {'push-key': 'dummy-key'}, context,
                      {'SIZE_RATCHET_PUSH_REMOTE': str(remote)})
    assert done[MEASURE].returncode == 0, done[MEASURE].stderr
    assert PUSH not in done
    assert git(remote, 'rev-parse', 'main').strip() == base


def test_the_key_is_a_0600_file_for_pinned_ssh_and_gone_after(tmp_path):
    # A stand-in ssh records what git hands it and refuses the connection.
    repo, sha, _ = loose_repo(tmp_path)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log = tmp_path / 'ssh.log'
    fake = bin_dir / 'ssh'
    fake.write_text(
        '#!/bin/sh\n'
        f'log={log}\n'
        'printf "%s\\n" "$@" "env-key=${SIZE_RATCHET_PUSH_KEY:-}" >> "$log"\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -i) stat -c "mode %a" "$2" >> "$log"; cat "$2" >> "$log";;\n'
        '    UserKnownHostsFile=*) cat "${1#UserKnownHostsFile=}" >> "$log";;\n'
        '  esac\n'
        '  shift\n'
        'done\n'
        'exit 255\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    done = run_action(
        repo, {'push-key': 'SECRET-KEY-TEXT'}, push_context(repo),
        {'SIZE_RATCHET_PUSH_REMOTE': 'git@github.com:example/consumer.git',
         'PATH': f'{bin_dir}{os.pathsep}{os.environ["PATH"]}'})
    assert done[PUSH].returncode == 2
    assert 'reading the branch afterwards failed too' in done[PUSH].stderr
    seen = log.read_text()
    assert 'mode 600\nSECRET-KEY-TEXT\n' in seen
    lines = seen.splitlines()
    for option in ('IdentitiesOnly=yes', 'StrictHostKeyChecking=yes',
                   'GlobalKnownHostsFile=/dev/null'):
        assert option in lines
    assert ['-F', '/dev/null'] in [lines[i:i + 2] for i in range(len(lines))]
    assert 'env-key=' in lines and 'SECRET-KEY-TEXT' not in seen.replace(
        'mode 600\nSECRET-KEY-TEXT\n', '')
    assert ('github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6'
            'UOoqKLsabgH5C9okWi0dh2l9GKJl\n') in seen
    for kind in ('ecdsa-sha2-nistp256 AAAAE2VjZHNh', 'ssh-rsa AAAAB3NzaC1yc2E'):
        assert f'github.com {kind}' in seen
    assert 'git@github.com' in seen
    # The key file and its directory went with the push step.
    temp = Path(repo.parent / f'{repo.name}-runner-temp')
    assert sorted(item.name for item in temp.iterdir()) == \
        sorted(['size-ratchet-records', 'output-1', 'output-2'])
    assert 'SECRET-KEY-TEXT' not in done[MEASURE].stdout + \
        done[PUSH].stdout + done[PUSH].stderr
