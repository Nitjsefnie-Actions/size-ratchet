"""Tests for the input policy: types, ceilings, precedence, validation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'ci'))
import config  # noqa: E402
import size_baseline  # noqa: E402


def test_every_named_extension_has_its_type():
    expected = {
        'source': ('.py', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx',
                   '.sh', '.bash'),
        'stylesheet': ('.css', '.scss'),
        'sql': ('.sql',),
        'yaml': ('.yml', '.yaml'),
    }
    assert size_baseline.TYPES == expected
    for type_name, extensions in expected.items():
        for extension in extensions:
            assert size_baseline.type_of('x' + extension) == type_name


def test_an_unknown_extension_has_no_type():
    assert size_baseline.type_of('README.md') is None
    assert size_baseline.type_of('data.bin') is None
    assert size_baseline.type_of('noext') is None


def test_default_ceiling_is_500_for_every_type():
    policy = size_baseline.Policy()
    for extension in ('.py', '.css', '.sql', '.yml'):
        assert policy.ceiling('x' + extension) == 500


def test_ceilings_input_replaces_a_type_default():
    policy = size_baseline.Policy(ceilings_text='source: 800\n')
    assert policy.ceiling('a.py') == 800
    assert policy.ceiling('a.css') == 500


def test_override_beats_type_ceiling_beats_default():
    policy = size_baseline.Policy(ceilings_text='source: 800\n',
                                  overrides_text='src/: 900\n')
    assert policy.ceiling('src/a.py') == 900
    assert policy.ceiling('a.py') == 800
    assert policy.ceiling('a.css') == 500


def test_longest_matching_prefix_wins():
    policy = size_baseline.Policy(overrides_text='tests/: 700\ntests/sub/: 300\n')
    assert policy.ceiling('tests/a.py') == 700
    assert policy.ceiling('tests/sub/a.py') == 300


def test_an_override_never_gives_a_ceiling_to_an_untyped_file():
    policy = size_baseline.Policy(overrides_text='docs/: 700\n')
    assert policy.ceiling('docs/README.md') is None


@pytest.mark.parametrize('line', [
    'source: 0',
    'source: -3',
    'source: 1.5',
    'source: abc',
    'source:',
])
def test_a_non_positive_ceiling_is_an_error_naming_input_and_line(line):
    with pytest.raises(ValueError, match='input ceilings line 1'):
        config.parse_ceilings(line)


def test_a_malformed_ceilings_line_is_an_error():
    with pytest.raises(ValueError, match='line 2'):
        config.parse_ceilings('source: 500\nno separator here\n')


def test_an_unknown_type_is_an_error_naming_the_line():
    with pytest.raises(ValueError, match="unknown type 'rust'"):
        config.parse_ceilings('rust: 500\n')


def test_a_folder_without_trailing_slash_is_an_error():
    with pytest.raises(ValueError, match='has no trailing /'):
        config.parse_overrides('tests: 700\n')


def test_a_duplicate_folder_is_an_error():
    with pytest.raises(ValueError, match="duplicate folder 'tests/'"):
        config.parse_overrides('tests/: 700\ntests/: 300\n')


def test_a_wildcard_folder_is_an_error():
    with pytest.raises(ValueError, match='wildcard'):
        config.parse_overrides('tests*/: 700\n')


def test_an_override_ceiling_error_names_the_input():
    with pytest.raises(ValueError, match='input overrides line 1'):
        config.parse_overrides('tests/: nope\n')


def test_blank_lines_are_skipped_everywhere():
    policy = size_baseline.Policy(ceilings_text='\nsource: 800\n\n',
                                  overrides_text='\n',
                                  exclude_text='\n\nsrc/x.py\n')
    assert policy.ceilings == {'source': 800}
    assert policy.overrides == {}
    assert policy.excluded('src/x.py')
    assert not policy.excluded('src/y.py')


def test_excluded_matches_exact_paths_and_prefixes():
    policy = size_baseline.Policy(exclude_text='vendored/\nspecial.css\n')
    assert policy.excluded('vendored/a/b.py')
    assert policy.excluded('special.css')
    assert not policy.excluded('vendored.py')
    assert not policy.excluded('other.css')


def test_unclassified_must_be_ignore_or_fail():
    with pytest.raises(ValueError, match='input unclassified'):
        size_baseline.Policy(unclassified='strict')


def test_a_duplicate_type_is_an_error():
    with pytest.raises(ValueError, match="input ceilings line 2 .*duplicate type 'source'"):
        config.parse_ceilings('source: 800\nsource: 900\n')


@pytest.mark.parametrize('folder', ['/abs/', '../up/', 'a/../b/'])
def test_a_folder_outside_the_repository_is_an_error(folder):
    with pytest.raises(ValueError, match='input overrides line 1 .*repository-relative'):
        config.parse_overrides(f'{folder}: 700\n')


@pytest.mark.parametrize('value', ['+7', '1_000', ' 7 x', '0x10', '７'])
def test_a_ceiling_must_be_plain_decimal_digits(value):
    with pytest.raises(ValueError, match='input ceilings line 1 .*not a positive integer'):
        config.parse_ceilings(f'source: {value}\n')

