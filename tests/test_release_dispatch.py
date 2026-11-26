"""Release admission uses GitHub evidence, with no live reads or writes."""
import contextlib
from functools import partial
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REPO = 'owner/repo'
SHA = 'a' * 40


def _load():
    path = ROOT / 'scripts/ci/release_dispatch.py'
    assert path.is_file(), 'release dispatch gate is missing'
    spec = importlib.util.spec_from_file_location('release_dispatch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class API:
    """Replay only named GitHub endpoints; unexpected reads fail the test."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv):
        assert argv[:2] == ['gh', 'api'], argv
        assert 'Cache-Control: no-cache' in argv, argv
        endpoint = argv[2]
        assert endpoint in self.responses, argv
        self.calls.append(argv)
        value = self.responses[endpoint]
        return json.dumps(value() if callable(value) else value)


def _refuses(module, operation, message):
    try:
        operation()
    except module.ReleaseError as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError(f'expected refusal: {message}')


def test_version_requires_canonical_three_component_semver():
    module = _load()
    assert module.version_tuple('2.10.0') == (2, 10, 0)
    for version in ('v2.1.1', '2.1', '2.1.1-rc.1', '2.1.1+build', '',
                    ' 2.1.1', '2.1.1\n', '02.1.1', '2.01.1', '2.1.01',
                    '２.1.1', '-2.1.1', '2.1.1; echo bad'):
        _refuses(module, partial(module.version_tuple, version), 'version')


def test_numeric_latest_tag_allows_exactly_three_successors():
    module = _load()
    histories = (
        ([[{'name': 'v2.9.0'}, {'name': 'v99.0.0-rc.1'}],
          [{'name': 'v2.10.0'}, {'name': 'not-a-release'}]],
         ('2.10.1', '2.11.0', '3.0.0'),
         ('7.4.7', '7.5.0', '8.0.0', '2.10.0', '2.9.1', '2.10.2',
          '2.11.1', '3.0.1', '4.0.0')),
        ([[{'name': 'v7.4.5'}], [{'name': 'v7.4.6'}]],
         ('7.4.7', '7.5.0', '8.0.0'),
         ('2.10.1', '2.11.0', '3.0.0', '7.4.6', '7.4.8', '7.5.1', '8.0.1', '9.0.0')),
    )
    for pages, accepted, refused in histories:
        for version in refused:
            api = API({f'repos/{REPO}/tags': pages})
            _refuses(module, partial(module.validate_version, api, REPO, version), 'successor')
        for version in accepted:
            api = API({f'repos/{REPO}/tags': pages})
            module.validate_version(api, REPO, version)
            assert '--paginate' in api.calls[0] and '--slurp' in api.calls[0]


def test_version_refuses_missing_releases_and_rejects_shape_before_reads():
    module = _load()
    api = API({f'repos/{REPO}/tags': [[{'name': 'v1.0.0-preview'}], []]})
    _refuses(module, lambda: module.validate_version(api, REPO, '1.0.0'), 'no vX.Y.Z')
    api = API({})
    _refuses(module, lambda: module.validate_version(api, REPO, 'v1.0.0'), 'version')
    assert not api.calls


def _rules(contexts=('required-one', 'required-two')):
    return {'id': 7, 'name': 'require-ci-aggregate', 'enforcement': 'active',
            'rules': [{'type': 'required_status_checks', 'parameters': {
                'required_status_checks': [{'context': name} for name in contexts]}}]}


def test_required_contexts_come_from_the_active_named_ruleset_on_later_pages():
    module = _load()
    api = API({f'repos/{REPO}/rulesets': [[{'id': 4, 'name': 'unrelated'}], [_rules()]],
               f'repos/{REPO}/rulesets/7': _rules(('a-new-context',))})
    assert module.required_contexts(api, REPO) == {'a-new-context'}
    assert '--paginate' in api.calls[0] and '--slurp' in api.calls[0]


def test_missing_inactive_or_empty_required_ruleset_refuses():
    module = _load()
    for rows, detail in (([], _rules()), ([_rules()], _rules(())),
                         ([_rules()], {**_rules(), 'enforcement': 'disabled'}),
                         ([{**_rules(), 'enforcement': 'evaluate'}], _rules()),
                         ([_rules(), _rules()], _rules()),
                         ([_rules()], {**_rules(), 'rules': []})):
        api = API({f'repos/{REPO}/rulesets': [rows], f'repos/{REPO}/rulesets/7': detail})
        _refuses(module, partial(module.required_contexts, api, REPO), 'require-ci-aggregate')


def _run(suite=1, event='push', actor='human', sha=SHA, conclusion='failure'):
    return {'check_suite_id': suite, 'event': event, 'actor': {'login': actor},
            'head_sha': sha, 'status': 'completed', 'conclusion': conclusion}


def _check(name='gitleaks', suite=1, conclusion: str | None = 'success',
           status='completed', sha=SHA):
    return {'name': name, 'check_suite': {'id': suite}, 'conclusion': conclusion,
            'status': status, 'head_sha': sha}


def _gate_api(runs, checks):
    return API({f'repos/{REPO}/actions/runs?head_sha={SHA}': runs,
                f'repos/{REPO}/commits/{SHA}/check-runs?filter=all': checks})


class Clock:
    """Replace only the elapsed-time boundary, never the admission logic."""

    def __init__(self):
        self.elapsed = 0
        self.sleeps = []

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        assert seconds > 0
        self.sleeps.append(seconds)
        self.elapsed += seconds


def _wait(module, api, *, required=None, timeout=60, grace=40, clock=None):
    clock = clock or Clock()
    with patch.object(module.time, 'monotonic', clock.now), \
            patch.object(module.time, 'sleep', clock.sleep):
        module.wait(api, REPO, SHA, required or {'gitleaks'},
                    timeout=timeout, grace=grace, interval=20)
    return clock


def test_push_check_success_counts_even_when_its_workflow_reports_failure():
    module = _load()
    api = _gate_api([{'workflow_runs': [_run()]}], [{'check_runs': [_check()]}])
    assert _wait(module, api).sleeps == []
    for argv in api.calls:
        assert '--paginate' in argv and '--slurp' in argv


def test_other_events_dependabot_unmapped_and_wrong_sha_never_satisfy():
    module = _load()
    for run, check in ((None, _check()),
                       (_run(event='schedule'), _check()),
                       (_run(event='workflow_run'), _check()),
                       (_run(event='issue_comment'), _check()),
                       (_run(event='pull_request'), _check()),
                       (_run(event='workflow_dispatch'), _check()),
                       (_run(actor='dependabot[bot]'), _check()),
                       (_run(sha='b' * 40), _check()),
                       (_run(suite=2), _check()),
                       (_run(), _check(sha='b' * 40))):
        api = _gate_api([{'workflow_runs': [run] if run else []}],
                        [{'check_runs': [check]}])
        clock = Clock()
        _refuses(module, partial(_wait, module, api, clock=clock), 'registration grace')
        assert clock.elapsed == 40 and clock.sleeps == [20, 20]


def test_scheduled_non_success_cannot_poison_push_success_on_the_same_sha():
    module = _load()
    for conclusion in ('failure', 'cancelled', 'skipped', 'neutral', 'success'):
        api = _gate_api([{'workflow_runs': [_run(suite=2, event='schedule')]},
                         {'workflow_runs': [_run()]}],
                        [{'check_runs': [_check(suite=2, conclusion=conclusion)]},
                         {'check_runs': [_check()]}])
        assert _wait(module, api).sleeps == []


def test_each_completed_non_success_refuses_immediately_even_beside_success():
    module = _load()
    for conclusion in ('failure', 'cancelled', 'skipped', 'neutral', 'timed_out',
                       'action_required', 'stale', 'startup_failure', None):
        api = _gate_api([{'workflow_runs': [_run(conclusion='success')]}],
                        [{'check_runs': [_check()]},
                         {'check_runs': [_check(conclusion=conclusion)]}])
        clock = Clock()
        _refuses(module, partial(_wait, module, api, clock=clock), f'gitleaks: {conclusion}')
        assert clock.sleeps == []


def test_every_required_context_must_register_not_just_one():
    module = _load()
    api = _gate_api([{'workflow_runs': [_run()]}], [{'check_runs': [_check()]}])
    _refuses(module, lambda: _wait(module, api, required={'gitleaks', 'new-context'}),
             'new-context')


def test_unrelated_failed_check_does_not_block_required_success():
    module = _load()
    api = _gate_api([{'workflow_runs': [_run()]}], [{'check_runs': [
        _check(), _check(name='informational', conclusion='failure')]}])
    assert _wait(module, api).sleeps == []


def test_pending_check_waits_until_its_own_success():
    module = _load()
    clock = Clock()
    api = _gate_api([{'workflow_runs': [_run()]}], lambda: [{'check_runs': [
        _check(conclusion=None, status='in_progress') if clock.elapsed < 40 else _check()]}])
    assert _wait(module, api, clock=clock).sleeps == [20, 20]


def test_missing_check_can_register_during_the_grace():
    module = _load()
    clock = Clock()
    api = _gate_api([{'workflow_runs': [_run()]}], lambda: [{'check_runs': [
        _check()] if clock.elapsed else []}])
    assert _wait(module, api, clock=clock).sleeps == [20]


def test_pending_and_absent_checks_have_separate_bounded_deadlines():
    module = _load()
    for checks, message in (([], 'registration grace'),
                            ([_check(status='queued', conclusion=None)], 'deadline')):
        api = _gate_api([{'workflow_runs': [_run()]}], [{'check_runs': checks}])
        clock = Clock()
        _refuses(module, partial(_wait, module, api, timeout=65, grace=35, clock=clock), message)
        assert clock.elapsed == (35 if not checks else 65)
        assert clock.sleeps == ([20, 15] if not checks else [20, 20, 20, 5])


def test_api_errors_and_malformed_json_refuse_without_sleep():
    module = _load()

    def broken(_argv):
        raise OSError('network read refused')

    _refuses(module, lambda: _wait(module, broken), 'cannot read repos/owner/repo/actions/runs')
    _refuses(module, lambda: _wait(module, lambda _argv: 'not JSON'), 'cannot read')


def _verify_api(sha, contexts=('gitleaks',)):
    api = _gate_api([{'workflow_runs': [_run(sha=sha)]}],
                    [{'check_runs': [_check(sha=sha)]}])
    api.responses = {key.replace(SHA, sha): value for key, value in api.responses.items()}
    api.responses.update({
        f'repos/{REPO}/branches/main': {'commit': {'sha': sha}},
        f'repos/{REPO}/tags': [[{'name': 'v2.10.0'}]],
        f'repos/{REPO}/rulesets': [[_rules()]],
        f'repos/{REPO}/rulesets/7': _rules(contexts)})
    return api


def _head():
    return subprocess.run(['git', 'rev-parse', 'HEAD'], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_verify_pins_main_before_other_reads_and_returns_only_that_sha():
    module = _load()
    sha = _head()
    api = _verify_api(sha)
    assert module.verify(api, REPO, '2.10.1') == sha
    assert api.calls[0][2] == f'repos/{REPO}/branches/main'
    assert [argv[2] for argv in api.calls].count(f'repos/{REPO}/branches/main') == 1


def _clocked_verify(module, contexts):
    printed, clock = io.StringIO(), Clock()
    # Only the module's binding: a global patch would also drive subprocess's own wait loop.
    with patch.object(module, 'time', SimpleNamespace(monotonic=clock.now, sleep=clock.sleep)), \
            contextlib.redirect_stdout(printed):
        module.verify(_verify_api(_head(), contexts), REPO, '2.10.1')
    return printed.getvalue()


def test_verify_skips_only_the_pull_request_gate_and_names_it():
    module = _load()
    assert module.PULL_REQUEST_ONLY == {'gate'}
    printed = _clocked_verify(module, ('gitleaks', 'gate'))
    assert 'skipping pull-request-only required context: gate' in printed
    assert 'skipping' not in _clocked_verify(module, ('gitleaks',))


def test_verify_still_refuses_a_missing_push_context_beside_the_gate():
    module = _load()
    suites, near_miss = 'suites (ubuntu-latest, 3.14)', 'gate (extra)'
    _refuses(module, partial(_clocked_verify, module, ('gitleaks', 'gate', suites, near_miss)),
             f"registration grace: ['{near_miss}', '{suites}']")
    _refuses(module, partial(_clocked_verify, module, ('gate',)), 'no push-checked contexts')


class Writer:
    """Record GitHub write argv and return the annotated object's distinct SHA."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        return json.dumps({'sha': 'c' * 40})


def test_publish_creates_annotated_object_then_ref_then_generated_release():
    module = _load()
    api = API({f'repos/{REPO}/tags': [[{'name': 'v2.10.0'}]]})
    write = Writer()
    module.publish(api, write, REPO, '2.10.1', SHA)
    assert write.calls == [
        ['gh', 'api', f'repos/{REPO}/git/tags', '-X', 'POST',
         '-f', 'tag=v2.10.1', '-f', 'message=Release v2.10.1',
         '-f', f'object={SHA}', '-f', 'type=commit'],
        ['gh', 'api', f'repos/{REPO}/git/refs', '-X', 'POST',
         '-f', 'ref=refs/tags/v2.10.1', '-f', 'sha=' + 'c' * 40],
        ['gh', 'api', f'repos/{REPO}/releases', '-X', 'POST',
         '-f', 'tag_name=v2.10.1', '-F', 'generate_release_notes=true']]


def test_publish_refuses_bad_version_sha_and_existing_tag_before_any_write():
    module = _load()
    for version, sha, tags, message in (
            ('v2.10.1', SHA, [], 'version'),
            ('2.10.1', 'not-a-sha', [], 'SHA'),
            ('2.10.1', SHA, [{'name': 'v2.10.1'}], 'already exists')):
        api = API({f'repos/{REPO}/tags': [[], tags]})
        write = Writer()
        _refuses(module, partial(module.publish, api, write, REPO, version, sha), message)
        assert not write.calls
        if message != 'already exists':
            assert not api.calls


def test_publish_write_errors_name_the_attempt_and_stop_later_writes():
    module = _load()
    for fail_at, attempt in ((1, 'annotated tag v2.10.1'),
                             (2, 'tag ref v2.10.1'), (3, 'release v2.10.1')):
        write = Writer()

        def broken(argv, write=write, fail_at=fail_at):
            result = write(argv)
            if len(write.calls) == fail_at:
                raise subprocess.CalledProcessError(1, argv, stderr='HTTP 422 already exists')
            return result

        api = API({f'repos/{REPO}/tags': [[]]})
        _refuses(module, partial(module.publish, api, broken, REPO, '2.10.1', SHA), attempt)
        assert len(write.calls) == fail_at


def test_cli_writes_pinned_output_only_after_verify_success(tmp_path):
    module = _load()
    sha = _head()
    output = tmp_path / 'output'
    write = Writer()
    with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
        assert module.main(['verify', REPO, '2.10.1'], read=_verify_api(sha), write=write) == 0
    assert output.read_text(encoding='utf-8') == f'sha={sha}\n'
    assert not write.calls
    output.unlink()
    api = API({f'repos/{REPO}/branches/main': {'commit': {'sha': 'f' * 40}}})
    errors = io.StringIO()
    with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}), \
            contextlib.redirect_stderr(errors):
        assert module.main(['verify', REPO, '2.10.1'], read=api, write=write) == 1
    assert not output.exists() and not write.calls
    assert 'verify' in errors.getvalue() and '2.10.1' in errors.getvalue()
    assert 'main moved' in errors.getvalue()


def test_cli_publish_and_malformed_api_data_fail_loudly():
    module = _load()
    api = API({f'repos/{REPO}/tags': [[]]})
    write = Writer()
    assert module.main(['publish', REPO, '2.10.1', SHA], read=api, write=write) == 0
    assert len(write.calls) == 3
    for payload in ({}, {'commit': {'sha': None}}):
        errors = io.StringIO()
        api = API({f'repos/{REPO}/branches/main': payload})
        with contextlib.redirect_stderr(errors):
            assert module.main(['verify', REPO, '2.10.1'], read=api, write=write) == 1
        assert 'verify' in errors.getvalue() and '2.10.1' in errors.getvalue()


def test_command_seams_capture_output_and_propagate_a_process_failure():
    module = _load()
    for command in (module._read, module._write):
        assert command([sys.executable, '-c', 'print("response")']) == 'response\n'
        try:
            command([sys.executable, '-c', 'raise SystemExit(7)'])
        except subprocess.CalledProcessError as error:
            assert error.returncode == 7
        else:
            raise AssertionError('a failed gh subprocess must not become an empty response')
