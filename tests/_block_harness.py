"""The README install block's readers, expression evaluator and called-run driver."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests.helpers import run_gate

ACTION_ROOT = Path(__file__).resolve().parents[1]


def install_block_text():
    """The README's single yaml fence."""
    text = (ACTION_ROOT / 'README.md').read_text()
    fences = re.findall(r'```yaml\n(.*?)```', text, re.S)
    assert len(fences) == 1, 'the README carries exactly one yaml block'
    return fences[0]


def install_block():
    return yaml.safe_load(install_block_text())


def block_with_inputs():
    """The block's `with:` mapping, as the consumer copies it."""
    steps = install_block()['jobs']['size-baseline']['steps']
    return [step for step in steps if 'uses' in step
            and 'size-ratchet' in step['uses']][0]['with']


BLOCK_IF = ("github.event_name == 'pull_request' || "
            "(github.event_name == 'push' && github.ref_type == 'branch' && "
            'github.ref_name == github.event.repository.default_branch)')


def block_step_if():
    """The block's measurability condition, carried by both steps."""
    steps = install_block()['jobs']['size-baseline']['steps']
    ifs = [step.get('if') for step in steps]
    assert len(ifs) == 2 and ifs[0] == ifs[1] and ifs[0]
    return ifs[0]


def push_run_starts(ref):
    push = install_block()[True]['push'] or {}
    assert set(push) == {'branches'}, f'unmodelled push filter {push!r}'
    # shortcut: literal names only; refuse patterns until one ships.
    patterns = push['branches']
    assert not any(set(p) & set('*?+[]!') for p in patterns), patterns
    return ref in [f'refs/heads/{pattern}' for pattern in patterns]


def eval_expression(text, github):
    """Evaluate the subset of GitHub expressions the block uses; anything
    the grammar does not name is refused, never empty-matched."""
    tokens = re.findall(
        r'\(|\)|,|==|!=|&&|\|\||\'[^\']*\'|[A-Za-z_][\w.]*|\d+', text)
    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else None

    def take():
        nonlocal position
        token = tokens[position]
        position += 1
        return token

    def primary():
        token = take()
        if token == '(':
            value = disjunction()
            assert take() == ')'
            return value
        if token.startswith("'"):
            return token[1:-1]
        if token == 'format':
            assert take() == '('
            template = primary()
            arguments = []
            while peek() == ',':
                take()
                arguments.append(primary())
            assert take() == ')'
            for index, value in enumerate(arguments):
                template = template.replace('{%d}' % index, str(value))
            return template
        if token == 'github' or token.startswith('github.'):
            value = github
            for part in token.split('.')[1:]:
                value = (value or {}).get(part) if isinstance(value, dict) \
                    else None
            return value
        raise AssertionError(f'unrecognized token {token!r} in {text!r}')

    def equal(left, right):
        # GitHub ignores case when comparing strings.
        if isinstance(left, str) and isinstance(right, str):
            return left.lower() == right.lower()
        return left == right

    def comparison():
        value = primary()
        while peek() in ('==', '!='):
            operator = take()
            value = equal(value, primary()) if operator == '==' \
                else not equal(value, primary())
        return value

    def conjunction():
        # Parsed eagerly, then GitHub's && / || value semantics applied.
        value = comparison()
        while peek() == '&&':
            take()
            right = comparison()
            value = right if value else False
        return value

    def disjunction():
        value = conjunction()
        while peek() == '||':
            take()
            right = conjunction()
            value = value if value else right
        return value

    value = disjunction()
    assert position == len(tokens), f'unparsed tail in {text!r}'
    return value


def run_block_as_called(repo, event, ref, payload, workflow='ci-gate'):
    """Drive the block as a caller's run would; returns (steps that ran,
    the gate's result or None)."""
    block = install_block()
    assert 'workflow_call' in block[True], 'the block is no longer callable'
    github_context = {
        'workflow': workflow, 'event_name': event, 'ref': ref,
        'ref_type': {'heads': 'branch', 'tags': 'tag'}.get(
            ref[len('refs/'):].partition('/')[0], 'unknown'),
        'ref_name': ref[len('refs/'):].partition('/')[2],
        'event': payload, 'run_id': '3699691921',
    }
    ran, result = 0, None
    for step in block['jobs']['size-baseline']['steps']:
        if 'if' in step and not eval_expression(step['if'], github_context):
            continue
        ran += 1
        if 'size-ratchet' in step.get('uses', ''):
            result = run_gate(
                repo, block_with_inputs()['mode'],
                base_ref=payload.get('pull_request', {}).get('base', {})
                .get('sha'),
                default_branch=payload['repository']['default_branch'],
                extra_env={'GITHUB_EVENT_NAME': event, 'GITHUB_REF': ref,
                           'GITHUB_WORKFLOW': workflow})
    return ran, result
