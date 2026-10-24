#!/usr/bin/env python3
"""Dispatch the composite action from environment inputs.

Inputs reach this script as data through the environment and are never
interpolated into a shell program. Each input has a SIZE_RATCHET_-prefixed
name; the bare name is read only when the prefixed one is unset, so a local
run can drive the gate without duplicating the action's env block.

Exit statuses: 0 pass, 1 violation, 2 configuration or environment failure.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ACTION_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ACTION_ROOT / 'scripts' / 'ci'))
import size_baseline as policy


def env(name, default):
    value = os.environ.get('SIZE_RATCHET_' + name)
    return os.environ.get(name, default) if value is None else value


def resolve_mode(requested):
    """auto checks a pull request and tightens a push to the default branch.

    Every other event, including a push to another branch, fails closed and
    names the event.
    """
    if requested in ('check', 'tighten'):
        return requested
    if requested != 'auto':
        raise ValueError(f'input mode must be auto, check or tighten, '
                         f'got {requested!r}')
    event = os.environ.get('GITHUB_EVENT_NAME', '')
    default = env('DEFAULT_BRANCH', '')
    if event == 'pull_request':
        return 'check'
    if (event == 'push' and default
            and os.environ.get('GITHUB_REF') == f'refs/heads/{default}'):
        return 'tighten'
    raise ValueError(f'input mode=auto cannot handle event {event!r} '
                     f'on ref {os.environ.get("GITHUB_REF", "")!r}')


def require_tight_input(text):
    """The boolean input accepts exactly 'true' or 'false'."""
    if text not in ('true', 'false'):
        raise ValueError(f'input require-tight must be true or false, '
                         f'got {text!r}')
    return text == 'true'


def build_policy():
    return policy.Policy(
        ceilings_text=env('CEILINGS', ''),
        overrides_text=env('OVERRIDES', ''),
        exclude_text=env('EXCLUDE', ''),
        unclassified=env('UNCLASSIFIED', 'ignore'),
    )


def main() -> int:
    try:
        root = Path(os.environ.get('GITHUB_WORKSPACE', os.getcwd())).resolve()
        requested = env('MODE', 'auto')
        policy_out = build_policy()
        if sys.argv[1:] == ['--validate-only']:
            # The validate-only pass judges the inputs alone: the event
            # context is the caller's, and a manual run has none.
            return 0
        mode = resolve_mode(requested)
        base = env('BASE_REF', '') if mode == 'check' else ''
        return policy.run(
            root,
            env('RECORDS', '.github/size-baseline.json'),
            env('MEMBER', ''),
            mode,
            base,
            policy_out,
            env('STALE', 'warn'),
            require_tight_input(env('REQUIRE_TIGHT', 'false')),
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'size-ratchet failed: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
