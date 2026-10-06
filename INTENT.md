# What this project is for

_Maintained by Claude: a running read of what the user is trying to do. It is
analysis, not a transcript, and it changes as understanding improves._

Work mode started: 2026-10-06 02:09 PST (thirty-minute intake verdict: WORK MODE).

## Current understanding

Build a git implementation in pure Python (standard library only) that reads
and writes real git repositories, in five stages, as laid out in
`data_lake/brief.md`:

1. Object store: blobs, trees, commits, tags, loose objects (zlib), refs and
   HEAD; `hash-object`, `cat-file`, `ls-tree`, `rev-parse`.
2. Index (binary format v2); `add`, `rm`, `status`, `commit`, `log`;
   `.gitignore` rules.
3. Branches and tags, `checkout`/`switch`, `diff` (Myers, unified output)
   between work tree, index and commits.
4. Merging: merge bases, fast-forward, three-way merge of trees and file
   contents with conflict markers, `merge --abort`.
5. Packfiles: read/write packs and indexes (with deltas), `gc`, and
   `clone`/`fetch`/`push` between repositories on local disk.

Every stage is tested against the real `git` binary (interop both ways;
byte-for-byte matching for git-defined output). The README documents the
design and its limits. The brief says it is a long project: keep going after
each stage, and adjust the plan (saying why) if a stage turns out different.

## What supports it

- `data_lake/brief.md`, the only material in the folder; it is a full spec.
- The user has said nothing in chat (0 messages at intake).
- The folder name is generated and carries no meaning.

## Assumptions

- Package name `pygit` (import `pygit`, CLI `python -m pygit <cmd>`), so it
  never shadows the real `git` used by the tests.
- Tests use `unittest` from the standard library (the brief says standard
  library only; pytest can still run them). Tests require `git` on PATH.
- SHA-1 repositories only (git's default); SHA-256 object format is out of
  scope unless the user asks.
- Python 3.9+.

## Constraints from the user

- Standard library only.
- (None given in chat yet.)

## Blocked

- **CI: BLOCKED-ON-USER-ACTION.** GitHub Actions jobs are not started on
  this account ("recent account payments have failed or your spending limit
  needs to be increased", run 37442191253, 2026-10-06). Unblock: fix billing
  or the spending limit in GitHub's Billing & plans settings. Until then the
  suite is run locally on Windows (Python 3.13 and 3.11) before each push;
  Linux/macOS and Python 3.9 are untested.

## Progress

- 2026-10-06 03:49 PST: all five stages of the brief are implemented and
  tested against git (91 tests). The brief's "keep going" is read as:
  close the limits documented in README.md, most output-affecting first
  (`todo.md`). This is an assumption; the user may prefer to stop here or
  steer elsewhere.

## Open questions

- None blocking. Exact CLI coverage per command (which flags) is decided as
  each stage goes, aiming at the commonly used ones.

## Confidence

High on the goal: the brief is explicit.
