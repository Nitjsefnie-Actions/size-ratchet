#!/usr/bin/env python3
"""Refuse a commit subject whose scope names a workflow but whose type is not ci.

CONTRIBUTING.md's scope table allows a workflow's name as a scope only with
type `ci`, and names the distinction the other way round: the type
disambiguates shared names, so `ci(tests)` changes the tests workflow while
`test(harness)` changes the suite machinery the directory also holds.
Nothing checked it here until this file existed. Prose that no gate reads
is a convention, not a rule, so this check reads the same table out of the
history itself.

The workflow-name set is DERIVED at HEAD from the tracked files under
.github/workflows, not kept in a list here. A remembered list is only as
current as the last time somebody remembered to add to it, and a workflow
renamed out of the list would take its scope out of the rule in silence. The
value that joins the set is the top-level `name:` in the YAML — the thing
CONTRIBUTING tells a contributor to use — never the filename: zzz.yml naming
itself `scorecard` makes `scorecard` a workflow-name scope, and a job- or
step-level `name:` is indented and must not be collected.

The tree itself is enumerated NUL-separated (`ls-tree -z`) and every path is
decoded byte-exact, so the set reads the bytes HEAD tracks: under the
default core.quotePath a newline-separated enumeration takes git's C-quoted
display form (`".github/workflows/\303\274ber.yml"`) for the path, the
quoted entry misses the workflow-file filter, and the workflow silently
leaves the set — a false green this gate exists not to give.

Only the OUTGOING range origin/main..HEAD is examined, and the green line
says so. Already-merged history is what the check exists to stop repeating,
not what it re-tries: re-judging the two `fix(tests)` commits above would
red every fetch of main forever over commits nobody can amend.

WHAT IT CANNOT SEE, and says so rather than guessing:

  - A subject that does not parse — a merge commit, a non-conforming
    subject, or anything without a lowercase type, a non-empty scope of
    non-parenthesis characters in parens and a colon. Workflow names with
    parentheses cannot be spelled as scopes here and stay listed, not examined.
    Skips are counted and listed, never failed: guessing a scope would gate
    a parse the contributor never wrote.
  - A `name:` line this reader cannot read — quoted, anchored, a block
    scalar, a trailing comment, continued onto the next line, or missing
    entirely. Each is a refusal naming the file: an incomplete set is a
    green over a gate that was never checked, which is the same quiet
    narrowing a remembered list would drift into.
  - The type table's other half. A type outside CONTRIBUTING's table is a
    defect under any scope, so no type is enumerated here: every type but
    `ci` violates when the scope names a workflow, and the scope rule is
    what fires.
  - A scope that is both a workflow name and the subject of its own work
    would need CONTRIBUTING's sentence for it before it gets its own
    exemption here; this repository has none today.

Runs on the standard library alone, and reads blobs with
`git cat-file blob HEAD:<path>` for the same reason gate_base_freshness
reads its workflows that way: in CI the working tree and HEAD are the same
commit, and at review time the tree somebody is editing is not the tree any
gate has agreed to judge.

    tests/commit_scopes.py [--root DIR]

--root points the check at a repository other than the one above tests/,
which is how the suite rehearses it against real fixtures.
"""

import re
import subprocess
import sys
from pathlib import Path

# The branch a pull request's head is compared against. Not configurable: a
# second branch here would be a second base, and this question has one.
BASE_BRANCH = "main"

WORKFLOW_DIR = ".github/workflows"


class GateError(Exception):
    """This run could not establish the answer, and says so instead."""


def git(root, *arguments, what):
    """Run one git command in `root` and return its stdout.

    Every call goes through here because a guard that reads its own error as
    a clean tree is the false green this exists to prevent: a non-zero status
    is a refusal naming what was being attempted, never an empty answer.

    stdout and stderr decode with surrogateescape so a path git prints raw —
    `ls-tree -z` output carries non-UTF-8 filename bytes undecorated —
    survives as the string it is; subprocess re-encodes every argument with
    the filesystem encoding, so `cat-file blob HEAD:<path>` reads the same
    bytes back.
    """
    command = ("git", "-C", str(root)) + arguments
    done = subprocess.run(command, capture_output=True, text=True,
                          errors="surrogateescape")
    if done.returncode != 0:
        detail = done.stderr.strip() or "no output"
        raise GateError(
            f"cannot {what}: `{' '.join(command)}` exited {done.returncode}: {detail}")
    return done.stdout


# --- reading the workflow names ---------------------------------------------
#
# The subset of YAML these workflows use for the one key this check reads: a
# plain block mapping entry at column 0. Every other shape is refused rather
# than guessed at, because a guessed name is either a scope the rule should
# have caught or a refusal nobody asked for — and only the first of those is
# silent.

MAPPING = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_.-]*):(?:[ \t]+(?P<value>.*))?$")

# The first characters that say a scalar is NOT plain: a quoted, anchored,
# aliased, tagged, block or flow value. A plain scalar starts with none of
# them, and reading one as if it were plain is how `name: "tests"` would
# enter the set carrying its quotes.
NOT_PLAIN = re.compile(r"""["'|>&*![\]{}]""")

# In a plain scalar a space before `#` starts a comment, so the value would
# end there — a fact this reader refuses to model rather than half-models by
# stripping, because a stripped comment and a name containing a space-hash
# are indistinguishable at this line's granularity.
TRAILING_COMMENT = re.compile(r"\s#")


def indent_of(line):
    return len(line) - len(line.lstrip(" "))


def skippable(line):
    """A blank line or a whole-line comment, which carries no node."""
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def tracked_files(root):
    # -z keeps the entries NUL-separated and raw. A newline-separated read
    # hands back git's C-quoted DISPLAY form under the default
    # core.quotePath — `".github/workflows/\303\274ber.yml"` — and the
    # leading double quote misses the workflow-file filter below, so the
    # workflow would leave the name set in silence and a commit naming it
    # would pass green. Paths are decoded byte-exact (see git()), never
    # unquoted back: the raw bytes ARE the path.
    return tuple(f for f in
                 git(root, "ls-tree", "-z", "-r", "--name-only", "--full-tree", "HEAD",
                     what="list the tracked tree").split("\0") if f)


def workflow_files(files):
    return sorted(f for f in files
                  if f.startswith(WORKFLOW_DIR + "/")
                  and (f.endswith(".yml") or f.endswith(".yaml")))


def refuse_name(workflow, reason):
    raise GateError(
        f"{workflow}: {reason} The workflow-name set would be incomplete, and "
        f"an incomplete set is a green over a gate that was never checked: "
        f"spell the name as a plain top-level `name:` line, or teach this "
        f"check the shape.")


def workflow_name(workflow, text):
    """The single top-level `name:` value of one workflow.

    Only column 0 is read: a job's or a step's `name:` is indented and is
    not a workflow name, and collecting it would put e.g. `check` from a
    job's display name under the ci-type rule where CONTRIBUTING never put
    it.
    """
    lines = text.splitlines()
    tops = [index for index, line in enumerate(lines)
            if not skippable(line) and indent_of(line) == 0
            and (header := MAPPING.match(line)) is not None
            and header["key"] == "name"]
    if len(tops) == 0:
        refuse_name(
            workflow,
            "has no top-level `name:` this reader can read, so the workflow "
            "it defines is unnamed")
    if len(tops) != 1:
        raise GateError(
            f"{workflow} has {len(tops)} top-level `name:` mappings this "
            f"reader can read, and which one names the workflow cannot be "
            f"established")
    at = tops[0]
    # Captured once: a second match call on the same line reads as a fresh
    # Optional to the type checker even though the line was already matched
    # in the scan above, and a header this reader somehow cannot match has
    # no value to extract, which the valueless-name refusal below states.
    header = MAPPING.match(lines[at])
    value = ((header["value"] if header is not None else "") or "").strip()
    if not value:
        refuse_name(workflow, f"the top-level `name:` at line {at + 1} carries no value")
    if NOT_PLAIN.match(value[0]):
        refuse_name(
            workflow,
            f"the top-level `name:` at line {at + 1} is {value!r}, which is "
            f"not a plain scalar")
    if TRAILING_COMMENT.search(value):
        refuse_name(
            workflow,
            f"the top-level `name:` at line {at + 1} carries a trailing "
            f"comment, and where the name ends cannot be read at this line's "
            f"granularity")
    for line in lines[at + 1:]:
        if skippable(line):
            continue
        if indent_of(line) > 0:
            refuse_name(
                workflow,
                f"the top-level `name:` at line {at + 1} continues onto an "
                f"indented line")
        break
    return value


def workflow_name_set(root):
    """Every workflow name, derived from HEAD's workflows and nothing else."""
    files = tracked_files(root)
    if not files:
        raise GateError("HEAD tracks no files, so there are no workflows to read")
    names = set()
    for workflow in workflow_files(files):
        names.add(workflow_name(
            workflow,
            git(root, "cat-file", "blob", f"HEAD:{workflow}",
                what=f"read {workflow}")))
    if not names:
        raise GateError(
            f"no workflow under {WORKFLOW_DIR}/ carries a readable `name:`, "
            f"so the workflow-name set is empty and no subject can be judged")
    return names


# --- the subjects -----------------------------------------------------------


def fetch_base(root):
    """Bring main in as it is NOW, not as the checkout left it.

    actions/checkout fetches one commit of one ref by default, so a main that
    moved after that run left nothing here to compare against. Anonymous on
    purpose: every checkout in this repository sets persist-credentials:
    false and the repository is public.

    --unshallow deepens the ref actually being compared. Without it the
    grafted boundary hides the head's own ancestry, and a range against a
    base git cannot see into reports commits the head already carries.
    """
    arguments = ["fetch", "--no-tags", "--quiet"]
    if git(root, "rev-parse", "--is-shallow-repository",
           what="ask whether the checkout is shallow").strip() == "true":
        arguments.append("--unshallow")
    arguments += ["origin", f"+refs/heads/{BASE_BRANCH}:refs/remotes/origin/{BASE_BRANCH}"]
    git(root, *arguments, what=f"fetch origin/{BASE_BRANCH}")


# A Conventional-Commits-shaped subject: lowercase type, optional breaking
# markers after type and/or scope, and a non-empty scope without parentheses,
# followed by a colon. Capture whitespace verbatim for exact name membership.
# Types are not enumerated here; the rule below checks workflow-name scopes.
SUBJECT = re.compile(
    r"^(?P<type>[a-z]+)!?\((?P<scope>[^()]+)\)!?:\s?(?P<summary>\S.*)?$")


def outgoing_commits(root, base):
    """[(sha, subject)] for exactly the commits this head is asking main to take."""
    listing = git(root, "log", "--format=%H%x09%s", f"{base}..HEAD",
                  what="list the outgoing commits")
    commits = []
    for line in listing.split("\n"):
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        commits.append((sha.strip(), subject.strip()))
    return commits


def check(root):
    fetch_base(root)
    base = git(root, "rev-parse", "--verify",
               f"refs/remotes/origin/{BASE_BRANCH}^{{commit}}",
               what=f"resolve origin/{BASE_BRANCH}").strip()
    names = workflow_name_set(root)
    commits = outgoing_commits(root, base)

    examined = 0
    unexamined = []
    violations = []
    for sha, subject in commits:
        match = SUBJECT.match(subject)
        if match is None:
            # Not a failure — a merge commit or a non-conforming subject has
            # no scope to read, and inventing one would gate a contributor on
            # a parse they never wrote. Counted and listed, so a green never
            # rides silently over what it skipped.
            unexamined.append((sha, subject))
            continue
        examined += 1
        scope, commit_type = match["scope"], match["type"]
        # CONTRIBUTING: a workflow's name is a scope only with type `ci`.
        # A scope that is both a workflow name and a subject of its own work
        # would need CONTRIBUTING's sentence for it before the type rule
        # gains an exemption for it here; none exists today.
        if scope in names and commit_type != "ci":
            violations.append((sha, subject, scope, commit_type))

    if violations:
        plural = "s" if len(violations) != 1 else ""
        verb = "pair" if len(violations) != 1 else "pairs"
        print(f"{len(violations)} commit{plural} in origin/main..HEAD {verb} a "
              f"workflow-name scope with a type other than `ci`:")
        for sha, subject, scope, commit_type in violations:
            print(f"  {sha} {subject}")
            print(f"    scope `{scope}` is the name of a workflow under "
                  f"{WORKFLOW_DIR}/; type `{commit_type}` is not `ci`.")
        print("A workflow's name is a scope only with the `ci` type: use the "
              "`ci` type on that scope, or take a scope that names what the "
              "commit is actually about.")
        return 1

    plural = "s" if examined != 1 else ""
    print(f"Examined {examined} commit subject{plural} in origin/main..HEAD; "
          f"{len(unexamined)} not examined.")
    # The green line states its own reach. Only the outgoing range is judged:
    # already-merged history is never re-judged, and a subject without a
    # readable scope is listed rather than failed. A green line that claimed
    # more than this would be the same over-claim as a report over its
    # evidence.
    print("  Only the OUTGOING range is examined: history already merged into")
    print("  main is never re-judged, and a subject that does not parse")
    print("  (a merge commit, a non-conforming subject) is listed below and is")
    print("  never a failure:")
    for sha, subject in unexamined:
        print(f"    {sha} {subject}")
    print("No commit pairs a workflow-name scope with a type other than `ci`.")
    return 0


def main(argv):
    root = Path(__file__).resolve().parent.parent
    rest = list(argv[1:])
    while rest:
        argument = rest.pop(0)
        if argument == "--root":
            if not rest:
                print("usage: commit_scopes.py [--root DIR]", file=sys.stderr)
                return 2
            root = Path(rest.pop(0))
        else:
            print(f"unknown argument: {argument}", file=sys.stderr)
            print("usage: commit_scopes.py [--root DIR]", file=sys.stderr)
            return 2
    try:
        return check(root)
    except GateError as refusal:
        print(f"commit scopes: {refusal}", file=sys.stderr)
        return 1
    except OSError as failure:
        # git could not be run at all — absent from the runner image, or an
        # argument list past what the kernel will exec. Named rather than
        # traced: the exit status is the same either way, and a refusal is
        # what this run owes the reader.
        print(f"commit scopes: cannot run git: {failure}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
