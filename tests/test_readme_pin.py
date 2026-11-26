"""The README pin gate: which release counts as current, and which does not."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEWEST_SHA = '1' * 40
PREVIOUS_SHA = '2' * 40
OLDER_SHA = '3' * 40
TIP = '4' * 40
RELEASES = ('v4.0.4', 'v4.0.3', 'v4.0.2')
SHAS = {'v4.0.4': NEWEST_SHA, 'v4.0.3': PREVIOUS_SHA, 'v4.0.2': OLDER_SHA}


def _load():
    path = ROOT / 'tests/gate_readme_pin.py'
    assert path.is_file(), 'README pin gate is missing'
    spec = importlib.util.spec_from_file_location('gate_readme_pin', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _readme(sha=NEWEST_SHA, tag='v4.0.4', indent='        '):
    return (f'```yaml\n{indent}uses: Nitjsefnie-Actions/size-ratchet@{sha} # {tag}\n```\n')


def _verdict(module, text, *, tip=TIP, releases=RELEASES):
    resolved = []

    def resolve(tag):
        resolved.append(tag)
        return SHAS[tag]

    return module.verdict(text, list(releases), tip, resolve)


def test_the_newest_release_pinned_with_its_tag_passes():
    module = _load()
    assert _verdict(module, _readme()) == (True, 'README.md pins v4.0.4')


def test_a_release_behind_fails_and_names_the_sha():
    module = _load()
    ok, message = _verdict(module, _readme(PREVIOUS_SHA, 'v4.0.3'))
    assert not ok and PREVIOUS_SHA in message and 'v4.0.4' in message
    ok, message = _verdict(module, _readme(OLDER_SHA, 'v4.0.2'))
    assert not ok and 'v4.0.4' in message


def test_the_release_before_is_current_only_on_the_newest_release_s_own_tree():
    module = _load()
    # The newest release tags the tip, so its commit carries the README a
    # release behind: accepted there and nowhere else.
    assert _verdict(module, _readme(PREVIOUS_SHA, 'v4.0.3'), tip=NEWEST_SHA)[0]
    ok, message = _verdict(module, _readme(PREVIOUS_SHA, 'v4.0.3'))
    assert not ok and 'v4.0.4' in message
    # A tree that already carries the newest pin stays current at the tip.
    assert _verdict(module, _readme(), tip=NEWEST_SHA)[0]


def test_the_comment_must_name_the_release_the_sha_belongs_to():
    module = _load()
    ok, message = _verdict(module, _readme(NEWEST_SHA, 'v4.0.3'))
    assert not ok and 'comment' in message
    ok, message = _verdict(module, _readme(NEWEST_SHA, ''))
    assert not ok and 'comment' in message


def test_a_second_pin_line_refuses_before_any_lookup():
    module = _load()
    text = _readme() + _readme(PREVIOUS_SHA, 'v4.0.3')
    resolved = []
    ok, message = module.verdict(text, list(RELEASES), TIP,
                                 lambda tag: resolved.append(tag) or SHAS[tag])
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
    # No comment at all is a refusal from the verdict, not a parse error.
    assert module.parse(f'uses: Nitjsefnie-Actions/size-ratchet@{NEWEST_SHA}\n') \
        == ((NEWEST_SHA, ''), None)
