# size-ratchet

Gate the line count of a repository's files with a per-file ratchet that
only tightens. Ceilings have per-type defaults, are configurable through
`with:`, and take per-folder overrides in the same `with:` block; records
live in one committed JSON file and are never raised.

The measurement and ratchet are ported from
[Nitjsefnie/claudit](https://github.com/Nitjsefnie/claudit)'s
`scripts/ci/size_baseline.py`, reshaped from a per-repository allowlist
into the type-and-folder policy below.

## Install

Save this workflow as `.github/workflows/size-baseline.yml`. Its `push:`
filter names both conventional default branches, `main` and `master`,
and its steps resolve the actual default branch at run time, so a
repository whose default branch is either edits nothing outside the
`with:` block. A default branch with any other name is added to that
`branches:` list; without it, pull requests are still checked but the
default branch gets no standalone verification run. The action reference is consumed by commit SHA — a
tag is a moving pointer — so pin the release's commit and keep the
`# vX.Y.Z` comment beside it, as the block below does for the latest
release. The file may also be one a consumer's own workflow already calls
as a reusable workflow: the `workflow_call` trigger below makes that
adoption an overwrite of that file too.

```yaml
name: size baseline
on:
  pull_request:
  push:
    branches: [main, master]
  workflow_call:
permissions:
  contents: read
concurrency:
  group: ${{ github.workflow == 'size baseline' && format('size-baseline-{0}', github.ref) || format('size-baseline-called-{0}', github.run_id) }}
  cancel-in-progress: true
jobs:
  size-baseline:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - if: github.event_name == 'pull_request' || (github.event_name == 'push' && github.ref_type == 'branch' && github.ref_name == github.event.repository.default_branch)
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.head.sha || github.sha }}
          fetch-depth: 0
          persist-credentials: false
      - if: github.event_name == 'pull_request' || (github.event_name == 'push' && github.ref_type == 'branch' && github.ref_name == github.event.repository.default_branch)
        uses: Nitjsefnie-Actions/size-ratchet@6d99c15370c8d87c149a62fa043df6db6588b3d6 # v2.0.3
        with:
          ceilings: ''
          overrides: ''
          exclude: ''
          unclassified: 'ignore'
          records: '.github/size-baseline.json'
          member: ''
          mode: 'auto'
          base-ref: ${{ github.event.pull_request.base.sha }}
          stale: 'warn'
          require-tight: 'true'
```

The `with:` block shows **every input with its default**. Omit it and a
bare `uses:` step behaves the same on these events. The gate's steps run
only on a pull request or a push to the repository's default branch,
resolved from the event at run time; on any other event the block runs
on — a push to `main` or `master` where that is not the default branch,
a called run whose caller event is neither — the job completes green
without measuring. A push to any other branch, or a tag, starts no run
of the block, so a pull request's head commit carries only the pull
request's run and a reader taking the newest run on that commit reads
the check. The checkout selects
the PR's head tree; full history supplies the PR base without retaining a
token. The action needs a Git checkout, bash and Python 3.15, which it
sets up itself. It runs its runner under `python3 -I`: the standard
library and the action's own modules only, so nothing from the consumer's
tree is ever imported or executed, and there is no dependency to install
or pin.

`auto` checks a pull request against the base revision's records, and on
a push to the repository's actual default branch it verifies the records
are exactly what tighten would write over that tree — every entry the
default branch holds loose is named. Under `require-tight: 'false'` a
pull request need not tighten the entries it loosens, so that run names
each loose entry as a notice and passes. Other events, including pushes to
another branch that reach the action without the steps' `if`, fail
closed and name the event. A `mode: check` forced by editing the block
must supply `base-ref` when the event has no PR base SHA.

A consumer whose aggregate workflow calls
`.github/workflows/size-baseline.yml` with `uses:` adopts by saving the
block over that file: no other workflow edit and no ruleset change, and
the size leg stays inside the required aggregate. A called run sees the
caller's event: on the caller's `pull_request` it checks the pull
request, on a caller push to the default branch it verifies the
records, and on any other caller event the job completes green without
measuring, so a fold that requires the calling job reports. A consumer
that calls the block also gets the block's own standalone run on a pull
request and on a push to `main` or `master`; both run the same check on
the same tree. The `push:` filter binds only that standalone run: a
called run follows the caller's triggers, so a caller push to a default
branch of any name verifies the records. A standalone run
shares its group with its own ref's earlier run and replaces a
superseded one. A called run takes the calling run's group
(`github.run_id` is the caller's run id), so no other run of the block,
in either arm, shares it unless one caller run invokes the block more
than once — those invocations share a group and can cancel or queue
each other. A caller workflow named `size baseline`, in any letter
case, would also share the standalone group — name it anything else.

Records are tightened in pull requests, never delivered by a job:
`require-tight` is on by default, so a pull request that shrinks, deletes
or excludes a recorded file — or graduates one by shrinking it below its
ceiling — fails until that entry in the same pull request says what
tighten would write — lowered, or dropped. A ceiling the pull request
raises without moving a count is the one shape that stays a stale notice,
named by the default branch's run until the next touching pull request
drops the entry. The default branch holds no job that commits records.
For
initial adoption, run tighten on the proposed head and commit the seed
in the adoption PR (see Seeding).

Two pull requests checked against the same base can both merge and leave
a record above its file's count — each lowered the record to its own
head's count, and the merged tree carries both deletions. The
default-branch run reports every such entry, and a pull request that
changes neither the file nor its record still passes; the next pull
request that touches either must make that entry exact.

A consumer that today delivers size records from its own push job deletes
exactly that: the size arm of its delivery workflow — the tighten step,
the artifact staging and the commit — and the tests of that delivery.
Records files a delivery also lands for other gates keep their arms, and
the delivery workflow itself stays when they do. No credential, ruleset,
environment or extra workflow is added: the block above is the whole
adoption.

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `ceilings` | `''` | Multiline `<type>: <ceiling>` lines replacing a type's default. |
| `overrides` | `''` | Multiline `<folder>/: <ceiling>` lines; the longest matching repository-relative folder prefix wins. |
| `exclude` | `''` | Multiline exact paths or `<folder>/` prefixes removed from measurement. |
| `unclassified` | `'ignore'` | `fail` fails a measurable file with no ceiling, no record and no exclusion. |
| `records` | `'.github/size-baseline.json'` | Repository-relative records file. |
| `member` | `''` | Top-level key of the records file holding the baseline map, instead of `baseline`. |
| `mode` | `'auto'` | Event dispatch above, or exactly `check` / `tighten` to force a mode. |
| `base-ref` | `${{ github.event.pull_request.base.sha }}` | Git revision whose records bind a check. Required for check, unused by tighten and by the default-branch verification. |
| `stale` | `'warn'` | `warn` reports a stale record as a notice; `fail` fails the run on one. |
| `require-tight` | `'true'` | Fails a check whose touched entries differ from what tighten would write, naming each; entries the pull request leaves untouched stay a stale notice. `'false'` fails neither, and the default-branch run reports each loose entry as a notice. |

Invalid inputs fail the run with exit 2 and name the input and the line: a
malformed line, an unknown type, a folder without a trailing `/`, a
duplicate folder, or a ceiling that is not a positive integer. Exit 0
passes, 1 is a violation, 2 is a configuration or environment failure.
Inputs reach the action through environment variables and are never
interpolated into shell programs.

**Inputs changed in `with:` are a workflow edit in the PR diff** — they are
reviewed like any workflow change, and the action reads them from the run's
own inputs without comparing them with the base revision's workflow.

## Types and ceilings

Each type has a default ceiling and is chosen by extension. A file with
none of these extensions has no type.

| Type | Extensions | Default ceiling |
| --- | --- | --- |
| `source` | `.py`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.sh`, `.bash` | 500 |
| `stylesheet` | `.css`, `.scss` | 500 |
| `sql` | `.sql` | 500 |
| `yaml` | `.yml`, `.yaml` | 500 |

A typed file's ceiling is the **first that applies**:

1. `overrides` — a `<folder>/` prefix match, longest prefix wins. An
   override never gives a ceiling to an untyped file.
2. `ceilings` — that type's configured ceiling.
3. the type's built-in default.

## Measurement rules

Every regular file (mode 100644 or 100755) in `git ls-tree -r` of the
measured revision that has a ceiling, or has a record, is measured in
lines: the number of `\n` bytes, plus one when a non-empty file does not
end in `\n`. Symlinks, submodules and files git reports as binary (a NUL
byte in the first 8000 bytes, git's own rule) are never measured and are
never unclassified. A file that cannot be read fails the run, naming it.
Check measures the checkout's `HEAD`; the install block above checks out
the PR head SHA with `fetch-depth: 0`.

The records file itself is never measured or unclassified: it is the
gate's own data, not source.

## Ratchet and records

`.github/size-baseline.json` holds `{"schema_version": 1, "baseline":
{"<path>": <lines>}}`. With `member` set, the baseline map is instead the
value of that top-level key, and every other key of the document is
preserved byte-for-byte on write. All comparisons use only the baseline
map. "No records" means the file is absent or the key is absent; an empty
map is records. Ceilings live only in `with:`.

- A file **with** a record may not grow above its record, whether or not it
  has a ceiling.
- A file **without** a record may not exceed its ceiling.
- A recorded number is never raised and no entry is ever added by hand:
  growth is answered by relocating code into a new file, or by shrinking
  the file.

**Check** compares the head tree against the baseline read at `base-ref`,
so a records edit in the head does not loosen the gate. With
`require-tight` on it measures the base tree as well, so the base's
counts bind the comparison. Each failure names
the path, its measured lines, and the record or ceiling it exceeded. A
recorded entry whose file shrank, was deleted, was excluded, or is at or
below its ceiling is **stale**: `stale: warn` reports it as a notice;
`stale: fail` fails the run. `require-tight` (on by default) also fails
when an entry the pull request changes — its file's count moved between
the base and head trees, or its record moved between the base and head
maps — differs from what tighten would write, naming each entry and what
it should become: a record to lower prints `required N` against the
recorded value, and a record to drop prints `recorded M` alone. Entries
the pull request leaves untouched
stay with the stale report: a loose record already on the base never
fails unrelated work. An entry the head adds for an unrecorded
over-ceiling file must be that file's exact head count — the same
exactness the seed fallback demands (see Seeding); any other number, and
any entry added for a file at or below its ceiling, is refused.

**Tighten** lowers each record to the file's current count. It drops
entries for deleted, excluded and graduated (at or below the ceiling)
files, and keeps an entry whose file has no ceiling, lowered to its count.
It never raises or adds an entry except when seeding. A run with nothing to
change writes nothing and passes. A job never commits the result: pull
requests carry tightened records (`require-tight`), and the default
branch's own run verifies them. Force `tighten` to seed, or to write the
records file for a deliberate commit.

## Seeding

With no records, tighten records every file over its ceiling at its current
count. A check whose `base-ref` has no records uses the head's: the head's
baseline map must equal exactly what a seeding tighten over the head tree
writes, and a failure names each differing entry. If neither revision has
records, check fails and says to run tighten to seed. Once the base has
records, only the base's bind. A base whose records map is empty is the
same for additions: an entry the head adds for an over-ceiling file must
be the file's exact head count.

Three consequences for an adopting PR:

- **Every later commit in the adopting PR that changes a recorded file's
  count needs the seed regenerated.** The seed binds exactly: a commit
  that adds ten lines to a seeded file fails the seed-fallback comparison
  until tighten runs again over the new head.
- **The widening limit.** After seeding, a change that brings an
  over-ceiling file under measurement — a lowered ceiling, a removed
  exclusion, a new type — fails check for that file until it is excluded
  or shrunk. **A release that adds a type is a major version** for exactly
  this reason.
- An adopter that already keeps another size baseline seeds under a new
  `member` or the default file and deletes the old one in the same PR.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the regression suite and the
records-update procedure, and [SECURITY.md](SECURITY.md) for private
vulnerability reporting. Licensed under the [MIT License](LICENSE).
