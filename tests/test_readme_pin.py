"""The README pin gate: which releases count as current, and what main does."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = 'owner/repo'
NEWEST_SHA, PREVIOUS_SHA, OLDER_SHA, DRAFT_SHA = '1' * 40, '2' * 40, '3' * 40, '4' * 40
SHAS = {'v4.0.5': DRAFT_SHA, 'v4.0.4': NEWEST_SHA, 'v4.0.3': PREVIOUS_SHA,
        'v4.0.2': OLDER_SHA}
RELEASES = ('v4.0.4', 'v4.0.3')


def _load():
    path = ROOT / 'tests/gate_readme_pin.py'
    assert path.is_file(), 'README pin gate is missing'
    spec = importlib.util.spec_from_file_location('gate_readme_pin', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _readme(sha=NEWEST_SHA, tag='v4.0.4', indent='        '):
    return f'```yaml\n{indent}uses: Nitjsefnie-Actions/size-ratchet@{sha} # {tag}\n```\n'


def _verdict(module, text, releases=RELEASES, resolve=None):
    resolve = resolve or (lambda tag: SHAS[tag])
    return module.verdict(text, list(releases), resolve)


def test_the_newest_release_pinned_with_its_tag_passes():
    module = _load()
    assert _verdict(module, _readme()) == (True, 'README.md pins v4.0.4')


def test_one_release_of_lag_is_accepted_and_two_are_not():
    module = _load()
    assert _verdict(module, _readme(PREVIOUS_SHA, 'v4.0.3')) == \
        (True, 'README.md pins v4.0.3')
    for sha, tag in ((OLDER_SHA, 'v4.0.2'), ('9' * 40, 'v4.0.4')):
        ok, message = _verdict(module, _readme(sha, tag))
        assert not ok and sha in message and 'v4.0.4' in message


def test_the_comment_must_name_the_release_the_sha_belongs_to():
    module = _load()
    for tag in ('v4.0.3', '', 'v4.0.4 (latest)'):
        ok, message = _verdict(module, _readme(NEWEST_SHA, tag))
        assert not ok and 'comment' in message


def test_a_second_pin_line_refuses_before_any_lookup():
    module = _load()
    resolved = []
    ok, message = module.verdict(_readme() + _readme(PREVIOUS_SHA, 'v4.0.3'),
                                 list(RELEASES), lambda tag: resolved.append(tag) or SHAS[tag])
    assert not ok and 'exactly one' in message
    assert resolved == []


def test_no_published_release_fails_closed():
    module = _load()
    ok, message = _verdict(module, _readme(), releases=())
    assert not ok and 'no published release' in message


def test_the_pin_and_its_comment_are_read_off_the_line():
    module = _load()
    assert module.parse(_readme()) == ((NEWEST_SHA, '# v4.0.4'), None)
    assert module.parse(_readme(indent='')) == ((NEWEST_SHA, '# v4.0.4'), None)
    assert module.parse('nothing here\n')[1].startswith('expected exactly one')
    assert module.parse(f'uses: Nitjsefnie-Actions/size-ratchet@{NEWEST_SHA}\n') \
        == ((NEWEST_SHA, ''), None)


def _published(*tags):
    return [{'tag_name': tag, 'draft': False, 'prerelease': False} for tag in tags]


def _drive(tmp_path, monkeypatch, module, text, releases, repository=REPO):
    (tmp_path / 'README.md').write_text(text, encoding='utf-8')
    if repository is None:
        monkeypatch.delenv('GITHUB_REPOSITORY', raising=False)
    else:
        monkeypatch.setenv('GITHUB_REPOSITORY', repository)
    calls = []

    def fake(endpoint):
        calls.append(endpoint)
        if '/releases?' in endpoint:
            return releases
        return {'sha': SHAS[endpoint.rsplit('/', 1)[1]]}

    monkeypatch.setattr(module, '_gh', fake)
    monkeypatch.chdir(tmp_path)
    return module.main(), calls


def test_main_passes_a_current_pin_and_fails_a_stale_one(tmp_path, monkeypatch, capsys):
    module = _load()
    code, _ = _drive(tmp_path, monkeypatch, module, _readme(), _published('v4.0.4'))
    assert code == 0 and 'pins v4.0.4' in capsys.readouterr().out
    code, _ = _drive(tmp_path, monkeypatch, module, _readme(OLDER_SHA, 'v4.0.2'),
                     _published('v4.0.4', 'v4.0.3', 'v4.0.2'))
    assert code == 1 and 'v4.0.4' in capsys.readouterr().err


def test_main_reads_only_the_two_newest_releases(tmp_path, monkeypatch):
    module = _load()
    # The previous release is current; a third entry is never considered.
    code, _ = _drive(tmp_path, monkeypatch, module, _readme(PREVIOUS_SHA, 'v4.0.3'),
                     _published('v4.0.4', 'v4.0.3', 'v4.0.2'))
    assert code == 0
    code, _ = _drive(tmp_path, monkeypatch, module, _readme(OLDER_SHA, 'v4.0.2'),
                     _published('v4.0.4', 'v4.0.3', 'v4.0.2'))
    assert code == 1


def test_main_does_not_count_a_draft_or_prerelease_as_newest(tmp_path, monkeypatch):
    module = _load()
    releases = [{'tag_name': 'v4.0.5', 'draft': True, 'prerelease': False},
                {'tag_name': 'v4.0.4', 'draft': False, 'prerelease': True},
                *_published('v4.0.3')]
    code, _ = _drive(tmp_path, monkeypatch, module, _readme(DRAFT_SHA, 'v4.0.5'), releases)
    assert code == 1


def test_main_refuses_without_a_repository_or_a_read(tmp_path, monkeypatch, capsys):
    module = _load()
    code, calls = _drive(tmp_path, monkeypatch, module, _readme(), _published('v4.0.4'),
                         repository=None)
    assert code == 1 and calls == []
    assert 'GITHUB_REPOSITORY is unset' in capsys.readouterr().err
