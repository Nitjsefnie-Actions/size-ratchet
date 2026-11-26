#!/usr/bin/env python3
"""Refuse releases outside the next semver successors or without successful
required push checks on a pinned main SHA. Read required contexts at runtime;
only the reviewed pull-request-only set below is skipped, and named when it is.

GitHub evidence is a snapshot: this cannot lock main, rulesets or other tag
writers. Publishing can leave an annotated object or tag if a later API write
fails; existing tags are never replaced. Runtime uses the standard library.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time


# Required contexts no push run produces; the ruleset requires them on the pull request.
PULL_REQUEST_ONLY = frozenset({'gate'})


class ReleaseError(Exception):
    """An attempted release could not establish its required evidence."""


def _read(argv):
    return subprocess.run(argv, check=True, capture_output=True,
                          text=True, timeout=60).stdout


def _get(read, endpoint, *, listing=False):
    argv = ['gh', 'api', endpoint, '-H', 'Cache-Control: no-cache']
    if listing:
        argv += ['--paginate', '--slurp']
    try:
        return json.loads(read(argv))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ReleaseError(f'cannot read {endpoint}: {error}') from error


def version_tuple(version):
    """Read a canonical, non-prerelease semantic version."""
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
        raise ReleaseError(f'refusing version {version!r}: expected X.Y.Z')
    return tuple(int(part) for part in version.split('.'))


def _tags(read, repository):
    pages = _get(read, f'repos/{repository}/tags', listing=True)
    return [tag['name'] for page in pages for tag in page]


def validate_version(read, repository, version):
    """Require exactly a patch, minor or major successor of the latest tag."""
    proposed = version_tuple(version)
    versions = [version_tuple(tag[1:]) for tag in _tags(read, repository)
                if re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', tag)]
    if not versions:
        raise ReleaseError(f'refusing release {version}: no vX.Y.Z tag exists')
    major, minor, patch = max(versions)
    successors = ((major, minor, patch + 1), (major, minor + 1, 0), (major + 1, 0, 0))
    if proposed not in successors:
        raise ReleaseError(f'refusing release {version}: expected a successor of '
                           f'v{major}.{minor}.{patch}')


def required_contexts(read, repository):
    """Read the active ruleset's required status contexts, refusing an empty set."""
    pages = _get(read, f'repos/{repository}/rulesets', listing=True)
    matching = [rule for page in pages for rule in page
                if rule['name'] == 'require-ci-aggregate']
    if len(matching) != 1 or matching[0]['enforcement'] != 'active':
        raise ReleaseError('cannot read one active require-ci-aggregate ruleset')
    ruleset = _get(read, f'repos/{repository}/rulesets/{matching[0]["id"]}')
    if ruleset['enforcement'] != 'active':
        raise ReleaseError('require-ci-aggregate is not active')
    contexts = {check['context'] for rule in ruleset['rules']
                if rule['type'] == 'required_status_checks'
                for check in rule['parameters']['required_status_checks']}
    if not contexts or any(not isinstance(name, str) or not name for name in contexts):
        raise ReleaseError('require-ci-aggregate has no usable required contexts')
    return contexts


def wait(read, repository, sha, required, *, timeout=2700, grace=600, interval=20):
    """Wait boundedly for each required check's own qualifying push evidence."""
    started = time.monotonic()
    while True:
        pages = _get(read, f'repos/{repository}/actions/runs?head_sha={sha}', listing=True)
        suites = {run['check_suite_id'] for page in pages for run in page['workflow_runs']
                  if run['head_sha'] == sha and run['event'] == 'push'
                  and run['actor']['login'] != 'dependabot[bot]'}
        pages = _get(read, f'repos/{repository}/commits/{sha}/check-runs?filter=all',
                     listing=True)
        checks = [check for page in pages for check in page['check_runs']
                  if check['name'] in required and check['head_sha'] == sha
                  and check['check_suite']['id'] in suites]
        seen = {check['name'] for check in checks}
        passed = set()
        for check in checks:
            if check['status'] == 'completed':
                if check['conclusion'] != 'success':
                    raise ReleaseError(f'refusing release on {sha}: '
                                       f'{check["name"]}: {check["conclusion"]}')
                passed.add(check['name'])
        elapsed = time.monotonic() - started
        if elapsed >= timeout:
            raise ReleaseError(f'release on {sha} reached the {timeout:g}s deadline; '
                               f'unsatisfied: {sorted(required - passed)}')
        if required <= passed:
            return
        missing = required - seen
        if missing and elapsed >= grace:
            raise ReleaseError(f'release on {sha}: no qualifying check within the '
                               f'{grace:g}s registration grace: {sorted(missing)}')
        print(f'waiting for release checks on {sha}: {sorted(required - passed)}', flush=True)
        remaining = min(timeout - elapsed, grace - elapsed if missing else timeout)
        time.sleep(min(interval, remaining))


def _commit_sha(sha):
    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
        raise ReleaseError(f'refusing release SHA {sha!r}: expected a 40-hex commit')
    return sha


def verify(read, repository, version):
    """Pin main first and require the checkout and its required checks to agree."""
    branch = _get(read, f'repos/{repository}/branches/main')
    sha = _commit_sha(branch['commit']['sha'])
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True,
                              capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseError(f'cannot resolve checked-out HEAD for release {version}: '
                           f'{error}') from error
    if head != sha:
        raise ReleaseError(f'refusing release {version}: main moved to {sha}, checkout is {head}; '
                           're-dispatch on main')
    validate_version(read, repository, version)
    required = required_contexts(read, repository)
    for name in sorted(required & PULL_REQUEST_ONLY):
        print(f'skipping pull-request-only required context: {name}', flush=True)
    if not required - PULL_REQUEST_ONLY:
        raise ReleaseError('require-ci-aggregate has no push-checked contexts')
    wait(read, repository, sha, required - PULL_REQUEST_ONLY)
    return sha


def _write(argv):
    return subprocess.run(argv, check=True, capture_output=True,
                          text=True, timeout=60).stdout


def _post(write, endpoint, fields, *, attempt):
    try:
        return json.loads(write(['gh', 'api', endpoint, '-X', 'POST', *fields]))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        detail = getattr(error, 'stderr', '') or str(error)
        raise ReleaseError(f'cannot create {attempt}: {detail}') from error


def publish(read, write, repository, version, sha):
    """Create an annotated tag, its ref, and a release without replacing tags."""
    version_tuple(version)
    _commit_sha(sha)
    name = f'v{version}'
    if name in _tags(read, repository):
        raise ReleaseError(f'refusing release {name}: tag already exists')
    tag = _post(write, f'repos/{repository}/git/tags', [
        '-f', f'tag={name}', '-f', f'message=Release {name}',
        '-f', f'object={sha}', '-f', 'type=commit'], attempt=f'annotated tag {name}')
    tag_sha = _commit_sha(tag['sha'])
    _post(write, f'repos/{repository}/git/refs', [
        '-f', f'ref=refs/tags/{name}', '-f', f'sha={tag_sha}'], attempt=f'tag ref {name}')
    _post(write, f'repos/{repository}/releases', [
        '-f', f'tag_name={name}', '-F', 'generate_release_notes=true'], attempt=f'release {name}')


def main(argv=None, *, read=_read, write=_write):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('verify', 'publish'):
        command = commands.add_parser(name)
        command.add_argument('repository')
        command.add_argument('version')
        if name == 'publish':
            command.add_argument('sha')
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify':
            sha = verify(read, args.repository, args.version)
            if output := os.environ.get('GITHUB_OUTPUT'):
                with open(output, 'a', encoding='utf-8') as handle:
                    handle.write(f'sha={sha}\n')
            print(f'verified release {args.version} on {sha}')
        else:
            publish(read, write, args.repository, args.version, args.sha)
    except (ReleaseError, OSError, ValueError, KeyError, TypeError) as error:
        print(f'release dispatch: cannot {args.command} {args.repository} '
              f'version {args.version!r}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
