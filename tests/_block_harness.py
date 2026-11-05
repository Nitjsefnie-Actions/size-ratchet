"""The README install block's readers, expression evaluator and called-run driver."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

from tests.helpers import head

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


def eval_expression(text, github, contexts=None):
    """Evaluate the GitHub expressions the block and action use, over
    ``github`` and named ``contexts``; anything else is refused."""
    scopes = {'github': github, **(contexts or {})}
    tokens = re.findall(
        r'\(|\)|,|==|!=|&&|\|\||\'[^\']*\'|[A-Za-z_][\w.-]*|\d+', text)
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
        if token.split('.')[0] in scopes:
            value = scopes[token.split('.')[0]]
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


def render(value, github, contexts):
    """A step's `env:` or `with:` value as the runner renders it."""
    if not isinstance(value, str) or '${{' not in value:
        return value
    whole = re.fullmatch(r'\$\{\{ (.*) \}\}', value)
    assert whole, f'unmodelled template {value!r}'
    result = eval_expression(whole[1], github, contexts)
    if isinstance(result, bool):
        return 'true' if result else 'false'
    return '' if result is None else str(result)


MEASURE = 'Measure and apply the ratchet'
PUSH = 'Push the tightened records'


def run_action(repo, inputs, github, extra_env=None):
    """Run action.yml's run: steps as the runner does, this interpreter
    standing in for setup-python. Returns {step name: CompletedProcess}."""
    spec = yaml.safe_load((ACTION_ROOT / 'action.yml').read_text())
    values = {name: render(item['default'], github, {})
              for name, item in spec['inputs'].items()}
    values.update(inputs)
    temp = repo.parent / f'{repo.name}-runner-temp'
    temp.mkdir(exist_ok=True)
    outputs, results = {}, {}
    for index, step in enumerate(spec['runs']['steps']):
        if 'uses' in step:
            continue
        contexts = {'inputs': values, 'steps': outputs}
        if 'if' in step and not eval_expression(step['if'], github, contexts):
            continue
        output = temp / f'output-{index}'
        output.write_text('')
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('SIZE_RATCHET_')}
        env.update({
            'GITHUB_WORKSPACE': str(repo), 'GITHUB_ACTION_PATH': str(ACTION_ROOT),
            'RUNNER_TEMP': str(temp), 'GITHUB_OUTPUT': str(output),
            'GITHUB_EVENT_NAME': github['event_name'],
            'GITHUB_REF': github['ref'], 'GITHUB_SHA': github['sha'],
            'GITHUB_REPOSITORY': 'example/consumer',
            'PATH': f'{Path(sys.executable).parent}{os.pathsep}{os.environ["PATH"]}'})
        env.update(extra_env or {})
        env.update({name: render(value, github, contexts)
                    for name, value in step['env'].items()})
        done = subprocess.run(
            ['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c',
             step['run']], cwd=repo, env=env, capture_output=True, text=True,
            timeout=120)
        results[step['name']] = done
        outputs[step.get('id', '')] = {'outputs': dict(
            line.split('=', 1) for line in output.read_text().splitlines())}
        if done.returncode != 0:
            break
    return results


def block_environment(github_context):
    """The environment the block's job binds for this run, or ''."""
    return render(install_block()['jobs']['size-baseline']['environment'],
                  github_context, {})


def run_block_as_called(repo, event, ref, payload, workflow='ci-gate',
                        environment_secrets=None, extra_env=None,
                        inherit=True, inputs=None):
    """Drive the block as a caller's run would (``inputs`` edit `with:`);
    returns (block steps that ran, run_action's result or None). A secret resolves only from the
    environment the job binds, and in a called job only when the caller
    passes it (GitHub's reusable-workflow docs)."""
    block = install_block()
    assert 'workflow_call' in block[True], 'the block is no longer callable'
    github_context = {
        'workflow': workflow, 'event_name': event, 'ref': ref,
        'ref_type': {'heads': 'branch', 'tags': 'tag'}.get(
            ref[len('refs/'):].partition('/')[0], 'unknown'),
        'ref_name': ref[len('refs/'):].partition('/')[2],
        'event': payload, 'run_id': '3699691921', 'sha': head(repo),
    }
    secrets = (environment_secrets or {}).get(
        block_environment(github_context), {})
    if workflow != 'size baseline' and not inherit:
        secrets = {}
    ran, result = 0, None
    for step in block['jobs']['size-baseline']['steps']:
        if 'if' in step and not eval_expression(step['if'], github_context):
            continue
        ran += 1
        if 'size-ratchet' in step.get('uses', ''):
            rendered = {name: render(value, github_context,
                                     {'secrets': secrets})
                        for name, value in step['with'].items()}
            result = run_action(repo, {**rendered, **(inputs or {})},
                                github_context, extra_env)
    return ran, result
