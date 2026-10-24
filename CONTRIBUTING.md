# Contributing to size-ratchet

Issues and pull requests are welcome — especially a small source fixture
whose measured line count, ceiling resolution or ratchet verdict differs
from what `README.md` says. Include the file, its expected count, and the
actual command output.

## LLM and agent contributions are welcome

You may use an LLM or a coding agent. There is no penalty, no separate
review queue, and no expectation that you rewrite its output by hand. Much
of this repo was built that way.

Two conditions, and they are about honesty rather than provenance:

1. **Disclose the model** with a trailer on each commit it authored:

   ```
   Co-Authored-By: <Model Name> <noreply@example.com>
   ```

   e.g. `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. The plain
   model name — a context-window suffix like `(1M context)` is not part of
   it. One primary-author trailer per commit. Before pushing, inspect every
   outgoing commit rather than checking only the tip:

   ```bash
   git log --format='%h %(trailers:key=Co-Authored-By,valueonly)' origin/main..HEAD
   ```

2. **Do not submit claims you have not verified.** Paste the command and
   its real output. "Tests pass" without the run is not evidence. A suite
   piped through another command can hide its own failure.

## A new file is invisible until you name it

`.gitignore` denies by default: it starts with `*` and names back exactly
what the repository ships. A file you create is **untracked and unstaged
and will not appear in `git status`** until it is named there. Git never
looks inside a directory it has already ignored, so re-open the directory
before naming its contents:

```
*
!scripts/
scripts/*
!scripts/ci/
scripts/ci/*
!scripts/ci/*.py
```

Prove a change to it rather than reading it: seed a junk file in the
directory you changed, check `git check-ignore -v` refuses that file, and
check `git status --porcelain` is clean with every shipped file tracked.

## Getting it running

Python 3.13, Git, bash and ShellCheck. The action's runner imports the
standard library alone, and the suite installs its pinned toolchain into a
venv from the hash-pinned manifest:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --require-hashes --only-binary=:all: -r .github/workflows/requirements-test.txt
tests/run.sh          # the whole suite; its exit status is the verdict
shellcheck tests/run.sh
```

The suite drives `run.py` as the composite action does — environment data,
`python3 -I` — against real disposable git repositories, so a case asserts
the exit statuses and output a consumer's workflow would see. Temporary
repositories configure their own test identity; they never change this
checkout's commit identity. Run suites bare and read their actual exit
status.

You cannot exercise the action end to end without a repository to install
it in. Point a scratch repository's workflow at your branch —
`uses: <your-fork>/size-ratchet@<your-branch>` — and open a pull request
there.

## House style

- **Comments explain why, not what.** If you change a line whose comment
  records a supply-chain, permission, trigger or exit-status decision,
  change the comment with it.
- **Inputs are environment data, never program text.** A `${{ inputs.* }}`
  expression belongs in the action's `env:` block only; a patch that puts
  one inside a `run:` block will be refused whatever else it does.
- **Ship new files below their ceilings.** A file at 500 shares its
  headroom with every later branch; relocate code into a new file instead.
- **Tests pin behaviour, not implementation.** A test that would still
  pass with the bug reintroduced is not worth adding. Break the line you
  are protecting, watch the case fail, restore it — and say in the pull
  request which case failed and what it printed.
- **No test asserts on a dependency pin** — not on a workflow `uses:` pin,
  its version comment, or another workflow's `with:` inputs. A consistency
  check between steps, or a security invariant enumerated over all
  workflows, is fine.

## Tighten this repository's records

Both records files tighten on every default-branch push. After the
aggregate gate is green on main, the tests workflow's `ratchet` job runs the
local action in tighten mode. Only a changed `.github/size-baseline.json`
stages a `ratchet-push` artifact, together with the measured run's head SHA.
The comment share workflow does the same for `.github/comment-share.json`:
after its gate passes on a main push, a changed file stages a
`comment-share-ratchet` artifact with its own measured head SHA.

The separate top-level [ratchet-push workflow](.github/workflows/ratchet-push.yml)
lands the result. It admits only successful main push runs of either
workflow, downloads each artifact that run staged, and creates one
records-only commit with the environment deploy key. It checks out no code,
installs nothing, and runs no fetched helpers. Configure
`MASTER_PUSH_DEPLOY_KEY` in the `main-push` environment; a missing key fails
loudly, and so does an artifact without its records file or measured base.
The bot commit reruns both gates once, and unchanged records stage no
artifact, so the chain stops there.

When main has moved, the push job drops the run outright when main carries
any non-records change made after the artifact's measured base: a clean
merge could otherwise land a record below the file's real count, and
tighten never raises one. Records-only edits survive into a three-way
merge of each records file against that base. A conflict, an unreadable
ancestor or invalid merged JSON drops the run with a summary; a push
rejected while main stood still is an error. A rejection after main moved
drops the stale run so the next gate can measure again.

The records move down only. A pull request that shrinks a recorded file
lowers its record in the same PR (`require-tight: true` enforces this on
request); growth is answered by relocating code into a new file or
shrinking the file, never by raising the record.

## Commit messages

Conventional Commits, and the scope is **required** rather than optional:

```
<type>(<scope>): <summary>

<body — why the previous behaviour was wrong>

Co-Authored-By: <Model Name> <noreply@example.com>
```

The summary is imperative, lower case, no trailing full stop, and fits in
72 characters. One logical change per commit.

**Types**

| Type | For |
|---|---|
| `feat` | new behaviour a caller can observe |
| `fix` | a defect in behaviour a caller can observe |
| `docs` | documentation only |
| `test` | the suite and its fixtures only |
| `ci` | workflows, Dependabot, repository automation |
| `refactor` | no observable change in behaviour |
| `perf` | faster, with the measurement in the body |
| `chore` | housekeeping that fits nothing above |
| `revert` | undoing an earlier commit, which it names |

**Scopes** — the SUBJECT of the change, not the directory it landed in:

| Scope | Subject |
|---|---|
| `action` | the action's interface — the inputs and the step in `action.yml` |
| `ratchet` | the measurement, records and policy, in `run.py` and `scripts/ci/` |
| `harness` | the test machinery — the fixtures and the disposable-repo driver |
| workflow name | the workflow with that name, with type `ci` |
| `deps` | a dependency bump; what Dependabot is configured to emit |
| `readme` | the installation and usage guide in `README.md` |
| `contributing` | the contribution guide in `CONTRIBUTING.md` |
| `security` | the vulnerability reporting policy in `SECURITY.md` |
| `coc` | the community conduct policy in `CODE_OF_CONDUCT.md` |
| `templates` | the issue and pull-request forms |
| `repo` | repository furniture — `LICENSE`, `.gitignore`, repository settings |

For a workflow change, use type `ci` and that workflow's **name** as the
scope (the top-level `name:` in its YAML). The type disambiguates shared
names: `ci(tests)` changes the workflow, while `test(harness)` changes the
test machinery. Dependency bumps are the exception: use `ci(deps)`,
including bumps inside workflows, to match Dependabot.

Breaking changes carry both the `!` marker and a `BREAKING CHANGE:` footer
explaining the caller impact. This action is consumed by commit SHA, so a
caller upgrades deliberately and reads what changed on the way. Releases
follow semantic versioning: a breaking change bumps the major version (and
**a release that adds a measured type is breaking** — see README's
widening limit), a `feat` bumps the minor version, and any other release
bumps the patch version.

## Issues

Use the [issue form](.github/ISSUE_TEMPLATE/issue.md). Its section order
is fixed and its **Description is observed behaviour only** — what is
actually wrong, not the mechanism and not the fix. A proved mechanism
still goes under Suggested Fix, marked unverified.

Not reliably reproducible? Drop the Reproduction Steps section entirely
and say so in the description, rather than writing steps that do not
trigger it.

Something exploitable — a way to make the gate accept a forbidden rise,
or consumer-controlled input executing while it is measured — goes to a
[security advisory](https://github.com/Nitjsefnie-Actions/size-ratchet/security/advisories/new),
not the tracker.

### Claim it before you start

Comment `/claim` on an open, unassigned issue and this repository's own
[`claim` workflow](.github/workflows/claim.yml) assigns you, no write
access needed. Re-read the issue afterwards and confirm your login is in
`assignees`: a posted comment is not a claim. Release an issue you stop
working, before the merge that closes it — the action acts on open issues
only, so a stale assignment on a closed one can no longer be removed.

## One maintainer, on purpose

One person maintains this project,
[@Nitjsefnie](https://github.com/Nitjsefnie), and that is the shape it is
meant to have rather than a stage it is passing through. It is written
down here so a contributor does not have to infer it from how long a pull
request sits. [SECURITY.md](SECURITY.md) says what that means for someone
reporting a vulnerability: the response times it promises, and the
explanation of a delay, are one person's.

## Pull requests

Small and single-purpose beats large and comprehensive. One logical change
per commit, in the form the [Commit messages](#commit-messages) section
sets out.

Claim the issue before you start, then name it in the pull request's
**Related Issues and Pull Requests** section.

In the description, include what changed, why, and the actual output of
the tests you ran.

If you find a second defect while fixing the first, **file it** rather
than folding it in. A commit that fixes two things is a commit that cannot
be reverted for one of them.
