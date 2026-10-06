# pure-python-git

A git implementation in pure Python, standard library only, that reads and
writes real git repositories. Repositories made by this tool are read
correctly by `git`, and the other way round; output that git defines (object
ids, `cat-file`, `ls-tree`, `diff`) matches it byte for byte.

> Started with [cleanvibe](https://github.com/EmmaLeonhart/cleanvibe) on 2026-10-06.
> The brief is in `data_lake/brief.md`; the current reading of it is in `INTENT.md`.

## Status

Work in progress, built in five stages:

| Stage | Scope | Status |
|---|---|---|
| 1 | Object store: blobs, trees, commits, tags, loose objects, refs, HEAD; `hash-object`, `cat-file`, `ls-tree`, `rev-parse` | done |
| 2 | Index v2; `add`, `rm`, `status`, `commit`, `log`; `.gitignore` | done |
| 3 | Branches, tags, `checkout`/`switch`, Myers `diff` | next (the diff engine is already done) |
| 4 | Merge: merge bases, fast-forward, three-way merge, conflicts, `merge --abort` | planned |
| 5 | Packfiles (with deltas), `gc`, local `clone`/`fetch`/`push` | planned |

## Usage

```
python -m pygit <command> [args]
```

## Tests

The tests use the standard library's `unittest` and compare against the real
`git` binary, which must be on `PATH`:

```
python -m unittest discover -s tests -v
```

Most tests run a command with both tools and require identical stdout,
stderr and exit code. Commands that change history run in twin repositories
given identical setup, and the resulting commit ids must match too. The diff
engine is also checked against `git diff --no-index` on random inputs.

## Commands so far

- Stage 1: `init`, `hash-object`, `cat-file`, `ls-tree`, `rev-parse`, and
  the plumbing used to build history without the index: `mktree`,
  `commit-tree`, `update-ref`, `symbolic-ref`.
- Stage 2: `add`, `rm`, `status` (long, `-s`, `--porcelain[ -z]`, `-b`,
  `-u`, `--ignored`), `commit` (`-m`, `-F`, `-a`, `--amend`,
  `--allow-empty`, `--author`, `--date`, `--cleanup`), `log` (medium,
  oneline, short, full, fuller, raw, `--format` placeholders, `-n`,
  `--reverse`, `--first-parent`, ranges `a..b`, `a...b`, `^a`, `-- paths`),
  plus `ls-files`, `write-tree`, `read-tree`, `update-index`,
  `check-ignore`.

## Design and limits

The package is layered so each stage builds on the one below:

- `pygit/objects.py`: the object store (`ObjectStore`): SHA-1 ids, loose
  objects written through a temp file and renamed into place, read-only like
  git's; parsers and serializers for trees, commits and tags. Commit and tag
  headers keep unknown fields (such as `gpgsig`) in order, so a parsed
  object serializes back to the same bytes.
- `pygit/refs.py`: loose refs, `packed-refs`, symbolic refs, ref updates
  through `.lock` files, reflogs when `core.logAllRefUpdates` is on.
- `pygit/revparse.py`: revision expressions (`HEAD~2^{tree}:path`, short ids,
  ref name lookup in git's order).
- `pygit/config.py`: the config file format, with global/repository
  precedence.
- `pygit/index.py`: the index file (reads versions 2 to 4, writes 2; the
  optional extensions are dropped on write, and git rebuilds them).
- `pygit/ignore.py`: a port of git's `wildmatch.c` and the `.gitignore`
  precedence rules (per-directory files, `info/exclude`,
  `core.excludesFile`; an excluded directory hides everything in it).
- `pygit/worktree.py`, `pygit/status.py`: work tree scanning, stat-based
  change detection with git's racy-clean check, untracked directory
  collapsing.
- `pygit/xdiff.py`: a port of git's xdiff (preprocessing, Myers with git's
  cost heuristics, change compaction with the indent heuristic, hunk
  emission). It produces the same hunks as git, not only a minimal diff.
- `pygit/diffcore.py`, `pygit/treediff.py`: tree comparison and rename
  detection; the diffstat summary `commit` prints.
- `pygit/history.py`, `pygit/pretty.py`: history walks in git's order
  (committer date, with path-limited simplification of merges) and commit
  formatting.
- `pygit/commands/`: the command-line layer. Output is written as bytes so
  it matches git on every platform (no `\r\n` on Windows).

Limits so far:

- SHA-1 repositories only; the SHA-256 object format is not supported.
- Packed objects can't be read yet, so repositories git has packed (after
  `git gc` or a clone) fail until stage 5.
- `ls-tree` paths are taken from the repository root, not the current
  directory.
- Config `[include]` sections are not followed.
- Content filters (`core.autocrlf`, `.gitattributes`) are not applied;
  file bytes are stored as they are.
- `commit` never opens an editor (it needs `-m`, `-F`, `-C` or `--amend`),
  runs no hooks, does not sign, and takes no pathspecs.
- Rename similarity uses git's chunking idea but not its exact hash, so a
  pair scoring right at the threshold may be paired differently than git
  pairs it. Exact renames always match.
- `log` has no `--graph`, decorations, `-p`/`--stat`, relative dates or
  `--follow`; path limiting implements git's default simplification only.
- Index extensions (`TREE`, `UNTR`, split index, sparse checkout) are not
  written.

## Working on it

Run `cleanvibe` in this folder (or double-click `!runClaude.bat` on Windows) to
open a new Claude session here. Earlier sessions are in `sessions/`.
