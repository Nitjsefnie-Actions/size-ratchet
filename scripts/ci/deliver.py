#!/usr/bin/env python3
"""Commit tightened records on the default branch: the measuring step
stages them, and the push step, the only one holding the deploy key,
commits that one file on github.sha and fast-forwards the branch."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import records

STAGED = 'size-ratchet-records'
MESSAGE = 'ci: tighten size-ratchet records'
BOT_NAME = 'github-actions[bot]'
BOT_EMAIL = '41898282+github-actions[bot]@users.noreply.github.com'
# GitHub's Meta endpoint ssh_keys, pinned: ssh-keyscan trusts any answer.
KNOWN_HOSTS = (
    'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n'
    'github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=\n'
    'github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=\n')


def _required(name):
    value = os.environ.get(name, '')
    if not value:
        raise ValueError(f'{name} is unset; the push needs it')
    return value


def _git(root, *args, env=None, data=None):
    return subprocess.run(['git', '-C', str(root), *args], input=data,
                          capture_output=True, env=env, timeout=300)


def _out(done, what):
    if done.returncode != 0:
        detail = done.stderr.decode(errors='replace').strip()
        raise OSError(f'{what} failed: {detail}')
    return done.stdout.decode().strip()


def check_head(root):
    """The run tightens github.sha's tree and nothing else."""
    sha = _required('GITHUB_SHA')
    actual = _out(_git(root, 'rev-parse', 'HEAD'), 'git rev-parse HEAD')
    if actual != sha:
        raise ValueError(f'the checkout HEAD {actual} is not github.sha '
                         f'{sha}; check out github.sha so the pushed '
                         'records describe the pushed tree')


def stage(root, records_path, maps):
    """Stage HEAD's records file with each {member: map} spliced for the
    push, or a created document when HEAD has none."""
    document = None
    if records.exists_at(root, 'HEAD', records_path):
        shown = _git(root, 'cat-file', 'blob', '--', f'HEAD:{records_path}')
        _out(shown, 'reading the records file')
        document = shown.stdout.decode('utf-8')
    for member, updated in maps.items():
        document = (records.document_text(member, updated) if document is None
                    else records.splice(document, member,
                                        records.serialize_map(updated)))
    path = Path(_required('RUNNER_TEMP')) / STAGED
    path.write_bytes(document.encode('utf-8'))
    with open(_required('GITHUB_OUTPUT'), 'a', encoding='utf-8') as out:
        out.write('push=true\n')


def push(root, records_path, default_branch):
    """Commit the staged records on github.sha and push it. 0 or 2."""
    sha = _required('GITHUB_SHA')
    key = _required('SIZE_RATCHET_PUSH_KEY')
    # From here the key lives only in its file, never in a child's env.
    del os.environ['SIZE_RATCHET_PUSH_KEY']
    if not default_branch:
        raise ValueError('the default branch is unknown; nothing to push to')
    # The suite's remote; on a runner the pinned github.com remote.
    remote = os.environ.get('SIZE_RATCHET_PUSH_REMOTE') or \
        f'git@github.com:{_required("GITHUB_REPOSITORY")}.git'
    staged = (Path(_required('RUNNER_TEMP')) / STAGED).read_bytes()
    with tempfile.TemporaryDirectory(dir=_required('RUNNER_TEMP')) as scratch:
        # Deleted with this directory, whatever the outcome.
        key_file = Path(scratch) / 'key'
        handle = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, 'w') as out:
            out.write(key if key.endswith('\n') else key + '\n')
        hosts = Path(scratch) / 'known_hosts'
        hosts.write_text(KNOWN_HOSTS)
        ssh = (f'ssh -F /dev/null -i {shlex.quote(str(key_file))} '
               '-o IdentitiesOnly=yes -o GlobalKnownHostsFile=/dev/null '
               f'-o UserKnownHostsFile={shlex.quote(str(hosts))} '
               '-o StrictHostKeyChecking=yes')
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(scratch) / 'index'),
                   GIT_AUTHOR_NAME=BOT_NAME, GIT_AUTHOR_EMAIL=BOT_EMAIL,
                   GIT_COMMITTER_NAME=BOT_NAME, GIT_COMMITTER_EMAIL=BOT_EMAIL)
        # No hook from the checkout's configuration runs beside the key.
        git = ('-c', f'core.sshCommand={ssh}', '-c', 'core.hooksPath=/dev/null')
        _out(_git(root, 'read-tree', sha, env=env), 'git read-tree')
        blob = _out(_git(root, 'hash-object', '-w', '--stdin', data=staged),
                    'git hash-object')
        _out(_git(root, 'update-index', '--add', '--cacheinfo',
                  f'100644,{blob},{records_path}', env=env), 'git update-index')
        tree = _out(_git(root, 'write-tree', env=env), 'git write-tree')
        commit = _out(_git(root, 'commit-tree', tree, '-p', sha, '-m', MESSAGE,
                           env=env), 'git commit-tree')
        target = f'refs/heads/{default_branch}'
        pushed = _git(root, *git, 'push', remote, f'{commit}:{target}')
        if pushed.returncode == 0:
            print(f'size-ratchet: pushed {commit} to {default_branch}')
            return 0
        refusal = pushed.stderr.decode(errors='replace').strip()
        # A fresh read of the remote tip; a stale FETCH_HEAD cannot answer.
        listed = _git(root, *git, 'ls-remote', '--exit-code', remote, target)
        if listed.returncode == 0 and listed.stdout.split()[:1] != [sha.encode()]:
            print(f'::notice::{default_branch} moved past {sha} before the '
                  'records push landed; the newer push run measures again')
            return 0
        if listed.returncode != 0:
            refusal += ('; reading the branch afterwards failed too: '
                        + listed.stderr.decode(errors='replace').strip())
        print(f'size-ratchet: the records push to {default_branch} was '
              f'refused, and the remote does not show it moved past {sha}: '
              f'{refusal}',
              file=sys.stderr)
        return 2
