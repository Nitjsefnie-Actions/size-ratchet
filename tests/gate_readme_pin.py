#!/usr/bin/env python3
"""Fail when README.md's install pin is behind the newest release.

A release tags the default branch's tip after the commit carrying the
README has merged, so on that one tree the release before it is current.
Exit 0 when the pin is current, 1 when it is behind or malformed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ACTION = 'Nitjsefnie-Actions/size-ratchet'
PIN = f'uses: {ACTION}@'


def parse(text):
    lines = [line.strip() for line in text.splitlines() if PIN in line]
    if len(lines) != 1:
        return None, f'expected exactly one "{PIN}" line in README.md, found {len(lines)}'
    sha, _, rest = lines[0].partition('@')[2].partition(' ')
    return (sha, rest.strip()), None


def verdict(text, releases, tip, resolve):
    """(ok, message) for a pinned SHA; releases are tags, newest first."""
    pinned, problem = parse(text)
    if problem:
        return False, problem
    sha, comment = pinned
    if not releases:
        return False, 'no published release to compare the README pin with'
    newest = releases[0]
    newest_sha = resolve(newest)
    accepted = None
    if sha == newest_sha:
        accepted = newest
    elif len(releases) > 1 and newest_sha == tip and sha == resolve(releases[1]):
        accepted = releases[1]
    if accepted is None:
        return False, f'README.md pins {sha}, but the newest release is {newest} ({newest_sha})'
    if comment != f'# {accepted}':
        return False, f'README.md pins {accepted} but its comment reads {comment!r}'
    return True, f'README.md pins {accepted}'


def _gh(endpoint):
    done = subprocess.run(['gh', 'api', endpoint], capture_output=True, text=True,
                          timeout=60)
    if done.returncode != 0:
        raise SystemExit(f'cannot read {endpoint}: {done.stderr.strip()}')
    return json.loads(done.stdout)


def main():
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    if not repository:
        print('GITHUB_REPOSITORY is unset', file=sys.stderr)
        return 1
    releases = [release['tag_name']
                for release in _gh(f'repos/{repository}/releases?per_page=10')
                if not release['draft'] and not release['prerelease']][:2]
    tip = _gh(f'repos/{repository}/commits/'
              f'{_gh(f"repos/{repository}")["default_branch"]}')['sha']
    shas = {}

    def resolve(tag):
        if tag not in shas:
            shas[tag] = _gh(f'repos/{repository}/commits/{tag}')['sha']
        return shas[tag]

    ok, message = verdict(Path('README.md').read_text(encoding='utf-8'),
                          releases, tip, resolve)
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
