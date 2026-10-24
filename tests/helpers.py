"""Shared fixtures: disposable git repositories and the subprocess driver."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ACTION_ROOT = Path(__file__).resolve().parents[1]


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


def make_repo(tmp_path, files, name='repo'):
    """A committed disposable repository holding ``files``."""
    repo = tmp_path / name
    repo.mkdir()
    git(repo, 'init', '-q')
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


def run_gate(repo, mode, base_ref=None, **inputs):
    """Run run.py as the composite action does: env data, python -I.

    The subprocess inherits the runner's environment: the CI coverage hook
    rides COVERAGE_PROCESS_START, and a clean-room env would measure none
    of the runner.
    """
    env = dict(os.environ)
    env['GITHUB_WORKSPACE'] = str(repo)
    env['SIZE_RATCHET_MODE'] = mode
    if base_ref is not None:
        env['SIZE_RATCHET_BASE_REF'] = base_ref
    for name, value in inputs.items():
        env['SIZE_RATCHET_' + name.upper()] = value
    return subprocess.run(
        [sys.executable, '-I', str(ACTION_ROOT / 'run.py')],
        env=env, capture_output=True, text=True, timeout=120)
