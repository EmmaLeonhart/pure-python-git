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
| 1 | Object store: blobs, trees, commits, tags, loose objects, refs, HEAD; `hash-object`, `cat-file`, `ls-tree`, `rev-parse` | in progress |
| 2 | Index v2; `add`, `rm`, `status`, `commit`, `log`; `.gitignore` | planned |
| 3 | Branches, tags, `checkout`/`switch`, Myers `diff` | planned |
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

## Design and limits

Filled in as each stage lands.

## Working on it

Run `cleanvibe` in this folder (or double-click `!runClaude.bat` on Windows) to
open a new Claude session here. Earlier sessions are in `sessions/`.
