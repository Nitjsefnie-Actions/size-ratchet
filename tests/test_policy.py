"""Tests for the input policy: types, ceilings, precedence, validation."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'ci'))
import config  # noqa: E402
import size_baseline  # noqa: E402

from tests.helpers import RECORDS_PATH, git, make_repo, run_gate  # noqa: E402


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


@pytest.mark.parametrize('folder', ['/abs/', '../up/', 'a/../b/', './tests/',
                                    'tests//', 'tests/./', './', '//',
                                    '.git/', 'vendor/.git/', 'a/.GIT/b/',
                                    'vendor/git~1/', 'a/.Git. /', 'GIT~1 /'])
def test_a_folder_no_repository_path_can_start_with_is_an_error(folder):
    with pytest.raises(ValueError, match='input overrides line 1 .*repository-relative'):
        config.parse_overrides(f'{folder}: 700\n')


@pytest.mark.parametrize('folder', ['.github/', '..x/', 'a./', '.../', 'a/.b/',
                                    '.gitx/', 'a/git/', 'git~2/', 'a/.git.x/'])
def test_a_dotted_folder_name_is_still_a_folder(folder):
    assert config.parse_overrides(f'{folder}: 700\n') == {folder: 700}
    assert config.parse_tree_budgets(folder, '') == [folder]


@pytest.mark.parametrize('entry,kind', [
    ('./tests/', 'folder'), ('tests//', 'folder'), ('/tests/', 'folder'),
    ('a/../b/', 'folder'), ('tests/./', 'folder'), ('/', 'folder'),
    ('./a.py', 'path'), ('src//a.py', 'path'), ('/a.py', 'path'),
    ('src/./a.py', 'path'), ('src/..', 'path'), ('.', 'path'), ('..', 'path'),
    ('vendor/.git/', 'folder'), ('.Git/', 'folder'), ('a/.GIT/x.py', 'path'),
    ('.git', 'path'), ('vendor/.git./', 'folder'), ('a/git~1/x.py', 'path'),
    ('a/.GIT ./b/', 'folder'), ('a/.git:x', 'path'),
])
def test_an_exclude_entry_no_repository_path_can_match_is_an_error(entry, kind):
    with pytest.raises(ValueError, match=(
            f"^input exclude line 2 \\('  {re.escape(entry)}'\\): {kind} "
            f"'{re.escape(entry)}' is not a repository-relative path"
            f"{' prefix' if kind == 'folder' else ''}$")):
        config.parse_exclude(f'src/ok.py\n  {entry}\n')


@pytest.mark.parametrize('entry', ['.github/', '..x/', 'a./', '.../', 'a/.b/',
                                   '.gitignore', 'src/.hidden.py', 'a/..b.py',
                                   '.gitx/', 'a/.gitx', 'a/.git.py',
                                   'git~1x/', 'a/.git x'])
def test_a_dotted_exclude_name_is_still_an_entry(entry):
    assert config.parse_exclude(f'{entry}\n') == [entry]


@pytest.mark.parametrize('entry', ['./tests/', 'tests//', 'tests/.git/',
                                   'tests/.git./'])
def test_tighten_refuses_an_inert_exclude_and_writes_no_records(tmp_path, entry):
    repo = make_repo(tmp_path, {'tests/a.py': 'x\n' * 600})
    done = run_gate(repo, 'tighten', exclude=entry)
    assert done.returncode == 2, done.stderr
    assert f"input exclude line 1 ('{entry}')" in done.stderr
    assert not (repo / RECORDS_PATH).exists()


# Segments git's protectNTFS check refuses as .git aliases, then near misses.
SEGMENTS = ['.git', '.GIT', '.git.', '.Git..', '.git ', '.gIt. .', 'git~1',
            'GIT~1', 'git~1.', 'Git~1 ', '.git:x', '.git. \\x', 'git~1:$DATA',
            '.gitx', '.git.x', '.git x', '.git.py', 'git', 'git~2', 'git~10',
            'git~1x', 'git~1.x', '.git~1', '..git', ' .git', '.git\t',
            '.gitx:y', 'x:y', 'a\\b', '.g\u0130t', '.g\u0131t', 'x\\.git',
            'x\\GIT~1', 'a\\b\\.git. ', 'x\\.gitx', 'x:.git', 'x.git',
            '\\.git', '\\git~1', '\\\\.git', '\\.gitx\\.git']


def _refuses(parse, entry):
    try:
        parse(entry)
    except ValueError:
        return True
    return False


def test_an_entry_is_refused_exactly_when_git_cannot_track_it(tmp_path):
    git(tmp_path, 'init', '-q')
    (tmp_path / 'empty').write_text('')
    blob = git(tmp_path, 'hash-object', '-w', 'empty').strip()

    def git_refuses(entry):
        path = entry.strip() + ('x' if entry.endswith('/') else '')
        return subprocess.run(
            ['git', '-C', str(tmp_path), '-c', 'core.protectNTFS=true',
             '-c', 'core.protectHFS=false', 'update-index', '--add',
             '--cacheinfo', f'100644,{blob},{path}'],
            capture_output=True, check=False).returncode != 0

    # Disjoint top folders, so no probe path is another's parent.
    entries = [form.format(segment) for segment in SEGMENTS
               for form in ('f/{}/', 'p/{}/x.py', 'l/{}')]
    by_git = {entry for entry in entries if git_refuses(entry)}
    assert {'f/.git./', 'f/.git /', 'f/git~1/', 'l/GIT~1', 'f/x\\.git/'} <= by_git
    assert not {'f/.gitx/', 'p/git~2/x.py', 'l/x:y', 'f/\\.git/'} & by_git
    assert {entry for entry in entries
            if _refuses(config.parse_exclude, entry)} == by_git
    folders = {entry for entry in entries if entry.endswith('/')}
    assert {folder for folder in folders if _refuses(
        lambda text: config.parse_tree_budgets(text, ''), folder)} == by_git & folders


@pytest.mark.parametrize('value', ['+7', '1_000', ' 7 x', '0x10', '７'])
def test_a_ceiling_must_be_plain_decimal_digits(value):
    with pytest.raises(ValueError, match='input ceilings line 1 .*not a positive integer'):
        config.parse_ceilings(f'source: {value}\n')

