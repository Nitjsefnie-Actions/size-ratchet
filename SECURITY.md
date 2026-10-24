# Security

Report vulnerabilities privately through a
[security advisory on this repository](https://github.com/Nitjsefnie-Actions/size-ratchet/security/advisories/new),
not the public issue tracker. Include the action commit SHA, calling
workflow, inputs, the measured file or records document, and observed
behavior needed to reproduce the problem.

In scope are the action, its measurement of consumer-controlled trees and
inputs, its records reading and writing, and the repository's workflows,
particularly a check that accepts a rise the ratchet forbids. Inputs must
remain environment data; consumer code must never execute while it is
being measured, and the runner must import nothing from the consumer's
tree.

The highest-value reports show code execution from a measured tree, or a
gate accepting a forbidden rise — a grown file passing, a record raised, a
seed accepted loose. Reproduce only in a repository you control.

## What happens after you report

A report is acknowledged within three working days. That acknowledgement
is a person confirming they have read it, not a verdict — the assessment
follows.

You will get an assessment within ten working days: whether it reproduces,
what it affects, and if it is accepted, a rough idea of when a fix will
land. If it takes longer than that, you will be told why rather than left
waiting.

Those two clocks are one person's working days. As of 2026-10-24 this
project had no fallback maintainer — [CONTRIBUTING.md](CONTRIBUTING.md)
records what that means — so the promises above are conditional on this
person being available to work on a report. A promise kept is kept as
written, and a slow answer can reflect that person's availability rather
than anything about the report. That availability is also what carries the
explanation: when it runs out the reason runs out with it, so a report can
pass ten working days with neither an assessment nor the account of why
there is none, and nobody else will answer — this file refuses the public
tracker, and there is no second maintainer to ask.

Disclosure is coordinated with you. A fix is released before any public
description, and the advisory credits you unless you ask otherwise. Ninety
days after the report is the point at which disclosure happens regardless,
so a report cannot be buried by being left unanswered. That disclosure is
carried by the same one person the clocks above are. Ninety days remains
the target, and if that person is unavailable when the ninety days fall
due, the advisory publishes when they return — the commitment is scoped to
the same availability, not withdrawn. What a reporter can rely on if the
day falls due with the person still away is that this file never asks a
report to be held past ninety days: the choice to disclose is the
reporter's own at that point.

If a report is declined, you will be told the reason. Disagreeing with
that is reasonable, and saying so is welcome.
