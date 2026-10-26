"""Shared fixtures: disposable git repositories and the subprocess driver."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ACTION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ACTION_ROOT / 'scripts' / 'ci'))
import records  # noqa: E402

RECORDS_PATH = '.github/size-baseline.json'


def git(repo, *args):
    done = subprocess.run(['git', '-C', str(repo), *args],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def write_files(repo, files):
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


def make_repo(tmp_path, files, name='repo', branch='main'):
    """A committed disposable repository; the default branch is explicit."""
    repo = tmp_path / name
    repo.mkdir()
    git(repo, 'init', '-q', '-b', branch)
    git(repo, 'config', 'user.email', 'test@example.invalid')
    git(repo, 'config', 'user.name', 'size-ratchet tests')
    write_files(repo, files)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'seed the fixture tree')
    return repo


def add_and_commit(repo, rel, content, message):
    write_files(repo, {rel: content})
    git(repo, 'add', rel)
    git(repo, 'commit', '-qm', message)
    return git(repo, 'rev-parse', 'HEAD').strip()


def head(repo):
    return git(repo, 'rev-parse', 'HEAD').strip()


def put_records(repo, baseline, member='baseline'):
    """Commit a records seed and return its HEAD."""
    add_and_commit(repo, RECORDS_PATH, records.document_text(member, baseline),
                   'carry the records seed')
    return head(repo)


def run_gate(repo, mode, base_ref=None, extra_env=None, **inputs):
    """Run run.py as the composite action does: env data, python -I. The
    subprocess inherits this environment, coverage hook included."""
    env = dict(os.environ)
    env['GITHUB_WORKSPACE'] = str(repo)
    env['SIZE_RATCHET_MODE'] = mode
    if base_ref is not None:
        env['SIZE_RATCHET_BASE_REF'] = base_ref
    for name, value in inputs.items():
        env['SIZE_RATCHET_' + name.upper()] = value
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, '-I', str(ACTION_ROOT / 'run.py')],
        env=env, capture_output=True, text=True, timeout=120)
