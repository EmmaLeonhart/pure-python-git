# pure-python-git

A git implementation in pure Python, standard library only, that reads and
writes real git repositories. Repositories made by this tool are read
correctly by `git`, and the other way round; output that git defines (object
ids, `cat-file`, `ls-tree`, `diff`) matches it byte for byte.

> Started with [cleanvibe](https://github.com/EmmaLeonhart/cleanvibe) on 2026-10-06.
> The brief is in `data_lake/brief.md`; the current reading of it is in `INTENT.md`.

## Status

All five stages of the brief are implemented:

| Stage | Scope | Status |
|---|---|---|
| 1 | Object store: blobs, trees, commits, tags, loose objects, refs, HEAD; `hash-object`, `cat-file`, `ls-tree`, `rev-parse` | done |
| 2 | Index v2; `add`, `rm`, `status`, `commit`, `log`; `.gitignore` | done |
| 3 | Branches, tags, `checkout`/`switch`, Myers `diff` | done |
| 4 | Merge: merge bases, fast-forward, three-way merge, conflicts, `merge --abort` | done |
| 5 | Packfiles (with deltas), `gc`, local `clone`/`fetch`/`push` | done |

## Usage

```
python -m pygit <command> [args]
```

Requires Python 3.9 or later and nothing else.

## Tests

The tests use the standard library's `unittest` and compare against the real
`git` binary, which must be on `PATH`:

```
python -m unittest discover -s tests -v
```

Most tests run a command with both tools and require identical stdout,
stderr and exit code. Commands that change history run in twin repositories
given identical setup; the resulting commit ids, index, files and status
must match too. The diff and merge engines are also checked against
`git diff --no-index` and `git merge-file` on random inputs, and packs each
tool writes are verified and indexed by the other.

## Commands

- Stage 1: `init`, `hash-object`, `cat-file`, `ls-tree`, `rev-parse`, and
  the plumbing used to build history without the index: `mktree`,
  `commit-tree`, `update-ref`, `symbolic-ref`.
- Stage 2: `add`, `rm`, `status` (long, `-s`, `--porcelain[ -z]`, `-b`,
  `-u`, `--ignored`), `commit` (`-m`, `-F`, `-a`, `--amend`,
  `--allow-empty`, `--author`, `--date`, `--cleanup`), `log` (medium,
  oneline, short, full, fuller, raw, `--format` placeholders, `-n`,
  `--reverse`, `--first-parent`, ranges `a..b`, `a...b`, `^a`, `-- paths`,
  and per-commit diffs: `-p`, `--stat`, `--numstat`, `--shortstat`,
  `--name-only`, `--name-status`, `--summary`), `show` (commits, tags,
  trees, blobs), plus `ls-files`, `write-tree`, `read-tree`, `update-index`,
  `check-ignore`, `check-attr`.
- Stage 3: `diff` (work tree, `--cached`, commits, `a..b`, `a...b`;
  patches, `--stat`, `--numstat`, `--shortstat`, `--name-only`,
  `--name-status`, `-U<n>`, `--exit-code`, `--quiet`, `-z`), `branch`
  (list, `-v`/`-vv`, create, `-d`/`-D`, `-m`, `-c`, upstreams), `tag`
  (lightweight, annotated, `-d`, `-f`, `-l`, `-n`), `checkout` and
  `switch` (branches, `-b`/`-B`/`-c`, detached HEAD, paths), `reset`
  (`--soft`, `--mixed`, `--hard`, paths), `restore` (`--source`,
  `--staged`, `--worktree`).
- Stage 4: `merge-base` (`--all`, `--is-ancestor`), `merge-file`
  (`-p`, `--diff3`, `--ours`/`--theirs`/`--union`, `-L`), `merge`
  (fast-forward, `--ff-only`, `--no-ff`, `--no-commit`, `-m`, merge
  commits, conflicts, `--abort`, `--continue`), `cherry-pick` (`-x`, `-n`,
  several commits, `--continue`/`--skip`/`--abort`/`--quit`), `stash`
  (push/save, list, show, apply, pop, drop, clear), `rebase` (`--onto`,
  `--continue`/`--skip`/`--abort`/`--quit`), with
  `status`, `diff` and `commit` aware of merges and picks in progress.
- Stage 5: `verify-pack`, `index-pack`, `pack-objects`, `count-objects`,
  `pack-refs`, `prune`, `gc`, `clone` (`--bare`, `-b`, `-o`), `fetch`
  (`--prune`, explicit refs), `push` (refspecs, `--force`/`+`, `--delete`,
  `--tags`, `-u`), `pull` (fetch and merge, with git's divergent-branch
  refusal), `remote` (list, `-v`, `add`, `remove`, `get-url`).

## Design

The package is layered so each stage builds on the one below:

- `pygit/objects.py`: the object store (`ObjectStore`): SHA-1 ids, loose
  objects written through a temp file and renamed into place, read-only like
  git's; parsers and serializers for trees, commits and tags. Commit and tag
  headers keep unknown fields (such as `gpgsig`) in order, so a parsed
  object serializes back to the same bytes. Packed objects are read
  through `pygit/pack.py`.
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
- `pygit/attributes.py`, `pygit/convert.py`: `.gitattributes` (git's
  precedence, macros) and the line-ending part of git's `convert.c`
  (text detection for `auto`, the "index already has CR" rule, safecrlf
  warnings, the native CRLF of Windows builds); applied wherever a
  work-tree file is hashed or written.
- `pygit/worktree.py`, `pygit/status.py`: work tree scanning, stat-based
  change detection with git's racy-clean check, untracked directory
  collapsing.
- `pygit/xdiff.py`: a port of git's xdiff (preprocessing, Myers with git's
  cost heuristics, change compaction with the indent heuristic, hunk
  emission). It produces the same hunks as git, not only a minimal diff.
- `pygit/diffcore.py`, `pygit/treediff.py`: tree comparison and rename
  detection (a port of git's diffcore-rename and diffcore-delta: exact
  renames, unique-basename matches, then the scored candidate matrix;
  pairs and similarity scores match git on random inputs); the diffstat
  summary `commit` prints.
- `pygit/diffout.py`: patch headers, `--stat` layout (git's width and
  scaling rules), numstat and name-status output.
- `pygit/combined.py`: a port of git's `combine-diff.c` (`diff --cc`):
  per-parent diffs, lost lines coalesced by LCS, dense hunk selection;
  used by `diff` during a conflicted merge and by `show` of merges.
- `pygit/checkout.py`: moving the index and work tree between trees with
  git's two-way rules (local changes carry over when a path is the same
  in both trees; a checkout that would lose a change or overwrite an
  untracked file is refused before anything is touched).
- `pygit/xmerge.py`: a port of git's `xmerge.c` (three-way file merge with
  conflict refinement and git's marker format); identical to
  `git merge-file` on random inputs in both conflict styles.
- `pygit/merge_ort.py`: the tree merge, producing the results and
  messages of git's `ort` strategy for the common cases (content and
  add/add conflicts, modify/delete, renames on either side with the
  content merged at the new path, rename/delete, rename/rename, mode
  changes, binary files, file/directory clashes), and recursive virtual
  merge bases for criss-cross histories.
- `pygit/pack.py`: pack reading (v1/v2 indexes, OFS and REF deltas, a
  cache of resolved bases) and writing (delta search over a window of 10
  candidates sorted by type, name hash and size, chains up to depth 50,
  16-byte block matching; v2 indexes, 64-bit offsets when needed).
- `pygit/transport.py`: the local "wire": the other repository is opened
  directly, refs are read, and the objects the receiver lacks (everything
  reachable from the wanted tips but not from the receiver's refs) are
  written into the receiver as one new pack.
- `pygit/history.py`, `pygit/pretty.py`: history walks in git's order
  (committer date, with path-limited simplification of merges) and commit
  formatting.
- `pygit/commands/`: the command-line layer. Output is written as bytes so
  it matches git on every platform (no `\r\n` on Windows).

Where git's exact output depends on an algorithm (xdiff, xmerge,
wildmatch), the code is a line-by-line port of git's C source rather than
an approximation; random tests compare the results with git.

## Limits

- SHA-1 repositories only; the SHA-256 object format is not supported.
- Of the content conversions, only line endings are done (`core.autocrlf`,
  `core.eol`, `core.safecrlf`, and the `text`/`eol`/`binary`/`crlf`
  attributes); `filter` drivers, `ident`, `working-tree-encoding` and
  `diff`/`merge` attribute drivers are not.
- Config `[include]` sections are not followed.
- No editor, hooks, signing, pager or colour anywhere: `commit`, `tag -a`
  and `merge` need their message on the command line or in a file.
- `commit` takes no pathspecs.
- `log` has no `--graph`, decorations, relative dates or `--follow`; path
  limiting implements git's default simplification only.
- Index extensions (`TREE`, `UNTR`, split index, sparse checkout) are not
  written.
- `diff` has no `--color`, word diff, `--relative`, `-M<n>` thresholds,
  copy detection or userdiff drivers (function context uses git's default
  rule only). Combined diffs are always dense (`--cc`); there is no
  `--combined`/`-m`/`--diff-merges` choice, and `log` shows no diffs for
  merges.
- `checkout`/`switch` have no `--merge`, `--conflict`, `--patch` or
  `--overlay` options; `rebase` is non-interactive only (no `-i`,
  `--autosquash`, `--exec`), does not drop commits whose patch is already
  upstream, and never opens an editor; `stash` has no
  `-u`/`--include-untracked`, `--keep-index`, `--patch` or `branch`; and
  `cherry-pick` has no `-m` (picking merges), `--edit` or `revert`.
- `merge` merges one branch at a time (no octopus) with the ort strategy
  only and no `-X` options. Unusual conflicts (distinct types,
  rename/rename(2to1), directory renames) are reported but not resolved
  the way ort resolves them.
- Packs pygit writes are valid but not byte-identical to git's (git's
  delta search, object order heuristics and compression differ); no
  `.rev`, bitmap or multi-pack index files are written; thin packs are not
  accepted by `index-pack`.
- `gc` always repacks everything into one pack; there is no
  `gc --auto`, cruft packs, or reflog expiry.
- Transport works between repositories on the local disk only (paths, not
  `ssh://`, `https://` or `git://` URLs); no shallow or partial clones,
  submodules, `--mirror`, or `pull --rebase`.

## Working on it

Run `cleanvibe` in this folder (or double-click `!runClaude.bat` on Windows) to
open a new Claude session here. Earlier sessions are in `sessions/`.
